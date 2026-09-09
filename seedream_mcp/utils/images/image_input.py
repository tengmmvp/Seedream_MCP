"""图像输入预处理：将多种来源的图像统一归一化为 API 可接受的格式。

支持 URL、Data URI 与本地文件路径三种来源。URL 与 Data URI 经校验后原样返回；
本地文件路径在工作区边界校验通过后，经 O_NOFOLLOW 读取并编码为 Base64 Data URI。
预处理逻辑独立于 SeedreamClient，可独立测试与复用。
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from ..core.errors import SeedreamMCPError, SeedreamValidationError
from ..core.formats import MIME_BY_EXTENSION, infer_extension_from_bytes
from ..core.logs import get_logger
from ..io.io_path import (
    get_read_context,
    is_unc_path,
    is_within_resolved,
    normalize_path,
    read_scope_denial_message,
    suggest_similar_paths,
)
from .image_validation import (
    LocalImageCandidate,
    iter_local_candidates,
    read_and_decode_local_image,
    resolve_local_image_candidate,
    validate_image_input,
    validate_image_path,
)
from .image_ref import classify_image_reference, require_image_str

logger = get_logger()

# 已完成候选定位的调用方经此传入 (路径, stat)，读取链复用同一候选不再二次定位，
# 消除签名定位与读取定位之间文件被替换的错位窗口。
local_candidate_ctx: ContextVar[LocalImageCandidate | None] = ContextVar(
    "local_candidate", default=None
)


@asynccontextmanager
async def local_candidate_scope(candidate: LocalImageCandidate | None) -> AsyncIterator[None]:
    """在作用域内向读取链注入本地候选，退出时复位上下文不泄漏。"""
    token = local_candidate_ctx.set(candidate)
    try:
        yield
    finally:
        local_candidate_ctx.reset(token)


async def prepare_image_input(image: str) -> str:
    """将单张图像输入归一化为 API 所需格式。

    - HTTP/HTTPS URL：经统一校验拒绝 userinfo 凭据等不安全形态后原样返回。
    - Data URI：经格式与维度校验后将 media type 归一化为小写标准 MIME 返回。
    - 本地文件路径：读取并编码为 Base64 Data URI 返回。

    URL 的 urlparse 全量解析与 Data URI 的 base64 解码均有阻塞事件循环的成本，
    均下沉工作线程执行。

    Args:
        image: 图像输入字符串，可为 HTTP/HTTPS URL、Data URI 或本地文件路径。

    Raises:
        SeedreamValidationError: 输入格式无效、路径越界、界内定位失败或本地文件
            读取失败等本地预处理失败；本阶段不触网，不参与 client 的 API 重试。
    """
    try:
        normalized = require_image_str(image).strip()

        kind = classify_image_reference(normalized)
        if kind != "local":
            return await asyncio.to_thread(validate_image_input, normalized)

        return await asyncio.to_thread(_prepare_local_image, normalized, image)
    except SeedreamMCPError:
        raise
    except Exception as e:
        # 兜底异常均来自本地预处理，归校验档。
        raise SeedreamValidationError(f"图像处理失败: {e}") from e


def _resolves_outside_read_scope(
    normalized: str, images_root: Path, read_scope: list[Path]
) -> bool:
    """判断输入路径解析后的物理位置是否落在读权限之外。

    候选管线与 resolve_local_image_candidate 共用 iter_local_candidates，任一
    候选命中读权限任一目录即界内。UNC 路径不 resolve 以免触发 SMB 连接，UNC
    输入直接返回 False 交诊断分支处理。
    """
    if is_unc_path(normalized):
        return False
    return not any(iter_local_candidates(normalized, images_root, read_scope))


def _relative_breaks_images_root(normalized: str, images_root: Path) -> bool:
    """判断相对输入解析后是否越出图片目录，相对路径仅限图片目录内。

    非法路径形态交后续分支处理，此处返回 False 不抢报。
    """
    if os.path.isabs(normalized):
        return False
    try:
        resolved = normalize_path(normalized, str(images_root))
    except (OSError, ValueError):
        return False
    return not is_within_resolved(resolved, images_root)


def _format_local_read_error(exc: OSError, normalized: str) -> str:
    """构建本地文件读取失败的错误文案，回显解析后的绝对路径。

    路径取异常携带的 filename，缺失时回退调用方输入的原样字符串，与诊断分支
    回显 resolve 后绝对路径的口径一致。系统错误语义取 strerror，缺失时回退
    errno 数值。
    """
    if exc.strerror:
        reason = exc.strerror
    elif exc.errno is not None:
        reason = f"errno {exc.errno}"
    else:
        reason = "无法读取"
    return f"读取图像文件失败: {reason}: {exc.filename or normalized}"


def _prepare_local_image(normalized: str, original: str) -> str:
    """校验本地图片路径并读取编码为 Base64 Data URI。

    候选定位委托 resolve_local_image_candidate，与 ImagePreparer 的缓存签名共用
    同一选择规则，锁定同一文件；调用方已完成定位时经 local_candidate_ctx 传入，
    读取复用同一候选不再二次定位。(图片目录, 读权限) 经 get_read_context 在函数顶
    部单点求值，各分支共享，消除一次失败请求内的重复解析。越界抛携带配置指引
    的错误；界内定位失败经 validate_image_path 做诊断性校验，取具体失败原因并附
    图片目录内的相似路径建议。各失败均属参数校验语义而非 API 调用失败，归
    SeedreamValidationError。需在工作线程中调用。
    """
    found = local_candidate_ctx.get()
    if found is None:
        _, images_root, read_scope = get_read_context()
        found = resolve_local_image_candidate(
            normalized, images_root=images_root, read_scope=read_scope
        )
        if found is None:
            if _relative_breaks_images_root(normalized, images_root):
                raise SeedreamValidationError(
                    "相对路径仅限图片保存目录内，其他位置请使用绝对路径",
                    field="image",
                    value=normalized,
                )
            if _resolves_outside_read_scope(normalized, images_root, read_scope):
                raise SeedreamValidationError(
                    read_scope_denial_message("路径"),
                    field="image",
                    value=normalized,
                )
            _, error_msg, _ = validate_image_path(normalized, skip_dimensions=True)
            error_text = error_msg or "图像路径校验失败"
            suggestions = suggest_similar_paths(original, search_dirs=[str(images_root)])
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n\n建议的相似路径:\n" + "\n".join(
                    f"  • {suggestion}" for suggestion in suggestions[:3]
                )
            raise SeedreamValidationError(
                f"{error_text}{suggestion_text}", field="image", value=normalized
            )

    validated_path, _ = found

    # 读取、限额复核与维度校验经共享辅助，与校验链同一安全语义。内存峰值：读取
    # 字节、b64 编码与 data URI 拼接约为单图的 5.5×，并发 5 × 30MB 上限下瞬态约
    # 800MB；PIL 解码成本落在工作线程而非事件循环。
    image_bytes = read_and_decode_local_image(
        validated_path,
        field_value=normalized,
        format_read_error=lambda exc: _format_local_read_error(exc, normalized),
    )
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    # MIME 以字节签名为准、扩展名回退：扩展名可伪造，与 auto_save 保存路径同口径。
    # 两来源的扩展名均为映射键，直取使键缺失以 KeyError 显式暴露而非静默回落。
    inferred_extension = infer_extension_from_bytes(image_bytes, default="")
    suffix = inferred_extension or validated_path.suffix.lower()
    mime_type = MIME_BY_EXTENSION[suffix]

    logger.info("成功处理图片文件: {} ({} 字节)", validated_path, len(image_bytes))
    return f"data:{mime_type};base64,{image_b64}"
