"""图像输入预处理：将多种来源的图像统一归一化为 API 可接受的格式。

支持 URL、Data URI 与本地文件路径三种来源。URL 与 Data URI 经校验后原样返回；
本地文件路径在工作区边界校验通过后，经 O_NOFOLLOW 读取并编码为 Base64 Data URI。
预处理逻辑独立于 SeedreamClient，可独立测试与复用。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from ..core.errors import SeedreamMCPError, SeedreamValidationError
from ..core.formats import MIME_BY_EXTENSION, format_file_too_large, infer_extension_from_bytes
from ..core.logs import get_logger
from ..io.io_file import open_no_follow_read
from ..io.io_path import (
    get_read_context,
    is_unc_path,
    mask_scope_paths,
    save_root_relative,
    suggest_similar_paths,
)
from .image_validation import (
    MAX_IMAGE_FILE_SIZE,
    UNIDENTIFIED_IMAGE_MESSAGE,
    decode_and_validate_dimensions,
    is_unidentified_image_error,
    iter_local_candidates,
    resolve_local_image_candidate,
    validate_image_input,
    validate_image_path,
)
from .image_ref import classify_image_reference

logger = get_logger()


async def prepare_image_input(image: str) -> str:
    """将单张图像输入归一化为 API 所需格式。

    - HTTP/HTTPS URL：经统一校验拒绝 userinfo 凭据等不安全形态后原样返回。
    - Data URI：经格式与维度校验后将 media type 归一化为小写标准 MIME 返回。
    - 本地文件路径：读取并编码为 Base64 Data URI 返回。

    URL 校验、Data URI 校验与本地文件读取均在工作线程中执行，避免阻塞事件循环。

    Args:
        image: 图像输入字符串，可为 HTTP/HTTPS URL、Data URI 或本地文件路径。

    Raises:
        SeedreamValidationError: 输入格式无效、路径越界、界内定位失败或本地文件
            读取失败等本地预处理失败；本阶段不触网，不参与 client 的 API 重试。
    """
    try:
        normalized = image.strip()

        kind = classify_image_reference(normalized)
        if kind == "url":
            # 巨型 URL 的 urlparse 全量解析有阻塞事件循环的成本，下沉工作线程执行。
            return await asyncio.to_thread(validate_image_input, normalized)

        if kind == "data_uri":
            return await asyncio.to_thread(validate_image_input, normalized)

        return await asyncio.to_thread(_prepare_local_image, normalized, image)
    except SeedreamMCPError:
        raise
    except Exception as e:
        # 兜底异常均来自本地预处理，归校验档。
        raise SeedreamValidationError(f"图像处理失败: {e}") from e


def _resolves_outside_workspace(normalized: str, save_root: Path, read_scope: list[Path]) -> bool:
    """判断输入路径解析后的物理位置是否落在读权限之外。

    候选管线与 resolve_local_image_candidate 共用 iter_local_candidates，任一
    候选命中读权限任一目录即界内。UNC 路径不 resolve 以免触发 SMB 连接，UNC
    输入直接返回 False 交诊断分支处理。
    """
    if is_unc_path(normalized):
        return False
    return not any(iter_local_candidates(normalized, save_root, read_scope))


def _format_local_read_error(exc: OSError, normalized: str) -> str:
    """构建本地文件读取失败的错误文案，不回显服务器侧绝对路径。

    异常的 str 与 filename 均可能嵌有服务器侧绝对路径，仅回显系统错误语义与
    调用方输入的原样字符串。系统错误语义取 strerror，缺失时回退 errno 数值。
    """
    if exc.strerror:
        reason = exc.strerror
    elif exc.errno is not None:
        reason = f"errno {exc.errno}"
    else:
        reason = "无法读取"
    return f"读取图像文件失败: {reason}: {normalized}"


def _prepare_local_image(normalized: str, original: str) -> str:
    """校验本地图片路径并读取编码为 Base64 Data URI。

    候选定位委托 resolve_local_image_candidate，与 ImagePreparer 的缓存签名共用
    同一选择规则，锁定同一文件。(存储区, 读权限) 经 get_read_context 在函数顶
    部单点求值，各分支共享，消除一次失败请求内的重复解析。越界抛携带配置指引
    的错误；界内定位失败经 validate_image_path 做诊断性校验，取具体失败原因并附
    存储区内的相似路径建议。各失败均属参数校验语义而非 API 调用失败，归
    SeedreamValidationError；错误文案不回显服务器侧路径。需在工作线程中调用。
    """
    _, save_root, read_scope = get_read_context()
    found = resolve_local_image_candidate(normalized, save_root=save_root, read_scope=read_scope)
    if found is None:
        if _resolves_outside_workspace(normalized, save_root, read_scope):
            raise SeedreamValidationError(
                "路径不在读取范围内；可通过客户端工作区（MCP Roots）、"
                "SEEDREAM_WORKSPACE_ROOT 或 SEEDREAM_AUTO_SAVE_BASE_DIR 授权该目录",
                field="image",
                value=normalized,
            )
        # 诊断错误消息含解析后的绝对路径，读权限成员逐项替换为占位符后回显，存储区
        # 外的工作区根路径同样遮蔽；建议限定在存储区内扫描并转为存储区相对形态，
        # 均不泄露服务器路径。
        _, error_msg, _ = validate_image_path(normalized, skip_dimensions=True)
        error_text = mask_scope_paths(error_msg or "图像路径校验失败", save_root, read_scope)
        suggestions = suggest_similar_paths(original, search_dirs=[str(save_root)])
        suggestion_text = ""
        if suggestions:
            relative_suggestions = [
                relative
                for relative in (
                    save_root_relative(suggestion, save_root) for suggestion in suggestions[:3]
                )
                if relative is not None
            ]
            if relative_suggestions:
                suggestion_text = "\n\n建议的相似路径:\n" + "\n".join(
                    f"  • {s}" for s in relative_suggestions
                )
        raise SeedreamValidationError(
            f"{error_text}{suggestion_text}", field="image", value=normalized
        )

    validated_path, _ = found

    # O_NOFOLLOW 拒绝最终分量的符号链接。
    # 内存峰值：读取字节、b64 编码与 data URI 拼接约为单图的 5.5×，并发 5 × 30MB 上限
    # 下瞬态约 800MB，不受 LRU 缓存字节上限约束。
    try:
        with open_no_follow_read(validated_path) as f:
            # 限制读取量并复核，防校验与读取间文件被替换为超大文件撑爆内存。
            image_bytes = f.read(MAX_IMAGE_FILE_SIZE + 1)
    except OSError as e:
        raise SeedreamValidationError(
            _format_local_read_error(e, normalized), field="image", value=normalized
        ) from e
    if len(image_bytes) > MAX_IMAGE_FILE_SIZE:
        # value 携带调用方输入原样串，不含服务器侧绝对路径。
        raise SeedreamValidationError(
            format_file_too_large(len(image_bytes), MAX_IMAGE_FILE_SIZE),
            field="image",
            value=normalized,
        )
    # 维度校验复用已读字节；PIL 惰性导入的解码器加载成本落在工作线程而非事件循环。
    from PIL import Image

    try:
        decode_and_validate_dimensions(image_bytes, normalized)
    except SeedreamValidationError:
        raise
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        # OSError 覆盖 PIL.UnidentifiedImageError；内容不可识别时用固定文案，避免
        # 异常原文中的 BytesIO 对象地址进入用户消息。
        message = (
            UNIDENTIFIED_IMAGE_MESSAGE
            if is_unidentified_image_error(e)
            else f"图像维度解析失败: {str(e)}"
        )
        raise SeedreamValidationError(message, field="image", value=normalized) from e
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    # MIME 以字节签名为准、扩展名回退：扩展名可伪造，与 auto_save 保存路径同口径。
    # 两来源的扩展名均为映射键，直取使键缺失以 KeyError 显式暴露而非静默回落。
    inferred_extension = infer_extension_from_bytes(image_bytes, default="")
    suffix = inferred_extension or validated_path.suffix.lower()
    mime_type = MIME_BY_EXTENSION[suffix]

    logger.info("成功处理图片文件: {} ({} bytes)", validated_path, len(image_bytes))
    return f"data:{mime_type};base64,{image_b64}"
