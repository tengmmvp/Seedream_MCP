"""图像输入校验：URL、本地文件路径与 Data URI 的格式与维度校验。

涉及 I/O 的图像校验归本模块：本地文件经 O_NOFOLLOW 读取字节后交 PIL 解码校验
维度，Data URI 经 base64 解码后同样校验。纯参数校验（尺寸、水印、prompt 等）与
宽高比常量归 validators，本模块从其导入共用。validate_image_path 组合工作区边界
判定与统一规则校验，供 image_input 等调用方使用。
"""

from __future__ import annotations

import base64
import io
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.errors import SeedreamValidationError
from ..core.formats import (
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS_ORDERED,
    _format_file_size_mb,
    parse_data_uri,
)
from ..core.validators import MAX_IMAGE_RATIO, MIN_IMAGE_RATIO
from ..core.logs import get_logger
from ..io.io_file import open_no_follow_read
from ..io.io_path import (
    _is_unc_path,
    get_workspace_root,
    is_within_resolved,
    normalize_path,
    resolve_env_workspace_root,
)
from .image_ref import classify_image_reference

logger = get_logger(__name__)

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


# 输入图像文件大小上限，本地文件与 Data URI 两条校验路径共用。
MAX_IMAGE_FILE_SIZE = 30 * 1024 * 1024
# 参考图即输入图像，其维度约束含最短边、宽高比上下限、总像素上限。
# 宽高比上下限由 validators 持有，输出尺寸校验与本模块共用同一规则。
MIN_IMAGE_EDGE = 15
MAX_IMAGE_PIXELS = 6000 * 6000


def _get_validation_base_dir() -> Path:
    """获取本地文件校验的基础目录，委托 io_path.resolve_env_workspace_root 保持单一来源。"""
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
        # 拒绝 userinfo，参考图 URL 不应携带凭据，且含凭据 URL 会被送往上游 API 致泄露。
        if parsed.username or parsed.password:
            raise SeedreamValidationError("URL 不允许携带用户名密码", field="image", value=url)
        return url
    except ValueError as e:
        raise SeedreamValidationError(f"URL验证失败: {str(e)}", field="image", value=url) from e


def image_candidate_stat(path: Path) -> os.stat_result | None:
    """返回通过图片文件资格检查的 stat，供候选定位与读取路径共用同一规则。

    资格规则：常规文件、扩展名在支持白名单、大小不超过 MAX_IMAGE_FILE_SIZE；
    任一不满足或 stat 失败返回 None。
    """
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        return None
    if st.st_size > MAX_IMAGE_FILE_SIZE:
        return None
    return st


def resolve_local_image_candidate(
    image: str, resolved_bases: list[Path]
) -> tuple[Path, os.stat_result] | None:
    """按输入路径与已 resolve 的工作区根列表定位可读取的候选图片文件。

    绝对路径直接作为候选；相对路径按根序逐一拼接。候选 resolve 一次后经
    is_within_resolved 与各已 resolve 根直接比较（拦截 ``..`` 与符号链接越界），
    通过后经 image_candidate_stat 做文件资格检查，返回首个命中的
    (resolve 后物理路径, stat)。越界判定不经 is_path_within_any_base 二次
    resolve：该函数对每张本地图在缓存签名与读取两条路径各调用一次，Windows/网络
    挂载下重复 resolve 是缓存命中路径的主要剩余开销。ImagePreparer 的缓存签名与
    image_input 的读取路径共用此定位，保证签名与实际读取锁定同一文件，杜绝两侧
    规则漂移导致签名命中与读取内容不一致的陈旧缓存。未命中返回 None，由调用方
    决定回退或报错。
    """
    candidates = (
        [Path(image)] if os.path.isabs(image) else [base / image for base in resolved_bases]
    )
    for candidate in candidates:
        # UNC 路径的 resolve 在 Windows 会触发 SMB 认证，须在 resolve 前拦截，
        # 避免凭据在越界拒绝尚未发生时已向远端泄露；直接比较优化不得丢失该前置守卫。
        if _is_unc_path(str(candidate)):
            continue
        try:
            resolved_candidate = candidate.resolve()
        except (OSError, ValueError):
            continue
        if not any(is_within_resolved(resolved_candidate, base) for base in resolved_bases):
            continue
        st = image_candidate_stat(resolved_candidate)
        if st is not None:
            return resolved_candidate, st
    return None


def _validate_image_dimensions(width: int, height: int, value: Any) -> None:
    """校验图像宽高下限、宽高比与总像素约束，不满足时抛出 SeedreamValidationError。

    供本地文件与 Data URI 两条校验路径复用，避免维度规则重复实现导致漂移。
    """
    if width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE:
        raise SeedreamValidationError(
            f"图像宽高长度至少{MIN_IMAGE_EDGE}px", field="image", value=value
        )

    ratio = width / height
    if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
        raise SeedreamValidationError(
            f"图像宽高比需在[{MIN_IMAGE_RATIO}, {MAX_IMAGE_RATIO}]范围内",
            field="image",
            value=value,
        )

    if width * height > MAX_IMAGE_PIXELS:
        raise SeedreamValidationError(
            f"图像总像素不能超过 {MAX_IMAGE_PIXELS}", field="image", value=value
        )


