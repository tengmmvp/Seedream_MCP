"""
Seedream MCP工具 - 参数验证模块
"""

# 标准库导入
import base64
import io
import re
import stat
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

# 第三方库导入
from PIL import Image
from pillow_heif import register_heif_opener

# 本地模块导入
from .errors import SeedreamValidationError
from .formats import SUPPORTED_IMAGE_EXTENSIONS
from .logging import get_logger
from .os_utils import open_no_follow_read
from .model_capabilities import get_model_capabilities
from .model_capabilities import (  # noqa: F401  以下重导出，兼容外部 from .validation import
    MODEL_CAPABILITIES as MODEL_CAPABILITIES,
    MODEL_FAMILY_40 as MODEL_FAMILY_40,
    MODEL_FAMILY_45 as MODEL_FAMILY_45,
    MODEL_FAMILY_50_LITE as MODEL_FAMILY_50_LITE,
    MODEL_FAMILY_50_PRO as MODEL_FAMILY_50_PRO,
    MODEL_FAMILY_UNKNOWN as MODEL_FAMILY_UNKNOWN,
    SEEDREAM_50PRO_MAX_REFERENCE_IMAGES as SEEDREAM_50PRO_MAX_REFERENCE_IMAGES,
    SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES as SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
    ModelCapabilities as ModelCapabilities,
    _resolve_model_family as _resolve_model_family,
    get_max_reference_images as get_max_reference_images,
    is_seedream_50_pro_model as is_seedream_50_pro_model,
)

logger = get_logger(__name__)


# HEIC/HEIF 解码器惰性注册，避免模块导入时的全局副作用，首次校验图片时按需注册
_heif_opener_registered = False


def _ensure_heif_opener_registered() -> None:
    """注册 HEIC/HEIF 解码器，仅首次调用时执行。"""
    global _heif_opener_registered
    if _heif_opener_registered:
        return
    register_heif_opener()
    _heif_opener_registered = True


# ==================== 常量定义 ====================

# 统一图像校验常量
MAX_IMAGE_FILE_SIZE = 30 * 1024 * 1024  # 30MB
# optimize_prompt_options.mode 合法取值
VALID_OPTIMIZE_MODES = frozenset({"standard", "fast"})
MIN_IMAGE_EDGE = 15
MIN_IMAGE_RATIO = 1 / 16
MAX_IMAGE_RATIO = 16
MAX_IMAGE_PIXELS = 6000 * 6000
VALID_SIZE_PRESETS = {"1K", "2K", "3K", "4K"}
VALID_OUTPUT_FORMATS = {"jpeg", "png"}
VALID_GENERATION_TOOL_TYPES = {"web_search"}
PIXEL_SIZE_PATTERN = re.compile(r"^(\d{2,5})x(\d{2,5})$", re.IGNORECASE)
MAX_SEQUENTIAL_TOTAL_IMAGES = 15  # 组图：参考图数量与生成数量之和上限
MAX_PARALLEL_REQUEST_COUNT = 4  # 并行生成请求次数与并行度上限


# ==================== 底层私有工具函数 ====================


def _get_validation_base_dir() -> Path:
    """
    获取本地文件校验的基础目录。

    委托 path_utils.resolve_env_workspace_root 保持单一来源；函数内延迟 import
    避免 path_utils 与 validation 的模块加载循环。
    """
    from .path_utils import resolve_env_workspace_root

    return resolve_env_workspace_root()


def _resolve_local_image_path(file_path: str) -> Path:
    """
    解析本地图片路径。

    相对路径基于校验基础目录解析，绝对路径保持原样。
    """
    raw_path = Path(file_path).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (_get_validation_base_dir() / raw_path).resolve()


def _parse_pixel_size(size: str) -> tuple[int, int] | None:
    """
    解析像素尺寸字符串。
    """
    matched = PIXEL_SIZE_PATTERN.fullmatch(size.strip())
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


def _coerce_positive_int_in_range(value: Any, field: str, min_value: int, max_value: int) -> int:
    """
    将任意输入校验并转换为指定范围内的正整数。
    """
    if isinstance(value, bool):
        raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)
    try:
        validated_value = int(value)
    except (ValueError, TypeError):
        raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)

    if validated_value < min_value or validated_value > max_value:
        raise SeedreamValidationError(
            f"{field} 必须在 {min_value}-{max_value} 之间",
            field=field,
            value=validated_value,
        )
    return validated_value


# 模型家族解析、能力表与判定函数已抽取至 model_capabilities.py，上方通过 re-export 保持兼容


