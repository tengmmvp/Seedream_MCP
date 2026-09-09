"""图像输入校验：URL、本地文件路径与 Data URI 的格式与维度校验。

涉及 I/O 的图像校验归本模块：本地文件经 O_NOFOLLOW 读取字节后交 PIL 解码校验
维度，Data URI 经 base64 解码后同样校验；纯参数校验（尺寸、水印、prompt 等）与
宽高比常量归 validators，本模块从其导入共用。validate_image_path 组合工作区边界
判定与统一规则校验。首次使用时进程级覆写 PIL.Image.MAX_IMAGE_PIXELS 为 36M，
嵌入本包的宿主进程须感知该副作用。
"""

from __future__ import annotations

import base64
import io
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.errors import SeedreamValidationError
from ..core.formats import (
    MAX_IMAGE_PIXELS,
    MIME_BY_EXTENSION,
    SUPPORTED_IMAGE_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS_ORDERED,
    ensure_image_decoders_ready,
    format_file_size_mb,
    format_file_too_large,
    parse_data_uri,
)
from ..core.validators import MAX_IMAGE_RATIO, MIN_IMAGE_RATIO
from ..core.logs import get_logger
from ..io.io_file import open_no_follow_read
from ..io.io_path import (
    get_read_scope,
    has_windows_colon_component,
    is_unc_path,
    is_within_resolved,
    normalize_path,
    resolve_images_root,
)
from .image_ref import classify_image_reference

logger = get_logger()

# 无法识别图像内容的固定错误文案。UnidentifiedImageError 的 str 嵌有 BytesIO
# 对象的内存地址，直接拼入用户可见消息会把服务器侧对象地址带给调用方。
UNIDENTIFIED_IMAGE_MESSAGE = "无法识别的图像内容"

# 输入图像文件大小上限，本地文件与 Data URI 两条校验路径共用。
MAX_IMAGE_FILE_SIZE = 30 * 1024 * 1024

# 参考图即输入图像：最短边上限在此，总像素上限由 formats 单一来源提供，宽高比
# 上下限由 validators 持有共用。
MIN_IMAGE_EDGE = 15

# 本地候选定位结果：(resolve 后物理路径, stat)，缓存签名计算与读取链共用同一候选。
LocalImageCandidate = tuple[Path, os.stat_result]


def decode_and_validate_dimensions(image_bytes: bytes, value_label: str) -> None:
    """解码图像字节并校验像素维度与数据完整性，供本地文件与预处理两条路径复用。

    image_bytes 为已读取的完整图像字节，value_label 用于错误信息。维度超限由
    _validate_image_dimensions 抛 SeedreamValidationError；img.verify 校验像素数据
    完整性，头合法但数据截断的文件在本地即被拒；解码与校验异常抛 PIL 原生类型，
    由调用方按所属模块的异常基类包装。

    Args:
        image_bytes: 已读取的完整图像字节。
        value_label: 出现在错误信息中的输入标识。

    Raises:
        SeedreamValidationError: 宽高下限、宽高比或总像素任一约束不满足。
    """
    from PIL import Image

    ensure_image_decoders_ready()
    with Image.open(io.BytesIO(image_bytes)) as img:
        _validate_image_dimensions(img.size[0], img.size[1], value_label)
        # verify 不解码像素，仅校验数据完整性；本路径不复用解码结果，verify 后
        # 图像不可用的限制无影响。
        img.verify()


