"""
Seedream MCP工具 - 参数验证模块
"""

# 标准库导入
import io
import os
import re
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

# 第三方库导入
from PIL import Image

# 本地模块导入
from .errors import SeedreamValidationError

# 统一图像校验常量
SUPPORTED_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"]
MAX_IMAGE_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MIN_IMAGE_EDGE = 15
MIN_IMAGE_RATIO = 1 / 16
MAX_IMAGE_RATIO = 16
MAX_IMAGE_PIXELS = 6000 * 6000
VALID_SIZE_PRESETS = {"1K", "2K", "4K"}
PIXEL_SIZE_PATTERN = re.compile(r"^(\d{2,5})x(\d{2,5})$", re.IGNORECASE)
SEEDREAM_4X_MIN_SIZE_PIXELS = 2560 * 1440
SEEDREAM_4X_MAX_SIZE_PIXELS = 4096 * 4096
SEEDREAM_3X_MIN_SIZE_PIXELS = 512 * 512
SEEDREAM_3X_MAX_SIZE_PIXELS = 2048 * 2048


def _get_validation_base_dir() -> Path:
    """
    获取本地文件校验的基础目录。

    优先使用 `SEEDREAM_WORKSPACE_ROOT`，未设置或无效时回退当前工作目录。
    """
    configured_root = os.getenv("SEEDREAM_WORKSPACE_ROOT")
    if configured_root and configured_root.strip():
        try:
            return Path(configured_root).expanduser().resolve()
        except Exception:
            pass
    return Path.cwd().resolve()


def _resolve_local_image_path(file_path: str) -> Path:
    """
    解析本地图片路径。

    相对路径基于校验基础目录解析，绝对路径保持原样。
    """
    raw_path = Path(file_path).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()
    return (_get_validation_base_dir() / raw_path).resolve()


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
        raise SeedreamValidationError(
            (
                f"提示词过长，建议不超过{max_chinese_chars}个汉字或{max_english_words}个英文单词"
                f"（当前中文{chinese_count}个，英文{english_word_count}个）"
            ),
            field="prompt",
            value=prompt,
        )

    return prompt