# ==================== 私有验证函数 ====================


def _validate_url(url: str) -> str:
    """
    验证HTTP/HTTPS URL的格式正确性

    检查URL的scheme、netloc等部分是否完整。

    Args:
        url: 待验证的URL字符串

    Returns:
        str: 原始URL（验证通过）

    Raises:
        SeedreamValidationError: 当URL格式无效时抛出
    """
    try:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in {"http", "https"} or not parsed.netloc:
            raise SeedreamValidationError("无效的URL格式", field="image", value=url)

        return url

    except SeedreamValidationError:
        raise
    except Exception as e:
        raise SeedreamValidationError(f"URL验证失败: {str(e)}", field="image", value=url)


def _validate_image_dimensions(width: int, height: int, value: Any) -> None:
    """校验图像宽高下限、宽高比与总像素约束，不满足时抛出 SeedreamValidationError。

    供本地文件与 Data URI 两条校验路径复用，避免维度规则重复实现导致漂移。
    """
    if width < MIN_IMAGE_EDGE or height < MIN_IMAGE_EDGE:
        raise SeedreamValidationError("图像宽高长度至少15px", field="image", value=value)

    ratio = width / height if height else 0
    if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
        raise SeedreamValidationError("图像宽高比需在[1/16, 16]范围内", field="image", value=value)

    if width * height > MAX_IMAGE_PIXELS:
        raise SeedreamValidationError("图像总像素不能超过 6000×6000", field="image", value=value)


