"""图像输入校验：URL、本地文件路径与 Data URI 的格式与维度校验。

从 validation 模块拆分出涉及 I/O 的图像校验：本地文件经 O_NOFOLLOW 读取字节后交
PIL 解码校验维度，Data URI 经 base64 解码后同样校验。纯参数校验（尺寸、水印、
prompt 等）仍留在 validation 模块，本模块的维度常量经 validation 重导出供输出尺寸
校验复用。涉及 path_utils 的调用采用函数内延迟 import，规避模块加载循环。
"""

from __future__ import annotations

import base64
import io
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import SeedreamValidationError
from .formats import (
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS_ORDERED,
    _format_file_size_mb,
    parse_data_uri,
)
from .os_utils import open_no_follow_read

# HEIC/HEIF 解码器惰性注册，避免模块导入时的全局副作用，首次校验图片时按需注册。
# check-then-set 非线程安全，并发首调用可能重复注册；register_heif_opener 与
# MAX_IMAGE_PIXELS 赋值均幂等，重复执行无功能影响。
_heif_opener_registered = False


def _ensure_heif_opener_registered() -> None:
    """注册 HEIC/HEIF 解码器并配置 PIL 解压炸弹防护，仅首次调用时执行。

    PIL 与 pillow_heif 延迟导入，避免模块导入期加载图像库产生全局副作用。
    """
    global _heif_opener_registered
    if _heif_opener_registered:
        return
    from PIL import Image
    from pillow_heif import register_heif_opener

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    register_heif_opener()
    _heif_opener_registered = True


def decode_and_validate_dimensions(image_bytes: bytes, value_label: str) -> None:
    """解码图像字节并校验像素维度，供本地文件与预处理两条路径复用。

    image_bytes 为已读取的完整图像字节，value_label 用于错误信息。维度超限由
    _validate_image_dimensions 抛 SeedreamValidationError；解码异常抛 PIL 原生类型，
    由调用方按所属模块的异常基类包装。
    """
    from PIL import Image

    _ensure_heif_opener_registered()
    with Image.open(io.BytesIO(image_bytes)) as img:
        _validate_image_dimensions(img.size[0], img.size[1], value_label)


# 输入图像文件大小上限，本地文件与 Data URI 两条校验路径共用
MAX_IMAGE_FILE_SIZE = 30 * 1024 * 1024  # 30MB
# 参考图即输入图像，其维度约束含最短边、宽高比上下限、总像素上限。
# 宽高比上下限同时用于输出尺寸校验，输入与输出沿用相同规则。
MIN_IMAGE_EDGE = 15
MIN_IMAGE_RATIO = 1 / 16
MAX_IMAGE_RATIO = 16
MAX_IMAGE_PIXELS = 6000 * 6000


def _get_validation_base_dir() -> Path:
    """获取本地文件校验的基础目录。

    委托 path_utils.resolve_env_workspace_root 保持单一来源；函数内延迟 import
    避免 path_utils 与本模块的加载循环。
    """
    from .path_utils import resolve_env_workspace_root

    return resolve_env_workspace_root()


def _resolve_local_image_path(file_path: str) -> Path:
    """解析本地图片路径，相对路径基于校验基础目录解析，绝对路径保持原样。"""
    raw_path = Path(file_path).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (_get_validation_base_dir() / raw_path).resolve()


def _validate_url(url: str) -> str:
    """验证 HTTP/HTTPS URL 的格式正确性，检查 scheme、netloc 等部分是否完整。

    Args:
        url: 待验证的 URL 字符串。

    Returns:
        验证通过时返回原始 URL。

    Raises:
        SeedreamValidationError: 当 URL 格式无效时抛出。
    """
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"} or not parsed.netloc:
            raise SeedreamValidationError("无效的URL格式", field="image", value=url)
        return url
    except ValueError as e:
        raise SeedreamValidationError(f"URL验证失败: {str(e)}", field="image", value=url) from e


def _validate_image_dimensions(width: int, height: int, value: Any) -> None:
    """校验图像宽高下限、宽高比与总像素约束，不满足时抛出 SeedreamValidationError。

    供本地文件与 Data URI 两条校验路径复用，避免维度规则重复实现导致漂移。
    """
    if width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE:
        raise SeedreamValidationError("图像宽高长度至少15px", field="image", value=value)

    ratio = width / height
    if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
        raise SeedreamValidationError("图像宽高比需在[1/16, 16]范围内", field="image", value=value)

    if width * height > MAX_IMAGE_PIXELS:
        raise SeedreamValidationError("图像总像素不能超过 6000×6000", field="image", value=value)