def validate_size(size: str) -> str:
    """
    验证图像尺寸参数是否在允许的范围内

    Args:
        size: 图像尺寸规格，支持 1K/2K/4K 或 <宽>x<高>

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
        "图像尺寸必须为 1K/2K/4K 或 <宽>x<高> 像素值",
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
    mid = (model_id or "").lower()

    # 分辨率档位校验
    if size in VALID_SIZE_PRESETS:
        if "doubao-seedream-4-5" in mid or "doubao-seedream-4.5" in mid:
            if size not in {"2K", "4K"}:
                raise SeedreamValidationError(
                    "在 doubao-seedream-4.5 模型下仅支持 2K/4K",
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
    if any(token in mid for token in ("doubao-seedream-4-5", "doubao-seedream-4.5")) or any(
        token in mid for token in ("doubao-seedream-4-0", "doubao-seedream-4.0")
    ):
        if (
            total_pixels < SEEDREAM_4X_MIN_SIZE_PIXELS
            or total_pixels > SEEDREAM_4X_MAX_SIZE_PIXELS
        ):
            raise SeedreamValidationError(
                (
                    "在 doubao-seedream-4.5/4.0 模型下，像素尺寸总像素需在 "
                    f"[{SEEDREAM_4X_MIN_SIZE_PIXELS}, {SEEDREAM_4X_MAX_SIZE_PIXELS}] 范围内"
                ),
                field="size",
                value=size,
            )
    elif any(token in mid for token in ("doubao-seedream-3-0", "doubao-seedream-3.0")) or any(
        token in mid for token in ("doubao-seededit-3-0", "doubao-seededit-3.0")
    ):
        if (
            total_pixels < SEEDREAM_3X_MIN_SIZE_PIXELS
            or total_pixels > SEEDREAM_3X_MAX_SIZE_PIXELS
        ):
            raise SeedreamValidationError(
                (
                    "在 doubao-seedream-3.0/doubao-seededit-3.0 模型下，像素尺寸总像素需在 "
                    f"[{SEEDREAM_3X_MIN_SIZE_PIXELS}, {SEEDREAM_3X_MAX_SIZE_PIXELS}] 范围内"
                ),
                field="size",
                value=size,
            )

    return size


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
    allowed = {"standard", "fast"}
    if mode not in allowed:
        raise SeedreamValidationError(
            f"optimize_prompt_options.mode 必须为 {sorted(list(allowed))}",
            field="optimize_prompt_options.mode",
            value=mode,
        )

    # doubao-seedream-4.5 模型仅支持 standard 模式
    mid = (model_id or "").lower()
    if "doubao-seedream-4-5" in mid or "doubao-seedream-4.5" in mid:
        if mode != "standard":
            raise SeedreamValidationError(
                "doubao-seedream-4.5 当前仅支持 optimize_prompt_options.mode=standard",
                field="optimize_prompt_options.mode",
                value=mode,
            )

    return {"mode": mode}


def validate_image_url(image: str) -> str:
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
    return _validate_file_path(image)


def validate_image_list(images: List[str], min_count: int = 1, max_count: int = 14) -> List[str]:
    """
    验证图像路径列表的有效性和数量限制

    对列表中的每个图像路径进行验证，并检查总数是否在允许范围内。

    Args:
        images: 图像URL或文件路径列表
        min_count: 最小图像数量，默认为1
        max_count: 最大图像数量，默认为14

    Returns:
        List[str]: 验证通过的图像路径列表

    Raises:
        SeedreamValidationError: 当列表为空、数量超限或任一路径无效时抛出
    """
    if not images or not isinstance(images, list):
        raise SeedreamValidationError("图像列表不能为空", field="images", value=images)

    if len(images) < min_count:
        raise SeedreamValidationError(
            f"图像数量不能少于{min_count}张", field="images", value=images
        )

    if len(images) > max_count:
        raise SeedreamValidationError(
            f"图像数量不能超过{max_count}张", field="images", value=images
        )

    # 逐个验证图像路径
    validated_images = []
    for i, image in enumerate(images):
        try:
            validated_image = validate_image_url(image)
            validated_images.append(validated_image)
        except SeedreamValidationError as e:
            raise SeedreamValidationError(
                f"第{i+1}张图像验证失败: {e.message}", field=f"images[{i}]", value=image
            )

    return validated_images


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

    if validated_value > 15:
        raise SeedreamValidationError(
            "最大图像数量不能超过15", field="max_images", value=validated_value
        )

    return validated_value


def validate_sequential_image_limit(max_images: int, reference_images: List[str] | None) -> None:
    """
    验证组图输出的总图片数量限制

    要求：
    - 参考图数量 <= 14
    - 参考图数量 + 生成数量 <= 15
    """
    reference_count = len(reference_images) if reference_images else 0
    if reference_count > 14:
        raise SeedreamValidationError(
            "参考图数量不能超过14，且参考图数量与生成数量之和不能超过15",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )

    if reference_count + max_images > 15:
        raise SeedreamValidationError(
            "参考图数量与生成数量之和不能超过15（参考图最多14）",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )


def _parse_pixel_size(size: str) -> tuple[int, int] | None:
    """
    解析像素尺寸字符串（如 2560x1440）。
    """
    matched = PIXEL_SIZE_PATTERN.fullmatch(size.strip())
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


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


# ==================== 私有辅助函数 ====================


def _validate_url(url: str) -> str:
    """
    验证HTTP/HTTPS URL的格式正确性

    检查URL的scheme、netloc等部分是否完整，并对图像扩展名进行简单校验。

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

        # 检查URL路径中的图像扩展名
        if parsed.path:
            path_lower = parsed.path.lower()
            if not any(path_lower.endswith(ext) for ext in SUPPORTED_IMAGE_EXTENSIONS):
                # 没有明显的图像扩展名，给出警告但不阻止
                pass

        return url

    except Exception as e:
        raise SeedreamValidationError(f"URL验证失败: {str(e)}", field="image", value=url)