def _validate_file_path(file_path: str, skip_dimensions: bool = False) -> str:
    """
    验证本地文件路径的存在性、格式和尺寸限制

    执行以下检查：
    - 文件是否存在
    - 是否为有效文件（而非目录）
    - 文件扩展名是否支持
    - 文件大小是否超过30MB
    - 图像尺寸是否符合要求（宽高>14px，宽高比在1/16到16之间，总像素≤6000×6000）

    Args:
        file_path: 本地文件的完整路径

    Returns:
        str: 文件的绝对路径

    Raises:
        SeedreamValidationError: 当文件不存在、格式不支持或尺寸超限时抛出
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
            )
        if not stat.S_ISREG(stat_result.st_mode):
            raise SeedreamValidationError(f"路径不是文件: {path}", field="image", value=file_path)

        # 检查文件扩展名
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise SeedreamValidationError(
                f"不支持的图像格式: {path.suffix}，支持的格式: {SUPPORTED_IMAGE_EXTENSIONS}",
                field="image",
                value=file_path,
            )

        # 检查文件大小
        file_size = stat_result.st_size
        if file_size > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"文件过大: {file_size / 1024 / 1024:.1f}MB，最大支持{MAX_IMAGE_FILE_SIZE // 1024 // 1024}MB",
                field="image",
                value=file_path,
            )

        if not skip_dimensions:
            # 经 open_no_follow_read 读取字节后再交 PIL 解码，拒绝最终分量符号链接，
            # 与 image_input._prepare_local_image 保持一致的安全语义
            try:
                _ensure_heif_opener_registered()
                with open_no_follow_read(path) as f:
                    image_bytes = f.read()
                with Image.open(io.BytesIO(image_bytes)) as img:
                    _validate_image_dimensions(img.size[0], img.size[1], file_path)
            except SeedreamValidationError:
                raise
            except OSError as exc:
                raise SeedreamValidationError(
                    f"无法读取文件: {path} -> {exc}",
                    field="image",
                    value=file_path,
                )
            except Exception as e:
                raise SeedreamValidationError(
                    f"图像维度解析失败: {str(e)}",
                    field="image",
                    value=file_path,
                )

        return str(path.absolute())

    except SeedreamValidationError:
        raise
    except Exception as e:
        raise SeedreamValidationError(f"文件路径验证失败: {str(e)}", field="image", value=file_path)


def _validate_data_uri(data_uri: str) -> str:
    """
    验证Data URI格式的图像数据

    执行以下检查：
    - Data URI格式是否正确（data:image/<格式>;base64,<数据>）
    - 图像格式是否支持
    - Base64数据是否可解码
    - 解码后数据大小是否超过30MB
    - 图像尺寸是否符合要求（宽高>14px，宽高比在1/16到16之间，总像素≤6000×6000）

    Args:
        data_uri: Data URI格式的图像字符串

    Returns:
        str: 原始Data URI（验证通过）

    Raises:
        SeedreamValidationError: 当格式无效、数据损坏或尺寸超限时抛出
    """
    try:
        # 解析Data URI结构
        header, _, b64 = data_uri.partition(",")
        if not header or not b64:
            raise SeedreamValidationError("Data URI 格式无效", field="image", value=data_uri)

        # 验证Header格式
        header_lower = header.lower()
        if not header_lower.startswith("data:image/") or ";base64" not in header_lower:
            raise SeedreamValidationError(
                "Data URI 必须为 data:image/<格式>;base64, 前缀且小写",
                field="image",
                value=data_uri,
            )

        # 提取并验证图像格式。白名单派生自 SUPPORTED_IMAGE_EXTENSIONS 以保持单一来源
        fmt = header_lower.split("data:image/")[-1].split(";")[0]
        allowed = {ext.lstrip(".") for ext in SUPPORTED_IMAGE_EXTENSIONS}
        if fmt not in allowed:
            raise SeedreamValidationError(
                f"不支持的Data URI图片格式: {fmt}", field="image", value=data_uri
            )

        # 先按 base64 文本长度估算解码后大小，避免对巨型文本先解码触发内存放大
        if len(b64) > MAX_IMAGE_FILE_SIZE * 4 // 3 + 16:
            raise SeedreamValidationError(
                f"数据过大: base64 长度 {len(b64)}，最大支持"
                f"{MAX_IMAGE_FILE_SIZE // 1024 // 1024}MB",
                field="image",
                value=data_uri,
            )

        # Base64解码
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception as e:
            raise SeedreamValidationError(
                f"Base64 解码失败: {str(e)}", field="image", value=data_uri
            )

        # 检查数据大小
        size_bytes = len(raw)
        if size_bytes > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"数据过大: {size_bytes / 1024 / 1024:.1f}MB，最大支持{MAX_IMAGE_FILE_SIZE // 1024 // 1024}MB",
                field="image",
                value=data_uri,
            )

        # 验证图像像素维度约束
        try:
            _ensure_heif_opener_registered()
            with Image.open(io.BytesIO(raw)) as img:
                _validate_image_dimensions(img.size[0], img.size[1], data_uri)
        except SeedreamValidationError:
            raise
        except Exception as e:
            raise SeedreamValidationError(
                f"图像维度解析失败: {str(e)}", field="image", value=data_uri
            )

        return data_uri

    except SeedreamValidationError:
        raise
    except Exception as e:
        raise SeedreamValidationError(f"Data URI 验证失败: {str(e)}", field="image", value=data_uri)


# ==================== 基础公共验证函数 ====================


def validate_prompt(prompt: str, max_chinese_chars: int = 300, max_english_words: int = 600) -> str:
    """
    验证文本提示词的有效性和长度限制

    当中文字符超过 `max_chinese_chars` 或英文单词超过 `max_english_words` 时，视为过长。
    """
    if not prompt or not isinstance(prompt, str):
        raise SeedreamValidationError("提示词不能为空", field="prompt", value=prompt)

    prompt = prompt.strip()
    if not prompt:
        raise SeedreamValidationError("提示词不能为空", field="prompt", value=prompt)

    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", prompt))
    english_word_count = len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", prompt))

    if chinese_count > max_chinese_chars or english_word_count > max_english_words:
        # 文档为"建议"而非硬限制：超限时仅记录警告，不阻断调用
        logger.warning(
            "提示词较长（中文{}个/英文{}个），建议不超过{}个汉字或{}个英文单词，可能影响生成效果",
            chinese_count,
            english_word_count,
            max_chinese_chars,
            max_english_words,
        )

    return prompt


def validate_watermark(watermark: Any) -> bool:
    """
    验证水印参数配置

    支持布尔值或可转换为布尔值的字符串（true/false、yes/no、on/off、1/0）。

    Args:
        watermark: 水印开关配置，支持bool或str类型

    Returns:
        bool: 标准化后的布尔值

    Raises:
        SeedreamValidationError: 当参数类型或格式无效时抛出
    """
    if isinstance(watermark, bool):
        return watermark

    if isinstance(watermark, str):
        watermark_lower = watermark.lower().strip()
        if watermark_lower in ("true", "1", "yes", "on"):
            return True
        elif watermark_lower in ("false", "0", "no", "off"):
            return False
        else:
            raise SeedreamValidationError(
                "水印参数必须是布尔值或有效的字符串（true/false）",
                field="watermark",
                value=watermark,
            )

    raise SeedreamValidationError("水印参数必须是布尔值", field="watermark", value=watermark)


def validate_response_format(response_format: str) -> str:
    """
    验证响应格式参数

    Args:
        response_format: 响应格式类型，支持 url 或 b64_json

    Returns:
        str: 标准化后的格式值（小写）

    Raises:
        SeedreamValidationError: 当格式参数无效时抛出
    """
    valid_formats = ["url", "b64_json"]

    if not response_format or not isinstance(response_format, str):
        raise SeedreamValidationError(
            "响应格式不能为空", field="response_format", value=response_format
        )

    response_format = response_format.strip().lower()
    if response_format not in valid_formats:
        raise SeedreamValidationError(
            f"响应格式必须是以下值之一: {valid_formats}",
            field="response_format",
            value=response_format,
        )

    return response_format


def validate_output_format(output_format: Any, model_id: str) -> str | None:
    """
    验证图像输出文件格式，并检查与模型兼容性。
    """
    if output_format is None:
        return None

    if not isinstance(output_format, str):
        raise SeedreamValidationError(
            "output_format 必须是字符串",
            field="output_format",
            value=output_format,
        )

    normalized = output_format.strip().lower()
    if not normalized:
        raise SeedreamValidationError(
            "output_format 不能为空",
            field="output_format",
            value=output_format,
        )

    if normalized not in VALID_OUTPUT_FORMATS:
        raise SeedreamValidationError(
            f"output_format 仅支持 {sorted(VALID_OUTPUT_FORMATS)}",
            field="output_format",
            value=output_format,
        )

    # output_format 仅 5.0 系列支持，4.5/4.0 不支持，未知模型放行，由能力表统一判定
    if not get_model_capabilities(model_id).supports_output_format:
        raise SeedreamValidationError(
            "仅 doubao-seedream-5.0 系列（5.0 Pro/5.0 Lite）模型支持 output_format",
            field="output_format",
            value=output_format,
        )

    return normalized


def validate_generation_tools(tools: Any, model_id: str) -> List[dict[str, str]] | None:
    """
    验证生成工具配置，并检查与模型兼容性。
    """
    if tools is None:
        return None

    if not isinstance(tools, list):
        raise SeedreamValidationError(
            "tools 必须是数组",
            field="tools",
            value=tools,
        )

    # tools 联网搜索仅 5.0 Lite 支持，5.0 Pro/4.5/4.0 不支持，未知模型放行，由能力表统一判定
    if not get_model_capabilities(model_id).supports_tools:
        raise SeedreamValidationError(
            "仅 doubao-seedream-5.0 Lite 模型支持 tools（5.0 Pro/4.5/4.0 不支持联网搜索）",
            field="tools",
            value=tools,
        )

    normalized_tools: List[dict[str, str]] = []
    for index, tool in enumerate(tools, start=1):
        if not isinstance(tool, dict):
            raise SeedreamValidationError(
                "tools 的每一项都必须是对象",
                field=f"tools[{index}]",
                value=tool,
            )

        extra_keys = set(tool.keys()) - {"type"}
        if extra_keys:
            raise SeedreamValidationError(
                f"tools[{index}] 包含不支持的字段: {sorted(extra_keys)}",
                field=f"tools[{index}]",
                value=tool,
            )

        tool_type = tool.get("type")
        if not isinstance(tool_type, str):
            raise SeedreamValidationError(
                "tools.type 必须是字符串",
                field=f"tools[{index}].type",
                value=tool_type,
            )

        normalized_type = tool_type.strip().lower()
        if not normalized_type:
            raise SeedreamValidationError(
                "tools.type 不能为空",
                field=f"tools[{index}].type",
                value=tool_type,
            )

        if normalized_type not in VALID_GENERATION_TOOL_TYPES:
            raise SeedreamValidationError(
                f"tools.type 仅支持 {sorted(VALID_GENERATION_TOOL_TYPES)}",
                field=f"tools[{index}].type",
                value=tool_type,
            )

        normalized_tools.append({"type": normalized_type})

    return normalized_tools


def validate_stream(stream: bool, model_id: str) -> bool:
    """
    验证流式输出参数与模型兼容性。

    Seedream 5.0 Pro 不支持流式输出（stream，传参报错），仅 5.0 Lite/4.5/4.0 支持。
    """
    if stream and not get_model_capabilities(model_id).supports_stream:
        raise SeedreamValidationError(
            "doubao-seedream-5.0-pro 不支持流式输出（stream），请使用 5.0 Lite/4.5/4.0",
            field="stream",
            value=stream,
        )
    return stream


def validate_max_images(max_images: Any) -> int:
    """
    验证最大图像数量参数

    确保参数为整数类型且在合理范围内（1-15）。

    Args:
        max_images: 最大图像数量，支持整数或可转换为整数的值

    Returns:
        int: 验证后的整数值

    Raises:
        SeedreamValidationError: 当参数类型错误或超出范围时抛出
    """
    if isinstance(max_images, bool):
        raise SeedreamValidationError(
            "最大图像数量必须是整数", field="max_images", value=max_images
        )
    if isinstance(max_images, int):
        validated_value = max_images
    else:
        try:
            validated_value = int(max_images)
        except (ValueError, TypeError):
            raise SeedreamValidationError(
                "最大图像数量必须是整数", field="max_images", value=max_images
            )

    if validated_value < 1:
        raise SeedreamValidationError(
            "最大图像数量不能小于1", field="max_images", value=validated_value
        )

    if validated_value > MAX_SEQUENTIAL_TOTAL_IMAGES:
        raise SeedreamValidationError(
            f"最大图像数量不能超过{MAX_SEQUENTIAL_TOTAL_IMAGES}",
            field="max_images",
            value=validated_value,
        )

    return validated_value


# ==================== 尺寸验证函数 ====================


def validate_size(size: str) -> str:
    """
    验证图像尺寸参数是否在允许的范围内

    Args:
        size: 图像尺寸规格，支持 1K/2K/3K/4K 或 <宽>x<高>

    Returns:
        str: 标准化后的尺寸值（大写格式）

    Raises:
        SeedreamValidationError: 当尺寸参数无效时抛出
    """
    if not size or not isinstance(size, str):
        raise SeedreamValidationError("图像尺寸不能为空", field="size", value=size)

    normalized = size.strip()
    if not normalized:
        raise SeedreamValidationError("图像尺寸不能为空", field="size", value=size)

    preset = normalized.upper()
    if preset in VALID_SIZE_PRESETS:
        return preset

    pixel_size = _parse_pixel_size(normalized)
    if pixel_size is not None:
        width, height = pixel_size
        return f"{width}x{height}"

    raise SeedreamValidationError(
        "图像尺寸必须为 1K/2K/3K/4K 或 <宽>x<高> 像素值",
        field="size",
        value=size,
    )


def validate_size_for_model(size: str, model_id: str) -> str:
    """
    验证图像尺寸与模型的兼容性

    不同模型对图像尺寸有特定要求，此函数确保尺寸参数符合模型限制。

    Args:
        size: 图像尺寸规格
        model_id: 模型标识符

    Returns:
        str: 验证通过的尺寸值

    Raises:
        SeedreamValidationError: 当尺寸与模型不兼容时抛出
    """
    size = validate_size(size)
    caps = get_model_capabilities(model_id)

    # 分辨率档位校验：各家族支持的档位白名单由能力表声明
    if size in VALID_SIZE_PRESETS:
        if size not in caps.allowed_presets:
            presets_str = "/".join(sorted(caps.allowed_presets))
            raise SeedreamValidationError(
                f"在 {caps.display_name} 模型下仅支持 {presets_str}，"
                "请调整 size 参数或更换为支持该尺寸的模型",
                field="size",
                value=size,
            )
        return size

    # 像素值校验
    parsed = _parse_pixel_size(size)
    if parsed is None:
        raise SeedreamValidationError("图像尺寸格式无效", field="size", value=size)
    width, height = parsed

    ratio = width / height if height else 0
    if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
        raise SeedreamValidationError(
            "尺寸宽高比需在 [1/16, 16] 范围内",
            field="size",
            value=size,
        )

    total_pixels = width * height
    if caps.min_size_pixels is not None and caps.max_size_pixels is not None:
        if not (caps.min_size_pixels <= total_pixels <= caps.max_size_pixels):
            raise SeedreamValidationError(
                f"在 {caps.display_name} 模型下，像素尺寸总像素需在 "
                f"[{caps.min_size_pixels}, {caps.max_size_pixels}] 范围内",
                field="size",
                value=size,
            )
    if caps.size_pixel_multiple is not None and (
        width % caps.size_pixel_multiple != 0 or height % caps.size_pixel_multiple != 0
    ):
        raise SeedreamValidationError(
            f"在 {caps.display_name} 模型下，像素宽高须为 {caps.size_pixel_multiple} 的倍数",
            field="size",
            value=size,
        )

    return size


# ==================== 图像验证函数 ====================


def validate_image_url(image: str, skip_dimensions: bool = False) -> str:
    """
    验证图像URL、文件路径或Data URI的有效性

    支持三种图像输入格式：
    - HTTP/HTTPS URL
    - 本地文件路径
    - Data URI（base64编码）

    Args:
        image: 图像URL、文件路径或Data URI

    Returns:
        str: 验证通过的图像路径

    Raises:
        SeedreamValidationError: 当图像路径格式无效或不可访问时抛出
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