def _validate_file_path(file_path: str, skip_dimensions: bool = False) -> str:
    """验证本地文件路径的存在性、格式与尺寸限制。

    执行存在性、文件类型、扩展名、文件大小检查；skip_dimensions 为 False 时还校验
    图像维度。路径的工作区边界由调用方以授权 Roots 集合保证：本函数解析用的基础目录
    取自环境配置，与 MCP 运行时的 Roots 集合来源不同，故不在函数内做边界断言，以免
    误拒 Roots 授权但环境根之外的合法路径。调用方 validate_image_path 与
    resolve_local_image_candidate 已在前置环节用正确的 Roots 集合完成越界拦截。

    图像维度读取经 open_no_follow_read 打开最终分量。path 已由
    _resolve_local_image_path 调用 resolve() 跟随符号链接，故 O_NOFOLLOW 对初始输入
    即符号链接的防护效果减弱，其贡献仅限 resolve 与 open 之间的 TOCTOU 窗口防护；
    主要的符号链接越界防御由调用方的边界 resolve 与比较提供。

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

        # 单次 stat 完成存在性、文件类型与大小检查。
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

        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise SeedreamValidationError(
                f"不支持的图像格式: {path.suffix}，支持的格式: {SUPPORTED_IMAGE_EXTENSIONS_ORDERED}",
                field="image",
                value=file_path,
            )

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
            # 与 image_input._prepare_local_image 保持一致的安全语义。
            try:
                with open_no_follow_read(path) as f:
                    # 限制读取量并复核，防 stat 与 read 间文件被替换为超大文件撑爆内存。
                    image_bytes = f.read(MAX_IMAGE_FILE_SIZE + 1)
                if len(image_bytes) > MAX_IMAGE_FILE_SIZE:
                    raise SeedreamValidationError(
                        f"文件过大: {_format_file_size_mb(len(image_bytes))}，"
                        f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                        field="image",
                        value=file_path,
                    )
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
        # 经 formats.parse_data_uri 统一拆分 media type 与负载，消除与 auto_save 的重复解析。
        media_type, b64 = parse_data_uri(data_uri)
        if media_type is None or not b64:
            raise SeedreamValidationError("Data URI 格式无效", field="image", value=data_uri)

        # parse_data_uri 仅拆出 media type 与负载；编码标记需在原始 header 中确认，
        # 确保后续按 base64 解码而非误处理 url-encoded 或纯文本负载。
        header_lower = data_uri.split(",", 1)[0].lower()
        if not header_lower.startswith("data:image/") or ";base64" not in header_lower:
            raise SeedreamValidationError(
                "Data URI 必须为 data:image/<格式>;base64, 前缀（scheme 大小写不敏感）",
                field="image",
                value=data_uri,
            )

        # 提取并验证图像格式。media_type 形如 image/png，取斜杠后部分为格式标识；
        # 白名单派生自 SUPPORTED_IMAGE_EXTENSIONS 以保持单一来源。
        fmt = media_type.lower().split("image/")[-1]
        allowed = {ext.lstrip(".") for ext in SUPPORTED_IMAGE_EXTENSIONS}
        if fmt not in allowed:
            raise SeedreamValidationError(
                f"不支持的Data URI图片格式: {fmt}", field="image", value=data_uri
            )

        # 先按 base64 文本长度估算解码后大小，避免对巨型文本先解码触发内存放大。
        if len(b64) > MAX_IMAGE_FILE_SIZE * 4 // 3 + 16:
            raise SeedreamValidationError(
                f"数据过大: base64 长度 {len(b64)}，"
                f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=data_uri,
            )

        try:
            raw = base64.b64decode(b64, validate=True)
        except ValueError as e:
            raise SeedreamValidationError(
                f"Base64 解码失败: {str(e)}", field="image", value=data_uri
            ) from e

        size_bytes = len(raw)
        if size_bytes > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"数据过大: {_format_file_size_mb(size_bytes)}，"
                f"最大支持{_format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=data_uri,
            )

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

    本函数对本地文件路径仅校验存在性、格式与维度，不强制工作区越界校验。调用方须先经
    本模块的 validate_image_path 完成基于 MCP Roots 的越界判定后再调用本函数，避免
    直接传入本地路径绕过工作区边界。

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

    # 三类来源统一经 classify_image_reference 判定，scheme 大小写不敏感。
    kind = classify_image_reference(image)
    if kind == "data_uri":
        return _validate_data_uri(image)
    if kind == "url":
        return _validate_url(image)
    return _validate_file_path(image, skip_dimensions=skip_dimensions)


def validate_image_path(
    path: str, base_dir: str | None = None, skip_dimensions: bool = False
) -> tuple[bool, str, Path | None]:
    """验证图片文件路径，强制其位于工作区边界内并符合图片规则。

    HTTP(S) URL 视为有效但标准化路径恒为 None，调用方须同时检查有效位与路径是否
    为 None，据以分流 URL 与本地文件处理，不可仅凭有效位判定为本地路径。

    Args:
        path: 图片文件路径；HTTP(S) URL 有效但路径返回 None。
        base_dir: 工作区基础目录，用于越界校验；None 时回退首个工作区根，多根工作区仅校验首个根，完整多根校验须由调用方遍历各根分别调用。
        skip_dimensions: 是否跳过图片像素维度校验。

    Returns:
        三元组 (是否有效, 错误信息, 标准化路径)；URL 有效但路径为 None。
    """
    try:
        if classify_image_reference(path) == "url":
            return True, "", None

        if base_dir is None:
            base_dir = str(get_workspace_root())
        normalized_path = normalize_path(path, base_dir)
        base_path = Path(base_dir).resolve()
        # normalized_path 与 base_path 均 resolve 完成，直接比较避免重复解析
        if not is_within_resolved(normalized_path, base_path):
            return False, "路径超出允许的工作区目录范围", normalized_path

        try:
            validated_path = validate_image_input(
                str(normalized_path), skip_dimensions=skip_dimensions
            )
            return True, "", Path(validated_path)
        except SeedreamValidationError as e:
            return False, e.message, normalized_path

    except Exception as e:
        logger.error("路径验证失败 {}: {}", path, e)
        return False, f"路径验证错误: {str(e)}", None
