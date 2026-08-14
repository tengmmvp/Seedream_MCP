"""图像输入预处理：将多种来源的图像统一归一化为 API 可接受的格式。

支持 URL、Data URI 与本地文件路径三种来源。URL 与 Data URI 经校验后原样返回；
本地文件路径在工作区边界校验通过后，经 O_NOFOLLOW 读取并编码为 Base64 Data URI。
该模块从 SeedreamClient 剥离，使客户端专注于 API 调用，预处理逻辑可独立测试与复用。
"""

from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlparse

from PIL import Image

from ..core.errors import SeedreamAPIError, SeedreamMCPError, SeedreamValidationError
from ..core.formats import MIME_BY_EXTENSION, _format_file_size_mb
from ..core.logs import get_logger
from ..io.io_file import open_no_follow_read
from ..io.io_path import get_workspace_roots, suggest_similar_paths
from .image_validation import (
    MAX_IMAGE_FILE_SIZE,
    decode_and_validate_dimensions,
    validate_image_input,
    validate_image_path,
)
from .image_ref import classify_image_reference

logger = get_logger(__name__)


async def prepare_image_input(image: str) -> str:
    """将单张图像输入归一化为 API 所需格式。

    - HTTP/HTTPS URL：校验主机后原样返回。
    - Data URI：经格式与维度校验后原样返回。
    - 本地文件路径：读取并编码为 Base64 Data URI 返回。

    Data URI 校验与本地文件读取均在工作线程中执行，避免阻塞事件循环。
    """
    try:
        normalized = image.strip()

        kind = classify_image_reference(normalized)
        if kind == "url":
            parsed = urlparse(normalized)
            if not parsed.netloc:
                raise SeedreamAPIError(f"无效的图像 URL: {normalized}")
            return normalized

        if kind == "data_uri":
            # validate_image_input 内含 PIL 解码等同步操作，放到工作线程避免阻塞事件循环。
            return await asyncio.to_thread(validate_image_input, normalized)

        # 本地文件：路径校验、读取与编码均为同步 IO，整体放到工作线程。
        return await asyncio.to_thread(_prepare_local_image, normalized, image)
    except SeedreamMCPError:
        raise
    except Exception as e:
        raise SeedreamAPIError(f"图像处理失败: {e}") from e


def _prepare_local_image(normalized: str, original: str) -> str:
    """校验本地图片路径并读取编码为 Base64 Data URI。

    路径需通过任一工作区 Root 的越界校验方可读取；全部 Root 均校验失败时给出
    相似路径建议。需在工作线程中调用。
    """
    workspace_roots = get_workspace_roots()
    if not workspace_roots:
        raise SeedreamAPIError("当前 MCP 会话未授权任何工作区目录，无法读取本地图片。")

    validated_path = None
    validation_errors: list[str] = []
    for root in workspace_roots:
        is_valid, error_msg, normalized_path = validate_image_path(
            normalized, base_dir=str(root), skip_dimensions=True
        )
        if is_valid and normalized_path is not None:
            validated_path = normalized_path
            break
        if error_msg:
            validation_errors.append(error_msg)

    if validated_path is None:
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

    # O_NOFOLLOW 防护最终路径分量、拒绝符号链接，由 io_file 统一实现；
    # 符号链接或打开失败抛 OSError，由 prepare_image_input 外层转 SeedreamAPIError。
    with open_no_follow_read(validated_path) as f:
        # 限制读取量并复核，防校验与读取间文件被替换为超大文件撑爆内存
        image_bytes = f.read(MAX_IMAGE_FILE_SIZE + 1)
    if len(image_bytes) > MAX_IMAGE_FILE_SIZE:
        raise SeedreamValidationError(
            f"文件过大: {_format_file_size_mb(len(image_bytes))}，"
            f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
            field="image",
            value=str(validated_path),
        )
    # 维度校验复用已读字节，维度相关异常与 image_validation 路径对齐为 SeedreamValidationError
    try:
        decode_and_validate_dimensions(image_bytes, str(validated_path))
    except SeedreamValidationError:
        raise
    except (ValueError, Image.DecompressionBombError) as e:
        raise SeedreamValidationError(
            f"图像维度解析失败: {str(e)}",
            field="image",
            value=str(validated_path),
        ) from e
    except Exception as e:
        raise SeedreamAPIError(f"图像维度解析失败: {e}") from e
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    suffix = validated_path.suffix.lower()
    mime_type = MIME_BY_EXTENSION.get(suffix, "image/jpeg")

    logger.info("成功处理图片文件: {} ({} bytes)", validated_path, len(image_bytes))
    return f"data:{mime_type};base64,{image_b64}"