# ==================== 高级验证函数 ====================


def validate_optimize_prompt_options(options: Any, model_id: str) -> dict | None:
    """
    验证提示词优化选项的配置

    检查优化模式是否有效，并确保与模型兼容。

    Args:
        options: 优化选项字典，包含mode等配置
        model_id: 模型标识符

    Returns:
        dict | None: 验证后的优化选项字典，若输入为None则返回None

    Raises:
        SeedreamValidationError: 当选项配置无效或与模型不兼容时抛出
    """
    if options is None:
        return None

    if not isinstance(options, dict):
        raise SeedreamValidationError(
            "optimize_prompt_options必须为对象", field="optimize_prompt_options", value=options
        )

    mode = options.get("mode", "standard")
    if not isinstance(mode, str):
        raise SeedreamValidationError(
            "optimize_prompt_options.mode必须为字符串",
            field="optimize_prompt_options.mode",
            value=mode,
        )

    mode = mode.strip().lower()
    if mode not in VALID_OPTIMIZE_MODES:
        raise SeedreamValidationError(
            f"optimize_prompt_options.mode 必须为 {sorted(VALID_OPTIMIZE_MODES)}",
            field="optimize_prompt_options.mode",
            value=mode,
        )

    caps = get_model_capabilities(model_id or "")
    if not caps.supports_fast_optimize_prompt and mode != "standard":
        raise SeedreamValidationError(
            f"{caps.display_name} 当前仅支持 optimize_prompt_options.mode=standard",
            field="optimize_prompt_options.mode",
            value=mode,
        )

    return {"mode": mode}