def _validate_file_path(file_path: str) -> str:
    """
    验证本地文件路径的存在性、格式和尺寸限制

    执行以下检查：
    - 文件是否存在
    - 是否为有效文件（而非目录）
    - 文件扩展名是否支持
    - 文件大小是否超过10MB
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

        # 检查文件是否存在
        if not path.exists():
            raise SeedreamValidationError(
                f"文件不存在: {path}", field="image", value=file_path
            )

        # 检查是否为文件
        if not path.is_file():
            raise SeedreamValidationError(
                f"路径不是文件: {path}", field="image", value=file_path
            )

        # 检查文件扩展名
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise SeedreamValidationError(
                f"不支持的图像格式: {path.suffix}，支持的格式: {SUPPORTED_IMAGE_EXTENSIONS}",
                field="image",
                value=file_path,
            )

        # 检查文件大小
        file_size = path.stat().st_size
        if file_size > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"文件过大: {file_size / 1024 / 1024:.1f}MB，最大支持10MB",
                field="image",
                value=file_path,
            )

        # 验证图像像素维度约束
        try:
            with Image.open(path) as img:
                w, h = img.size

                # 宽高必须大于14像素
                if w < MIN_IMAGE_EDGE or h < MIN_IMAGE_EDGE:
                    raise SeedreamValidationError(
                        "图像宽高长度必须大于14px",
                        field="image",
                        value=file_path,
                    )

                # 宽高比限制在 1/16 到 16 之间
                ratio = w / h if h else 0
                if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
                    raise SeedreamValidationError(
                        "图像宽高比需在[1/16, 16]范围内",
                        field="image",
                        value=file_path,
                    )

                # 总像素不超过 6000×6000
                if w * h > MAX_IMAGE_PIXELS:
                    raise SeedreamValidationError(
                        "图像总像素不能超过 6000×6000",
                        field="image",
                        value=file_path,
                    )
        except SeedreamValidationError:
            raise
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
    - 解码后数据大小是否超过10MB
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

        # 提取并验证图像格式
        fmt = header_lower.split("data:image/")[-1].split(";")[0]
        allowed = {"jpeg", "png", "webp", "bmp", "tiff", "gif"}
        if fmt not in allowed:
            raise SeedreamValidationError(
                f"不支持的Data URI图片格式: {fmt}", field="image", value=data_uri
            )

        # Base64解码
        try:
            import base64

            raw = base64.b64decode(b64, validate=True)
        except Exception as e:
            raise SeedreamValidationError(
                f"Base64 解码失败: {str(e)}", field="image", value=data_uri
            )

        # 检查数据大小
        size_bytes = len(raw)
        if size_bytes > MAX_IMAGE_FILE_SIZE:
            raise SeedreamValidationError(
                f"数据过大: {size_bytes / 1024 / 1024:.1f}MB，最大支持10MB",
                field="image",
                value=data_uri,
            )

        # 验证图像像素维度约束
        try:
            with Image.open(io.BytesIO(raw)) as img:
                w, h = img.size

                # 宽高必须大于14像素
                if w < MIN_IMAGE_EDGE or h < MIN_IMAGE_EDGE:
                    raise SeedreamValidationError(
                        "图像宽高长度必须大于14px", field="image", value=data_uri
                    )

                # 宽高比限制在 1/16 到 16 之间
                ratio = w / h if h else 0
                if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
                    raise SeedreamValidationError(
                        "图像宽高比需在[1/16, 16]范围内", field="image", value=data_uri
                    )

                # 总像素不超过 6000×6000
                if w * h > MAX_IMAGE_PIXELS:
                    raise SeedreamValidationError(
                        "图像总像素不能超过 6000×6000", field="image", value=data_uri
                    )
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