def _validate_file_path(file_path: str, skip_dimensions: bool = False) -> str:
    """验证本地文件路径的存在性、格式与尺寸限制。

    执行存在性、文件类型、扩展名、文件大小检查；skip_dimensions 为 False 时还校验
    图像维度。路径的工作区边界由调用方以授权 Roots 集合保证：本函数解析用的基础目录
    取自环境配置，与 MCP 运行时的 Roots 集合来源不同，故不在函数内做边界断言，以免
    误拒 Roots 授权但环境根之外的合法路径。调用方 path_utils.validate_image_path 与
    client 的本地文件签名校验已在前置环节用正确的 Roots 集合完成越界拦截。

    Args:
        file_path: 本地文件的完整路径。
        skip_dimensions: 是否跳过图像像素维度校验。

    Returns:
        文件的绝对路径。

    Raises:
        SeedreamValidationError: 当文件不存在、格式不支持或尺寸超限时抛出。
    """
    try:
        path = _resolve_local_image_path(file_path)

        # 单次 stat 完成存在性、文件类型与大小检查
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            raise SeedreamValidationError(f"文件不存在: {path}", field="image", value=file_path)
        except OSError as exc:
            raise SeedreamValidationError(
                f"无法访问文件: {path} -> {exc}", field="image", value=file_path
            ) from exc
        if not stat.S_ISREG(stat_result.st_mode):
            raise SeedreamValidationError(f"路径不是文件: {path}", field="image", value=file_path)

        # 检查文件扩展名
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise SeedreamValidationError(
                f"不支持的图像格式: {path.suffix}，支持的格式: {SUPPORTED_IMAGE_EXTENSIONS_ORDERED}",
                field="image",
                value=file_path,
            )

        # 检查文件大小
        file_size = stat_result.st_size
        if file_size > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"文件过大: {_format_file_size_mb(file_size)}，"
                f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=file_path,
            )

        if not skip_dimensions:
            from PIL import Image

            # 经 open_no_follow_read 读取字节后交共享解码校验，拒绝最终分量符号链接，
            # 与 image_input._prepare_local_image 保持一致的安全语义
            try:
                with open_no_follow_read(path) as f:
                    image_bytes = f.read()
                decode_and_validate_dimensions(image_bytes, file_path)
            except OSError as exc:
                raise SeedreamValidationError(
                    f"无法读取文件: {path} -> {exc}",
                    field="image",
                    value=file_path,
                ) from exc
            except (ValueError, Image.DecompressionBombError) as e:
                raise SeedreamValidationError(
                    f"图像维度解析失败: {str(e)}",
                    field="image",
                    value=file_path,
                ) from e

        return str(path.absolute())

    except (OSError, ValueError, RuntimeError) as e:
        raise SeedreamValidationError(
            f"文件路径验证失败: {str(e)}", field="image", value=file_path
        ) from e


def _validate_data_uri(data_uri: str) -> str:
    """验证 Data URI 格式图像数据的格式、可解码性、大小与像素维度。

    Args:
        data_uri: Data URI 格式的图像字符串。

    Returns:
        验证通过时返回原始 Data URI。

    Raises:
        SeedreamValidationError: 当格式无效、数据损坏或尺寸超限时抛出。
    """
    try:
        # 经 formats.parse_data_uri 统一拆分 media type 与负载，消除与 auto_save 的重复解析
        media_type, b64 = parse_data_uri(data_uri)
        if media_type is None or not b64:
            raise SeedreamValidationError("Data URI 格式无效", field="image", value=data_uri)

        # parse_data_uri 仅拆出 media type 与负载；编码标记需在原始 header 中确认，
        # 确保后续按 base64 解码而非误处理 url-encoded 或纯文本负载
        header_lower = data_uri.split(",", 1)[0].lower()
        if not header_lower.startswith("data:image/") or ";base64" not in header_lower:
            raise SeedreamValidationError(
                "Data URI 必须为 data:image/<格式>;base64, 前缀且小写",
                field="image",
                value=data_uri,
            )

        # 提取并验证图像格式。media_type 形如 image/png，取斜杠后部分为格式标识；
        # 白名单派生自 SUPPORTED_IMAGE_EXTENSIONS 以保持单一来源
        fmt = media_type.lower().split("image/")[-1]
        allowed = {ext.lstrip(".") for ext in SUPPORTED_IMAGE_EXTENSIONS}
        if fmt not in allowed:
            raise SeedreamValidationError(
                f"不支持的Data URI图片格式: {fmt}", field="image", value=data_uri
            )

        # 先按 base64 文本长度估算解码后大小，避免对巨型文本先解码触发内存放大
        if len(b64) > MAX_IMAGE_FILE_SIZE * 4 // 3 + 16:
            raise SeedreamValidationError(
                f"数据过大: base64 长度 {len(b64)}，"
                f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=data_uri,
            )

        # Base64解码
        try:
            raw = base64.b64decode(b64, validate=True)
        except ValueError as e:
            raise SeedreamValidationError(
                f"Base64 解码失败: {str(e)}", field="image", value=data_uri
            ) from e

        # 检查数据大小
        size_bytes = len(raw)
        if size_bytes > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"数据过大: {_format_file_size_mb(size_bytes)}，"
                f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=data_uri,
            )

        # 验证图像像素维度约束
        from PIL import Image

        try:
            decode_and_validate_dimensions(raw, data_uri)
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            raise SeedreamValidationError(
                f"图像维度解析失败: {str(e)}", field="image", value=data_uri
            ) from e

        return data_uri

    except (OSError, ValueError) as e:
        raise SeedreamValidationError(
            f"Data URI 验证失败: {str(e)}", field="image", value=data_uri
        ) from e


def validate_image_input(image: str, skip_dimensions: bool = False) -> str:
    """验证图像输入的有效性，支持 HTTP/HTTPS URL、本地文件路径与 Data URI 三种格式。

    Args:
        image: 图像 URL、文件路径或 Data URI。
        skip_dimensions: 是否跳过本地文件的像素维度校验。

    Returns:
        验证通过的图像路径或原始输入。

    Raises:
        SeedreamValidationError: 当图像输入格式无效或不可访问时抛出。
    """
    if not image or not isinstance(image, str):
        raise SeedreamValidationError("图像路径不能为空", field="image", value=image)

    image = image.strip()
    if not image:
        raise SeedreamValidationError("图像路径不能为空", field="image", value=image)

    # Data URI 格式验证
    if image.lower().startswith("data:image/"):
        return _validate_data_uri(image)

    # HTTP/HTTPS URL 验证
    if image.startswith(("http://", "https://")):
        return _validate_url(image)

    # 本地文件路径验证
    return _validate_file_path(image, skip_dimensions=skip_dimensions)