def validate_parallel_generation_options(
    *,
    request_count: Any,
    parallelism: Any,
    stream: bool,
    max_request_count: int = MAX_PARALLEL_REQUEST_COUNT,
) -> tuple[int, int]:
    """
    校验并行生成参数组合，并返回规范化后的 request_count/parallelism。
    """
    validated_request_count = _coerce_positive_int_in_range(
        request_count, "request_count", 1, max_request_count
    )

    if parallelism is None:
        validated_parallelism = min(validated_request_count, max_request_count)
    else:
        validated_parallelism = _coerce_positive_int_in_range(
            parallelism, "parallelism", 1, max_request_count
        )

    if validated_parallelism > validated_request_count:
        raise SeedreamValidationError(
            "parallelism 不能大于 request_count",
            field="parallelism",
            value=validated_parallelism,
        )

    if stream and validated_request_count > 1:
        raise SeedreamValidationError(
            "stream=true 时 request_count 必须为 1",
            field="request_count",
            value=validated_request_count,
        )

    return validated_request_count, validated_parallelism


def validate_sequential_image_limit(max_images: int, reference_images: List[str] | None) -> None:
    """
    验证组图输出的总图片数量限制

    要求：
    - 参考图数量 <= 14
    - 参考图数量 + 生成数量 <= 15
    """
    reference_count = len(reference_images) if reference_images else 0
    if reference_count > SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES:
        raise SeedreamValidationError(
            "参考图数量不能超过14，且参考图数量与生成数量之和不能超过15",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )

    if reference_count + max_images > MAX_SEQUENTIAL_TOTAL_IMAGES:
        raise SeedreamValidationError(
            "参考图数量与生成数量之和不能超过15（参考图最多14）",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )


def resolve_sequential_max_images(
    max_images: int | None,
    reference_images: List[str] | None = None,
) -> int:
    """
    根据参考图数量推导组图最大生成数量

    当未显式指定 max_images 时，默认为 15 - len(reference_images)，
    以保证"参考图数量 + 生成数量 <= 15"。

    Args:
        max_images: 用户指定的最大生成数量，None 表示未指定
        reference_images: 参考图片列表，可为空

    Returns:
        推导后的最大生成数量
    """
    if max_images is not None:
        return max_images
    reference_count = len(reference_images) if reference_images else 0
    return MAX_SEQUENTIAL_TOTAL_IMAGES - reference_count
