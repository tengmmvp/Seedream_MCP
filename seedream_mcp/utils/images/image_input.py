"""图像输入预处理：将多种来源的图像统一归一化为 API 可接受的格式。

支持 URL、Data URI 与本地文件路径三种来源。URL 与 Data URI 经校验后原样返回；
本地文件路径在工作区边界校验通过后，经 O_NOFOLLOW 读取并编码为 Base64 Data URI。
预处理逻辑独立于 SeedreamClient：客户端专注于 API 调用，本模块可独立测试与复用。
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from ..core.errors import SeedreamAPIError, SeedreamMCPError, SeedreamValidationError
from ..core.formats import MIME_BY_EXTENSION, format_file_size_mb, infer_extension_from_bytes
from ..core.logs import get_logger
from ..io.io_file import open_no_follow_read
from ..io.io_path import (
    _is_unc_path,
    get_workspace_roots,
    is_boundary_from_session_roots,
    is_within_resolved,
    resolve_workspace_roots,
    suggest_similar_paths,
)
from .image_validation import (
    MAX_IMAGE_FILE_SIZE,
    decode_and_validate_dimensions,
    resolve_local_image_candidate,
    validate_image_input,
    validate_image_path,
)
from .image_ref import classify_image_reference

logger = get_logger(__name__)


async def prepare_image_input(image: str) -> str:
    """将单张图像输入归一化为 API 所需格式。

    - HTTP/HTTPS URL：经统一校验拒绝 userinfo 凭据等不安全形态后原样返回。
    - Data URI：经格式与维度校验后原样返回。
    - 本地文件路径：读取并编码为 Base64 Data URI 返回。

    URL 校验、Data URI 校验与本地文件读取均在工作线程中执行，避免阻塞事件循环。
    """
    try:
        normalized = image.strip()

        kind = classify_image_reference(normalized)
        if kind == "url":
            # URL 分支同样经统一校验：拒绝携带 userinfo 凭据的参考图 URL，防止凭据
            # 随请求体送往上游 API。urlparse 对请求体上限内的巨型 URL 的全量解析
            # 可造成事件循环停顿，与 data_uri 分支一致下沉工作线程执行。
            return await asyncio.to_thread(validate_image_input, normalized)

        if kind == "data_uri":
            # validate_image_input 内含 PIL 解码等同步操作，放到工作线程避免阻塞事件循环。
            return await asyncio.to_thread(validate_image_input, normalized)

        # 本地文件：路径校验、读取与编码均为同步 IO，整体放到工作线程。
        return await asyncio.to_thread(_prepare_local_image, normalized, image)
    except SeedreamMCPError:
        raise
    except Exception as e:
        raise SeedreamAPIError(f"图像处理失败: {e}") from e


def _resolves_outside_workspace(normalized: str, resolved_roots: list[Path]) -> bool:
    """判断输入路径解析后的物理位置是否落在全部工作区根之外。

    与 resolve_local_image_candidate 的候选构造一致：绝对路径原样作为候选，相对
    路径按根序拼接；任一候选 resolve 后命中任一根即视为界内。用于把「路径越界」
    从「文件缺失或无效」类失败中拆出，走带允许根列表的校验错误分支。UNC 路径不
    进入 resolve 以免触发 SMB 连接：UNC 形态的输入直接返回 False 交由诊断分支
    处理，UNC 根拼接出的相对路径候选经逐候选守卫跳过。
    """
    if _is_unc_path(normalized):
        return False
    if os.path.isabs(normalized):
        candidates = [Path(normalized)]
    else:
        candidates = [base / normalized for base in resolved_roots]
    for candidate in candidates:
        # 逐候选 UNC 守卫：根本身为 UNC 形态时相对路径候选拼接后仍以 UNC 前缀开头，
        # resolve 同样会触发 SMB 连接。守卫规则与 resolve_local_image_candidate 的
        # 逐候选前置拦截同源，均为 io_path._is_unc_path 的单一规则。
        if _is_unc_path(str(candidate)):
            continue
        try:
            resolved_candidate = candidate.resolve()
        except (OSError, ValueError):
            continue
        if any(is_within_resolved(resolved_candidate, base) for base in resolved_roots):
            return False
    return True


def _prepare_local_image(normalized: str, original: str) -> str:
    """校验本地图片路径并读取编码为 Base64 Data URI。

    候选定位委托 image_validation.resolve_local_image_candidate，与
    ImagePreparer._local_file_signature 共用同一选择规则，缓存签名与实际读取
    锁定同一文件。定位失败且路径解析后落在全部工作区根之外时，抛出携带允许根
    列表的 SeedreamValidationError，属参数校验语义而非 API 调用失败；界内定位
    失败则经 validate_image_path 做诊断性校验取具体失败原因，并给出相似路径建议。
    需在工作线程中调用。
    """
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise SeedreamAPIError("当前 MCP 会话未授权任何工作区目录，无法读取本地图片。")

    resolved_roots = resolve_workspace_roots(workspace_roots)

    found = resolve_local_image_candidate(normalized, resolved_roots)
    if found is None:
        if _resolves_outside_workspace(normalized, resolved_roots):
            # 回退边界下来自服务器环境的根路径不进入面向调用方的错误消息，
            # 与 browse_images 的回退边界遮蔽标准一致；仅会话 Roots 声明的
            # 边界回显具体路径供调用方自纠。
            if not is_boundary_from_session_roots():
                raise SeedreamValidationError(
                    "路径超出允许的工作区目录范围，仅允许服务器配置的工作区目录",
                    field="image",
                    value=normalized,
                )
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            raise SeedreamValidationError(
                f"路径超出允许的工作区目录范围，允许的根: {allowed_roots}",
                field="image",
                value=normalized,
            )
        # 诊断性校验仅用于错误文案：定位已由共享规则唯一决定，此处取各根的具体
        # 失败原因（文件不存在、格式不支持等）拼入错误消息，不影响候选一致性。
        validation_errors: list[str] = []
        for root in workspace_roots:
            _, error_msg, _ = validate_image_path(
                normalized, base_dir=str(root), skip_dimensions=True
            )
            if error_msg:
                validation_errors.append(error_msg)

        error_text = "图像路径校验失败"
        if validation_errors:
            error_text = "；".join(dict.fromkeys(validation_errors))

        suggestions = suggest_similar_paths(
            original,
            search_dirs=[str(root) for root in workspace_roots],
        )
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n建议的相似路径:\n" + "\n".join(
                f"  • {s}" for s in suggestions[:3]
            )
        raise SeedreamAPIError(f"{error_text}{suggestion_text}")

    validated_path, _ = found

    # O_NOFOLLOW 防护最终路径分量、拒绝符号链接，由 io_file 统一实现；
    # 符号链接或打开失败抛 OSError，由 prepare_image_input 外层转 SeedreamAPIError。
    # 内存特征：读取字节、b64 编码与最终 data URI 拼接的瞬时峰值约为单图的 5.5×，
    # 预处理并发 5 × 30MB 上限下瞬态约 800MB，不受 LRU 缓存字节上限约束，部署
    # 受限时须按此规划内存。
    with open_no_follow_read(validated_path) as f:
        # 限制读取量并复核，防校验与读取间文件被替换为超大文件撑爆内存。
        image_bytes = f.read(MAX_IMAGE_FILE_SIZE + 1)
    if len(image_bytes) > MAX_IMAGE_FILE_SIZE:
        raise SeedreamValidationError(
            f"文件过大: {format_file_size_mb(len(image_bytes))}，"
            f"最大支持{format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
            field="image",
            value=str(validated_path),
        )
    # 维度校验复用已读字节，维度相关异常与 image_validation 路径对齐为 SeedreamValidationError。
    # PIL 函数内惰性导入与 image_validation 模式一致：本函数运行于工作线程，首次导入的
    # 解码器加载成本不落在事件循环线程，首个带参考图的请求不阻塞其他在途请求。
    from PIL import Image

    try:
        decode_and_validate_dimensions(image_bytes, str(validated_path))
    except SeedreamValidationError:
        raise
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        # OSError 覆盖 PIL.UnidentifiedImageError：扩展名合法但内容损坏同样属参数
        # 校验语义，与 _validate_file_path 的维度解析分支对齐为 SeedreamValidationError。
        raise SeedreamValidationError(
            f"图像维度解析失败: {str(e)}",
            field="image",
            value=str(validated_path),
        ) from e
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    # MIME 以字节签名复核为准，签名不可识别时回退扩展名映射；扩展名可伪造，
    # 与 auto_save 保存路径的 infer_extension_from_bytes 保持同一口径。
    inferred_extension = infer_extension_from_bytes(image_bytes, default="")
    suffix = inferred_extension or validated_path.suffix.lower()
    mime_type = MIME_BY_EXTENSION.get(suffix, "image/jpeg")

    logger.info("成功处理图片文件: {} ({} bytes)", validated_path, len(image_bytes))
    return f"data:{mime_type};base64,{image_b64}"