def decode_and_validate_image_bytes(image_bytes: bytes, field_value: str) -> None:
    """解码校验维度并把 PIL 原生异常统一包装为固定文案的校验错误。

    本地文件与 Data URI 两条路径共用的异常包装单一来源。

    Raises:
        SeedreamValidationError: 维度约束不满足或解码失败。
    """
    from PIL import Image

    try:
        decode_and_validate_dimensions(image_bytes, field_value)
    except SeedreamValidationError:
        raise
    except (ValueError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        # SyntaxError：Pillow 的 PNG 插件对损坏 chunk 抛原生 SyntaxError，归一为
        # 校验错误，保留 field/value 元数据。
        message = (
            UNIDENTIFIED_IMAGE_MESSAGE
            if is_unidentified_image_error(exc)
            else f"图像维度解析失败: {str(exc)}"
        )
        raise SeedreamValidationError(message, field="image", value=field_value) from exc


def is_unidentified_image_error(exc: BaseException) -> bool:
    """判断异常是否为 PIL.UnidentifiedImageError，供各解码失败包装分支共用。"""
    from PIL import Image

    return isinstance(exc, Image.UnidentifiedImageError)


def read_and_decode_local_image(
    path: Path,
    *,
    field_value: str,
    format_read_error: Callable[[OSError], str],
) -> bytes:
    """O_NOFOLLOW 读取本地图像字节并做限额复核与维度校验，供校验与读取两条路径复用。

    读取失败的文案由调用方经 format_read_error 生成；超限、维度与解码失败用两
    条路径一致的固定文案，UnidentifiedImageError 用固定文案避免对象地址进入用户
    消息。返回读取的字节供调用方编码复用。

    Args:
        path: 已通过候选定位的本地文件路径。
        field_value: 出现在错误信息中的调用方输入标识。
        format_read_error: 读取阶段 OSError 的文案生成函数。

    Returns:
        读取并通过限额复核的完整图像字节。

    Raises:
        SeedreamValidationError: 读取失败、超出大小上限或维度与解码校验失败。
    """
    try:
        with open_no_follow_read(path) as f:
            # 限制读取量并复核，防校验与读取间文件被替换为超大文件撑爆内存。
            image_bytes = f.read(MAX_IMAGE_FILE_SIZE + 1)
    except OSError as exc:
        raise SeedreamValidationError(
            format_read_error(exc), field="image", value=field_value
        ) from exc
    if len(image_bytes) > MAX_IMAGE_FILE_SIZE:
        raise SeedreamValidationError(
            format_file_too_large(len(image_bytes), MAX_IMAGE_FILE_SIZE),
            field="image",
            value=field_value,
        )
    decode_and_validate_image_bytes(image_bytes, field_value)
    return image_bytes


def _get_validation_base_dir() -> Path:
    """本地文件校验的基目录，取图片目录，与参考图读取链的解析基准一致。"""
    return resolve_images_root()


def _resolve_local_image_path(file_path: str) -> Path:
    """解析本地图片路径，相对路径以图片目录为基准，绝对路径保持原样。

    不做 ~ 前缀展开，与 resolve_local_image_candidate、normalize_path 的定位口径
    一致。UNC 路径在 resolve 前抛 ValueError 拒绝，避免 Windows 下 resolve 触发
    SMB 认证。

    Raises:
        ValueError: 路径为 UNC 形式。
    """
    if is_unc_path(file_path):
        raise ValueError(f"拒绝 UNC 路径以避免触发 SMB 连接: {file_path}")
    raw_path = Path(file_path)
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (_get_validation_base_dir() / raw_path).resolve()


def _validate_url(url: str) -> str:
    """验证 HTTP/HTTPS URL 的格式，scheme 与 netloc 须完整且不携带 userinfo 凭据。"""
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


def iter_local_candidates(
    image: str, base_dir: str | Path, read_scope: list[Path]
) -> Iterator[Path]:
    """迭代界内的候选物理路径：绝对路径判读权限，相对路径仅限图片目录内。

    绝对路径直接作为候选，相对路径以 base_dir（图片目录）拼接；候选 resolve 一次
    后按形态判定：绝对候选与读权限集合逐项比较，相对候选须落在图片目录之内，
    ``..`` 与符号链接逃逸同样被拦截。UNC 前缀的候选不 resolve，避免在 Windows
    触发 SMB 认证。候选定位与越界判定两条路径共用本迭代器，保证判定口径一致。

    Args:
        image: 输入路径字符串，可为绝对或相对路径。
        base_dir: 相对路径的解析基准与边界，为图片目录。
        read_scope: 已 resolve 的读权限目录列表（工作区 ∪ 图片目录）。

    Yields:
        resolve 后落在对应边界内的候选物理路径。
    """
    is_absolute = os.path.isabs(image)
    candidates = [Path(image)] if is_absolute else [Path(base_dir) / image]
    for candidate in candidates:
        # 驱动器相对形态（C:foo）pathlib 拼接会丢弃 base_dir 锚定到该盘进程 CWD，
        # 与 normalize_path 同口径跳过该形态，消除两条链路的判定分叉。
        if candidate.drive and not candidate.root:
            continue
        # UNC 根拼接出的候选仍以 UNC 前缀开头，resolve 会触发 SMB 连接，跳过。
        if is_unc_path(str(candidate)):
            continue
        try:
            resolved_candidate = candidate.resolve()
        except (OSError, ValueError):
            continue
        if is_absolute:
            if any(is_within_resolved(resolved_candidate, base) for base in read_scope):
                yield resolved_candidate
        elif is_within_resolved(resolved_candidate, Path(base_dir)):
            yield resolved_candidate


def resolve_local_image_candidate(
    image: str,
    *,
    images_root: Path | None = None,
    read_scope: list[Path] | None = None,
) -> LocalImageCandidate | None:
    """定位可读取的候选图片文件：绝对路径判读权限，相对路径仅限图片目录内。

    界内候选逐一做 image_candidate_stat 资格检查，返回首个命中的
    (resolve 后物理路径, stat)，未命中返回 None。ImagePreparer 的缓存签名与
    image_input 的读取路径共用此定位，保证签名与实际读取锁定同一文件。
    images_root 与 read_scope 未提供时按当前请求现取；调用方在一次请求内多次
    定位时传入 get_read_context 的共享结果，消除重复求值。

    Args:
        image: 输入路径字符串，可为绝对或相对路径。
        images_root: 已 resolve 的图片目录，相对路径的解析基准。
        read_scope: 已 resolve 的读权限目录列表（工作区 ∪ 图片目录）。

    Returns:
        首个命中候选的 (物理路径, stat)；无命中时为 None。

    Raises:
        SeedreamValidationError: win32 下输入路径分量含冒号，即 NTFS 备用数据流
            形态时抛出，先于候选构造与文件读取。
    """
    # UNC 的 resolve 在 Windows 会触发 SMB 认证，须在候选构造前判定；反斜杠形态
    # 的 UNC 在 POSIX 上非绝对路径，先拼后查会丢失 UNC 前缀。
    if is_unc_path(image):
        return None
    # classify 仅特判 http(s) 与 data，file:// 等其余 scheme 形态落入本地分支；
    # 先按形态给出诊断，不落入冒号分量拒绝被误报为 NTFS 备用数据流。
    if "://" in image:
        raise SeedreamValidationError(
            f"仅支持图像 URL（http/https）、Data URI 或本地路径: {image}",
            field="image",
            value=image,
        )
    # 分量含冒号是 NTFS 备用数据流形态，流名不参与越界判定，界内文件名携带流
    # 后缀时读取命中的是同文件的另一数据流；判定与 normalize_path 共用单一来源，
    # 参考图链与浏览、保存链拒绝口径一致。
    if has_windows_colon_component(image):
        raise SeedreamValidationError(
            f"拒绝参考图路径分量含冒号以避免访问 NTFS 备用数据流: {image}",
            field="image",
            value=image,
        )
    if images_root is None:
        images_root = resolve_images_root()
    if read_scope is None:
        read_scope = get_read_scope()
    for resolved_candidate in iter_local_candidates(image, images_root, read_scope):
        st = image_candidate_stat(resolved_candidate)
        if st is not None:
            return resolved_candidate, st
    return None


def _validate_image_dimensions(width: int, height: int, value: Any) -> None:
    """校验图像宽高下限、宽高比与总像素约束，供本地文件与 Data URI 两条路径复用。"""
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
    """验证本地文件路径的存在性、文件类型、扩展名与大小，默认还校验图像维度。

    工作区边界由调用方以授权 Roots 集合保证：本函数解析用的基目录取自环境配置，
    与 MCP Roots 来源不同，函数内不做边界断言以免误拒 Roots 授权的合法路径。维度
    读取经 open_no_follow_read 打开最终分量；path 已由 _resolve_local_image_path
    resolve 跟随符号链接，O_NOFOLLOW 仅防 resolve 与 open 之间的 TOCTOU 窗口，
    主要越界防御由调用方的边界 resolve 与比较提供。

    Args:
        file_path: 本地文件的完整路径。
        skip_dimensions: 是否跳过图像像素维度校验。

    Returns:
        文件的绝对路径。

    Raises:
        SeedreamValidationError: 文件不存在、格式不支持或尺寸超限时抛出。
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
                f"不支持的图像格式: {path.suffix}，"
                f"支持的格式: {'/'.join(SUPPORTED_IMAGE_EXTENSIONS_ORDERED)}",
                field="image",
                value=file_path,
            )

        file_size = stat_result.st_size
        if file_size > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                format_file_too_large(file_size, MAX_IMAGE_FILE_SIZE),
                field="image",
                value=file_path,
            )

        if not skip_dimensions:
            # 读取与解码校验经共享辅助，与 image_input 的读取链同一安全语义。
            read_and_decode_local_image(
                path,
                field_value=file_path,
                format_read_error=lambda exc: f"无法读取文件: {path} -> {exc}",
            )

        return str(path.absolute())

    except (OSError, ValueError, RuntimeError, SyntaxError) as e:
        raise SeedreamValidationError(
            f"文件路径验证失败: {str(e)}", field="image", value=file_path
        ) from e


# Data URI 允许的格式标识白名单，派生自 SUPPORTED_IMAGE_EXTENSIONS，避免每次
# 校验重建集合。
_DATA_URI_ALLOWED_FORMATS: frozenset[str] = frozenset(
    ext.lstrip(".") for ext in SUPPORTED_IMAGE_EXTENSIONS
)


def _validate_data_uri(data_uri: str) -> str:
    """验证 Data URI 的格式、可解码性、大小与像素维度。

    校验通过后把 media type 归一化为小写标准 MIME（``image/jpg`` 一并归一为
    ``image/jpeg``）重建 Data URI 返回，使送往上游的载荷恒为官方要求的标准形态，
    用户侧大小写误写不再依赖上游报错。
    """
    try:
        # 经 formats.parse_data_uri 统一拆分，与 auto_save 共用单一解析。
        media_type, b64, is_base64 = parse_data_uri(data_uri)
        if media_type is None or not b64:
            raise SeedreamValidationError("Data URI 格式无效", field="image", value=data_uri)

        if not is_base64 or not media_type.lower().startswith("image/"):
            raise SeedreamValidationError(
                "Data URI 必须为 data:image/<格式>;base64, 前缀（scheme 大小写不敏感）",
                field="image",
                value=data_uri,
            )

        # 格式标识取 media_type 斜杠后部分。
        fmt = media_type.lower().split("image/")[-1]
        if fmt not in _DATA_URI_ALLOWED_FORMATS:
            raise SeedreamValidationError(
                f"不支持的Data URI图片格式: {fmt}", field="image", value=data_uri
            )
        # 官方要求格式小写；标准 MIME 子类型经 formats 单一映射派生，jpg 随之归一为 jpeg。
        canonical_fmt = MIME_BY_EXTENSION[f".{fmt}"].split("/", 1)[1]

        # 先按 base64 文本长度估算解码后大小，避免对巨型文本先解码触发内存放大。
        if len(b64) > MAX_IMAGE_FILE_SIZE * 4 // 3 + 16:
            raise SeedreamValidationError(
                f"数据过大: base64 长度 {len(b64)}，"
                f"最大支持{format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
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
                f"数据过大: {format_file_size_mb(size_bytes)}，"
                f"最大支持{format_file_size_mb(MAX_IMAGE_FILE_SIZE)}",
                field="image",
                value=data_uri,
            )

        decode_and_validate_image_bytes(raw, data_uri)

        return f"data:image/{canonical_fmt};base64,{b64}"

    except (OSError, ValueError, SyntaxError) as e:
        raise SeedreamValidationError(
            f"Data URI 验证失败: {str(e)}", field="image", value=data_uri
        ) from e


def validate_image_input(image: str, skip_dimensions: bool = False) -> str:
    """验证图像输入的有效性，支持 HTTP/HTTPS URL、本地文件路径与 Data URI 三种格式。

    本函数对本地文件路径不做工作区越界校验，调用方须先经 validate_image_path 完成
    基于 MCP Roots 的越界判定，避免直接传入本地路径绕过工作区边界。

    Args:
        image: 图像 URL、文件路径或 Data URI。
        skip_dimensions: 是否跳过本地文件的像素维度校验。

    Returns:
        验证通过的图像输入：URL 原样返回，本地文件为绝对路径，Data URI 以归一化的 media type 重建返回。

    Raises:
        SeedreamValidationError: 图像输入格式无效或不可访问时抛出。
    """
    if not isinstance(image, str):
        raise SeedreamValidationError("图像输入必须是字符串", field="image", value=image)
    if not image:
        raise SeedreamValidationError("图像路径不能为空", field="image", value=image)

    image = image.strip()
    if not image:
        raise SeedreamValidationError("图像路径不能为空", field="image", value=image)

    kind = classify_image_reference(image)
    if kind == "data_uri":
        return _validate_data_uri(image)
    if kind == "url":
        return _validate_url(image)
    return _validate_file_path(image, skip_dimensions=skip_dimensions)


def validate_image_path(path: str, skip_dimensions: bool = False) -> tuple[bool, str, Path | None]:
    """验证图片文件路径，强制其位于读权限内并符合图片规则。

    HTTP(S) URL 与 Data URI 为非本地引用，无读取范围可言，视为有效但标准化路径
    恒为 None；Data URI 的内容校验由 validate_image_input 承担。调用方须同时检查
    有效位与路径是否为 None，不可仅凭有效位判定为本地路径。

    Args:
        path: 图片文件路径；HTTP(S) URL 与 Data URI 有效但路径返回 None。
        skip_dimensions: 是否跳过图片像素维度校验。

    Returns:
        三元组 (是否有效, 错误信息, 标准化路径)。
    """
    try:
        path = path.strip()
        kind = classify_image_reference(path)
        if kind in ("url", "data_uri"):
            return True, "", None

        normalized_path = normalize_path(path, str(resolve_images_root()))
        # 越界判定面向读权限集合（工作区 ∪ 图片目录），与候选定位同口径。
        if not any(is_within_resolved(normalized_path, scope) for scope in get_read_scope()):
            return False, "路径不在读取范围内", normalized_path

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
