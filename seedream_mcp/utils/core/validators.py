"""Seedream MCP 参数校验模块。

集中处理图像生成各工具的入参校验，覆盖尺寸、水印、像素维度、参考图数量、
文件大小、宽高比、输出格式、提示词长度、组图总数与并行参数等。

设计要点：
- 模型能力数据驱动校验：与模型相关的规则，含尺寸档位、像素区间、倍数约束、输出
  格式、联网工具、流式输出、参考图上限、组图支持等，统一委托 model_capabilities
  的能力声明判定，新增模型只需扩展能力表而无需改动校验代码。
- 布尔字符串解析 parse_bool 与宽高比上下限常量由本模块单一持有，config 的
  _pick_bool 与 image_validation 的维度校验均为消费方。
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from .errors import SeedreamConfigError, SeedreamValidationError
from .logs import get_logger
from ..model.model_capabilities import get_max_reference_images, get_model_capabilities

logger = get_logger()


# ==================== 常量定义 ====================

# optimize_prompt_options.mode 的合法取值白名单。
VALID_OPTIMIZE_MODES = frozenset({"standard", "fast"})
# 尺寸预设档位与输出格式白名单。
VALID_SIZE_PRESETS = frozenset({"1K", "1.5K", "2K", "3K", "4K"})
VALID_OUTPUT_FORMATS = frozenset({"jpeg", "png"})
# 图片透明通道参数 background 的合法取值白名单，仅 5.0 Pro 图生图支持。
VALID_BACKGROUND_MODES = frozenset({"transparent", "opaque"})
# 布尔字符串解析的合法取值，parse_bool 据此判定真值与假值。
TRUE_BOOL_STRINGS = frozenset({"true", "1", "yes", "on"})
FALSE_BOOL_STRINGS = frozenset({"false", "0", "no", "off"})
# 图像宽高比上下限，输入参考图与输出尺寸校验共用；image_validation 由此导入，维持单一来源。
MIN_IMAGE_RATIO = 1 / 16
MAX_IMAGE_RATIO = 16
# 生成工具类型白名单，目前仅支持联网搜索；schemas.GenerationToolType 枚举的取值
# 集合以本常量为源，test_consistency_guards 守护两侧一致。
VALID_GENERATION_TOOL_TYPES = frozenset({"web_search"})
# 响应格式白名单，schemas.ResponseFormat 枚举的取值集合以本常量为源，同一守护测试
# 断言两侧一致。
VALID_RESPONSE_FORMATS = frozenset({"url", "b64_json"})
# 像素尺寸字符串正则：宽高各 2-5 位十进制。\d 匹配任意 Unicode 十进制数字，
# 非 ASCII 数字串同样命中，int() 可正常转换，行为良性并按此声明；IGNORECASE 使
# 分隔符接受大写 X，命中后输出统一归一为小写 x。
PIXEL_SIZE_PATTERN = re.compile(r"^(\d{2,5})x(\d{2,5})$", re.IGNORECASE)
# 组图总数上限：参考图数量与生成数量之和不超过 15，故参考图至多 14 张。
MAX_SEQUENTIAL_TOTAL_IMAGES = 15
# 并行生成上限：request_count 与 parallelism 共用此上界。
MAX_PARALLEL_REQUEST_COUNT = 10
# CJK 字符计数范围：CJK 表意文字各区与假名，覆盖生僻字与日文避免计数偏低；
# 仅影响超限告警计数，不参与任何放行判定。
CJK_CHAR_PATTERN = re.compile("[㐀-䶿一-鿿豈-﫿" "\U00020000-\U0003ffffぁ-ヿｦ-ﾟ]")


# 英文单词计数模式：字母串允许一个撇号连接，don't 计为一个单词。
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

# ==================== 底层私有工具函数 ====================


def _parse_pixel_size(size: str) -> tuple[int, int] | None:
    """解析像素尺寸字符串，命中返回 (宽, 高)，否则返回 None。"""
    matched = PIXEL_SIZE_PATTERN.fullmatch(size.strip())
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


def _preset_numeric_sort_key(preset: str) -> tuple[float, str]:
    """尺寸档位的排序键：按数值前缀升序，其次按字典序保证稳定。

    字典序会把 1.5K 排在 1K 之前，与档位的数值视觉顺序相反；数值前缀无法解析的
    档位排在末尾，不阻断排序。
    """
    try:
        return (float(preset.removesuffix("K")), preset)
    except ValueError:
        return (float("inf"), preset)


def _coerce_positive_int_in_range(value: Any, field: str, min_value: int, max_value: int) -> int:
    """将任意输入校验并转换为 [min_value, max_value] 内的整数。

    非法值抛出 SeedreamValidationError。
    """
    if isinstance(value, bool):
        raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)
    if isinstance(value, float):
        # 拒绝非整数浮点，避免静默截断造成语义偏差；整数浮点允许转换。
        if not value.is_integer():
            raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)
        validated_value = int(value)
    else:
        try:
            validated_value = int(value)
        except (ValueError, TypeError, OverflowError):
            raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)
        # Decimal/Fraction 经 int() 会静默截断小数，与 float 分支同规则拒绝非整数值；
        # 字符串经 int() 解析已保证无损转换，不参与等值比较。
        if not isinstance(value, str) and value != validated_value:
            raise SeedreamValidationError(f"{field} 必须是整数", field=field, value=value)

    if validated_value < min_value or validated_value > max_value:
        raise SeedreamValidationError(
            f"{field} 必须在 {min_value}-{max_value} 之间",
            field=field,
            value=validated_value,
        )
    return validated_value


# ==================== 基础公共验证函数 ====================


def parse_bool(value: object) -> bool:
    """将值解析为布尔，无法识别的取值显式报错而非静默当作 False。

    Args:
        value: 待解析的取值。

    Returns:
        bool 原样返回；true/yes/on/1 解析为 True，false/no/off/0 解析为 False；
        None 视为未配置并返回 False。

    Raises:
        SeedreamConfigError: 取值无法解析为布尔时抛出。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    if normalized in TRUE_BOOL_STRINGS:
        return True
    if normalized in FALSE_BOOL_STRINGS:
        return False
    raise SeedreamConfigError(f"无法解析为布尔值(期望 true/false/yes/no/on/off/1/0): {value!r}")


def validate_prompt(prompt: str, max_chinese_chars: int = 300, max_english_words: int = 600) -> str:
    """验证文本提示词的有效性和长度限制。

    中文字符超过 `max_chinese_chars` 或英文单词超过 `max_english_words` 时仅记录
    警告，不阻断调用。

    Args:
        prompt: 待校验的提示词文本。
        max_chinese_chars: 中文字符数告警阈值。
        max_english_words: 英文单词数告警阈值。

    Returns:
        去除首尾空白后的提示词文本。

    Raises:
        SeedreamValidationError: 提示词为空、非字符串或包含无法编码字符时抛出。
    """

    if not isinstance(prompt, str):
        raise SeedreamValidationError("提示词必须是字符串", field="prompt", value=prompt)
    if not prompt:
        raise SeedreamValidationError("提示词不能为空", field="prompt", value=prompt)

    prompt = prompt.strip()
    if not prompt:
        raise SeedreamValidationError("提示词不能为空", field="prompt", value=prompt)

    # MCP JSON 可合法传入未配对 UTF-16 代理字符的转义序列，此类文本无法 UTF-8 编码，
    # 若放行会在请求体序列化处才失败并呈现编码细节错误；在此提前以参数级提示拒绝。
    try:
        prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise SeedreamValidationError(
            "提示词包含无法编码的字符（如未配对的代理字符）", field="prompt", value=None
        )

    # 短文本粗筛：长度不超过中文阈值时两项计数必然在限内，跳过正则扫描避免物化
    # 大列表。计数扫描为全量 O(n)，超长提示词的扫描成本由调用侧
    # client._validate_common_generation_params 统一经工作线程执行，函数自身保持
    # 同步契约。
    chinese_count = 0
    english_word_count = 0
    if len(prompt) > max_chinese_chars:
        # subn 以替换计数取代 findall 物化匹配列表，超长中文提示词下仅一次分配。
        chinese_count = CJK_CHAR_PATTERN.subn("", prompt)[1]
        english_word_count = ENGLISH_WORD_PATTERN.subn("", prompt)[1]

    if chinese_count > max_chinese_chars or english_word_count > max_english_words:
        # 官方文档为「建议」而非硬限制，超限仅告警不阻断。
        logger.warning(
            "提示词较长（中文{}个/英文{}个），建议不超过{}个汉字或{}个英文单词，可能影响生成效果",
            chinese_count,
            english_word_count,
            max_chinese_chars,
            max_english_words,
        )

    return prompt


def validate_watermark(watermark: Any) -> bool:
    """验证水印参数，接受布尔或布尔字符串，返回标准化后的布尔值。

    布尔字符串经 parse_bool 解析，解析失败转抛 SeedreamValidationError 以保持
    校验层异常类型。
    """
    if isinstance(watermark, bool):
        return watermark

    if isinstance(watermark, str):
        try:
            return parse_bool(watermark)
        except SeedreamConfigError:
            raise SeedreamValidationError(
                "水印参数必须是布尔值或有效的字符串（true/false）",
                field="watermark",
                value=watermark,
            )

    raise SeedreamValidationError("水印参数必须是布尔值", field="watermark", value=watermark)


def validate_response_format(response_format: str) -> str:
    """验证响应格式参数，仅接受 url 或 b64_json，返回小写标准化值。"""
    if not isinstance(response_format, str):
        raise SeedreamValidationError(
            "response_format 必须为字符串", field="response_format", value=response_format
        )
    if not response_format:
        raise SeedreamValidationError(
            "response_format 不能为空", field="response_format", value=response_format
        )

    response_format = response_format.strip().lower()
    if response_format not in VALID_RESPONSE_FORMATS:
        raise SeedreamValidationError(
            f"response_format 必须是以下值之一: {sorted(VALID_RESPONSE_FORMATS)}",
            field="response_format",
            value=response_format,
        )

    return response_format


def validate_output_format(output_format: Any, model_id: str) -> str | None:
    """验证图像输出文件格式并检查模型兼容性。

    仅支持 jpeg/png；output_format 由 doubao-seedream-5.0 系列（5.0 Pro/5.0 Lite）
    支持，4.5/4.0 不支持，未知模型放行由能力表统一判定。输入为 None 时返回 None。

    Raises:
        SeedreamValidationError: output_format 非字符串、为空、取值不在 jpeg/png
            白名单，或模型能力声明不支持 output_format 时抛出。
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

    if not get_model_capabilities(model_id).supports_output_format:
        raise SeedreamValidationError(
            "仅 doubao-seedream-5.0 系列（5.0 Pro/5.0 Lite）模型支持 output_format",
            field="output_format",
            value=output_format,
        )

    return normalized


def validate_generation_tools(tools: Any, model_id: str) -> list[dict[str, str]] | None:
    """验证生成工具配置并检查模型兼容性。

    每项为仅含 type 字段的对象，type 仅支持 web_search；联网搜索由
    doubao-seedream-5.0 / 5.0-lite 系列支持，5.0 Pro/4.5/4.0 不支持，未知模型
    放行由能力表统一判定。输入为 None 时返回 None。

    Raises:
        SeedreamValidationError: tools 非数组、模型不支持联网工具、数组项非对象、
            项含 type 外字段，或项的 type 非字符串、为空、取值不在白名单时抛出。
    """
    if tools is None:
        return None

    if not isinstance(tools, list):
        raise SeedreamValidationError(
            "tools 必须是数组",
            field="tools",
            value=tools,
        )

    # 空列表等同不使用工具，跳过模型能力校验并归一化为 None，避免向 API 传空数组。
    if not tools:
        return None

    if not get_model_capabilities(model_id).supports_tools:
        raise SeedreamValidationError(
            "仅 doubao-seedream-5.0 系列（5.0/5.0-lite）支持 tools"
            "（5.0 Pro/4.5/4.0 不支持联网搜索）",
            field="tools",
            value=tools,
        )

    normalized_tools: list[dict[str, str]] = []
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
                f"tools[{index}] 包含不支持的字段: {sorted(extra_keys, key=repr)}",
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
    """验证流式输出参数与模型兼容性，原样返回 stream。

    5.0 Pro 不支持流式输出，5.0 系列（5.0/5.0-lite 同一模型）/4.5/4.0 支持。

    Raises:
        SeedreamValidationError: stream 非布尔，或为真而模型不支持流式输出时抛出。
    """
    if not isinstance(stream, bool):
        raise SeedreamValidationError("stream 必须为布尔值", field="stream", value=stream)

    caps = get_model_capabilities(model_id)
    if stream and not caps.supports_stream:
        raise SeedreamValidationError(
            f"{caps.display_name} 不支持流式输出（stream），请改用支持流式的模型",
            field="stream",
            value=stream,
        )
    return stream


def validate_max_images(max_images: Any) -> int:
    """验证最大图像数量参数，确保为 1-15 内的整数。

    委托 _coerce_positive_int_in_range 完成校验，与其他整数参数共享统一的错误格式。
    """
    return _coerce_positive_int_in_range(max_images, "max_images", 1, MAX_SEQUENTIAL_TOTAL_IMAGES)


# ==================== 尺寸验证函数 ====================


def _resolve_size_token(
    size: str, *, layer_decomposition: bool = False, model_id: str = ""
) -> str | tuple[int, int]:
    """校验尺寸输入形态并归一化，返回档位/auto 字符串标记或像素元组。

    Args:
        size: 原始尺寸输入。
        layer_decomposition: 是否处于图层拆分场景，true 时额外接受按输入图
            尺寸自适应的 "auto" 且拒绝宽高像素值。
        model_id: 模型标识符，图层拆分场景拒绝像素值时按该模型能力声明的档位
            白名单生成错误文案；缺省时按未知家族的全集档位表述。

    Returns:
        分辨率档位的大写字符串、图层拆分场景的小写 "auto" 或像素规格的
        (宽, 高) 元组。

    Raises:
        SeedreamValidationError: 输入非字符串、为空、形态非法，或图层拆分场景
            传入像素值时抛出。
    """
    if not isinstance(size, str):
        raise SeedreamValidationError("图像尺寸必须为字符串", field="size", value=size)
    if not size:
        raise SeedreamValidationError("图像尺寸不能为空", field="size", value=size)

    normalized = size.strip()
    if not normalized:
        raise SeedreamValidationError("图像尺寸不能为空", field="size", value=size)

    preset = normalized.upper()
    if preset in VALID_SIZE_PRESETS:
        return preset

    # 图层拆分场景的 auto 档：由模型按输入图尺寸与宽高比自适应输出，无像素校验。
    if layer_decomposition and normalized.lower() == "auto":
        return "auto"

    # 图层拆分场景仅支持分辨率档位与 auto，不支持宽高像素值方式；档位清单从模型
    # 能力声明动态派生，与档位放行判定保持同一数据来源。
    if layer_decomposition:
        presets_text = "/".join(
            sorted(
                get_model_capabilities(model_id).allowed_presets,
                key=_preset_numeric_sort_key,
            )
        )
        raise SeedreamValidationError(
            f"图层拆分场景的 size 仅支持分辨率档位（{presets_text}）或 auto",
            field="size",
            value=size,
        )

    pixel_size = _parse_pixel_size(normalized)
    if pixel_size is not None:
        return pixel_size

    raise SeedreamValidationError(
        "图像尺寸必须为 1K/1.5K/2K/3K/4K 或 <宽>x<高> 像素值",
        field="size",
        value=size,
    )


def validate_size_for_model(size: str, model_id: str, *, layer_decomposition: bool = False) -> str:
    """验证图像尺寸与模型的兼容性。

    尺寸规则由 model_capabilities 能力声明驱动：预设档位白名单 allowed_presets、
    像素总区间 min/max_size_pixels、倍数约束 size_pixel_multiple，如 5.0 Pro 要求
    宽高为 16 的倍数，新增模型只需扩展能力声明。图层拆分场景的 "auto" 仅校验
    模型支持图层拆分，不走档位与像素校验。

    Returns:
        标准化尺寸值：档位为大写，auto 为小写，像素规格归一为小写 x 分隔且
        剥离前导零。

    Raises:
        SeedreamValidationError: 尺寸形态非法、档位不在模型白名单、auto 而模型
            不支持图层拆分，或像素宽高非正、宽高比越界、总像素越界、宽高不是
            声明倍数时抛出。
    """
    token = _resolve_size_token(size, layer_decomposition=layer_decomposition, model_id=model_id)
    caps = get_model_capabilities(model_id)

    if isinstance(token, str):
        if token == "auto":
            if not caps.supports_layer_decomposition:
                raise SeedreamValidationError(
                    f"{caps.display_name} 模型不支持图层拆分，size 不接受 auto",
                    field="size",
                    value=token,
                )
            return token

        if token not in caps.allowed_presets:
            presets_str = "/".join(sorted(caps.allowed_presets, key=_preset_numeric_sort_key))
            raise SeedreamValidationError(
                f"在 {caps.display_name} 模型下仅支持 {presets_str}，"
                "请调整 size 参数或更换为支持该尺寸的模型",
                field="size",
                value=token,
            )
        return token

    # 像素值校验：宽高元组由 _resolve_size_token 单次解析后贯穿校验链，前导零
    # 输入按数值参与判定，不因归一化字符串破坏重解析而误报格式错误。
    width, height = token
    pixel_text = f"{width}x{height}"
    if width <= 0 or height <= 0:
        raise SeedreamValidationError(
            "像素尺寸的宽与高必须为正整数", field="size", value=pixel_text
        )

    ratio = width / height
    if ratio < MIN_IMAGE_RATIO or ratio > MAX_IMAGE_RATIO:
        raise SeedreamValidationError(
            f"尺寸宽高比需在 [{MIN_IMAGE_RATIO}, {MAX_IMAGE_RATIO}] 范围内",
            field="size",
            value=pixel_text,
        )

    total_pixels = width * height
    # 像素区间按单边独立判定：能力表仅声明 min 或仅声明 max 时，对应单边约束独立生效。
    min_pixels = caps.min_size_pixels
    max_pixels = caps.max_size_pixels
    below_min = min_pixels is not None and total_pixels < min_pixels
    above_max = max_pixels is not None and total_pixels > max_pixels
    if below_min or above_max:
        if min_pixels is not None and max_pixels is not None:
            bound_text = f"在 [{min_pixels}, {max_pixels}] 范围内"
        elif min_pixels is not None:
            bound_text = f"不低于 {min_pixels}"
        else:
            bound_text = f"不超过 {max_pixels}"
        raise SeedreamValidationError(
            f"在 {caps.display_name} 模型下，像素尺寸总像素需{bound_text}",
            field="size",
            value=pixel_text,
        )
    if caps.size_pixel_multiple is not None and (
        width % caps.size_pixel_multiple != 0 or height % caps.size_pixel_multiple != 0
    ):
        raise SeedreamValidationError(
            f"在 {caps.display_name} 模型下，像素宽高须为 {caps.size_pixel_multiple} 的倍数",
            field="size",
            value=pixel_text,
        )

    return pixel_text


# ==================== 高级验证函数 ====================


def validate_layer_decomposition(layer_decomposition: Any, model_id: str) -> bool:
    """验证图层拆分开关与模型的兼容性。

    图层拆分将单张输入图拆解为 1 张底图与最多 16 个带透明通道的 PNG 图层，仅
    5.0 Pro 支持，未知模型放行由能力表统一判定；单张参考图输入的前提由
    image_to_image 工具的输入形态保证。None 视为未启用返回 False。

    Raises:
        SeedreamValidationError: layer_decomposition 非布尔，或为真而模型不支持
            图层拆分时抛出。
    """
    if layer_decomposition is None:
        return False
    if not isinstance(layer_decomposition, bool):
        raise SeedreamValidationError(
            "layer_decomposition 必须为布尔值",
            field="layer_decomposition",
            value=layer_decomposition,
        )
    caps = get_model_capabilities(model_id or "")
    if layer_decomposition and not caps.supports_layer_decomposition:
        raise SeedreamValidationError(
            f"{caps.display_name} 模型不支持 layer_decomposition 图层拆分",
            field="layer_decomposition",
            value=layer_decomposition,
        )
    return layer_decomposition


def validate_background(
    background: Any, model_id: str, output_format: str | None = None
) -> str | None:
    """验证图片透明通道参数与模型的兼容性。

    background 控制是否生成带透明通道的图片，仅 5.0 Pro 图生图支持，未知模型
    放行由能力表统一判定。输入图的格式约束由上游校验，此处做值域、模型门控与
    output_format 互斥校验；透明背景输出为带 alpha 的 png，与 jpeg 互斥同时
    指定时报错。

    Raises:
        SeedreamValidationError: background 非字符串或取值不在 transparent/opaque
            白名单、模型不支持透明通道、output_format 非字符串，或 transparent
            与 output_format=jpeg 同时指定时抛出。
    """
    if background is None:
        return None
    if not isinstance(background, str):
        raise SeedreamValidationError(
            "background 必须为字符串", field="background", value=background
        )
    normalized = background.strip().lower()
    if normalized not in VALID_BACKGROUND_MODES:
        raise SeedreamValidationError(
            f"background 必须为 {sorted(VALID_BACKGROUND_MODES)}",
            field="background",
            value=background,
        )
    caps = get_model_capabilities(model_id or "")
    if not caps.supports_background:
        raise SeedreamValidationError(
            f"{caps.display_name} 模型不支持 background 透明通道参数",
            field="background",
            value=normalized,
        )
    if output_format is not None:
        if not isinstance(output_format, str):
            raise SeedreamValidationError(
                "output_format 必须为字符串", field="output_format", value=output_format
            )
        if normalized == "transparent" and output_format.strip().lower() == "jpeg":
            raise SeedreamValidationError(
                "透明背景输出为 png，background=transparent 与 output_format=jpeg 互斥",
                field="background",
                value=normalized,
            )
    return normalized


def validate_optimize_prompt_options(options: Any, model_id: str) -> dict[str, Any] | None:
    """验证提示词优化选项并检查模型兼容性，可配置字段仅 mode，取值 standard/fast。

    Raises:
        SeedreamValidationError: options 非对象、含 mode 外字段、mode 非字符串或
            不在 standard/fast 白名单，或模型仅支持 standard 而传入 fast 时抛出。
    """
    if options is None:
        return None

    if not isinstance(options, dict):
        raise SeedreamValidationError(
            "optimize_prompt_options 必须为对象", field="optimize_prompt_options", value=options
        )

    # 未知字段显式拒绝而非静默丢弃；schemas 的 extra="forbid" 已覆盖工具层，
    # 此处补齐公共 client 直调路径。
    extra_keys = set(options.keys()) - {"mode"}
    if extra_keys:
        raise SeedreamValidationError(
            f"optimize_prompt_options 包含不支持的字段: {sorted(extra_keys, key=repr)}",
            field="optimize_prompt_options",
            value=options,
        )

    mode = options.get("mode", "standard")
    if not isinstance(mode, str):
        raise SeedreamValidationError(
            "optimize_prompt_options.mode 必须为字符串",
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
    """校验并行生成参数组合，返回规范化后的 (request_count, parallelism)。

    未指定 parallelism 时取 min(request_count, max_request_count)；parallelism
    不得超过 request_count；stream 为真时 request_count 必须为 1。
    max_request_count 为两者公共上界。

    Raises:
        SeedreamValidationError: request_count 或 parallelism 非整数、越出公共上界
            区间、parallelism 大于 request_count，或 stream 为真而 request_count
            大于 1 时抛出。
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


def validate_sequential_generation_support(model_id: str) -> None:
    """验证模型对组图生成的支持。

    组图由 5.0/5.0 Lite/4.5/4.0 支持，5.0 Pro 不支持，由能力表统一判定。

    Args:
        model_id: 模型标识符。

    Raises:
        SeedreamValidationError: 模型能力声明不支持组图生成时抛出。
    """
    caps = get_model_capabilities(model_id)
    if not caps.supports_sequential_generation:
        raise SeedreamValidationError(
            f"{caps.display_name} 不支持组图生成，请切换为支持组图的模型",
            field="model",
            value=model_id,
        )


def validate_sequential_image_limit(
    max_images: int, reference_images: list[str] | None, model_id: str = ""
) -> None:
    """验证组图输出的总图片数量限制。

    参考图数量不超过模型能力上限（5.0 Pro 为 10，其余家族为 14），且与生成数量
    之和不超过 15。model_id 缺省时按通用上限粗校验，供无模型上下文的 schema 层
    使用；精确校验由 client 层传入实际 model_id 完成。

    Raises:
        SeedreamValidationError: 参考图数量超过模型能力上限，或与生成数量之和
            超过 15 时抛出。
    """
    max_reference = get_max_reference_images(model_id)
    reference_count = len(reference_images) if reference_images else 0
    if reference_count > max_reference:
        raise SeedreamValidationError(
            f"参考图数量不能超过{max_reference}，"
            f"且参考图数量与生成数量之和不能超过{MAX_SEQUENTIAL_TOTAL_IMAGES}",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )

    if reference_count + max_images > MAX_SEQUENTIAL_TOTAL_IMAGES:
        raise SeedreamValidationError(
            f"参考图数量与生成数量之和不能超过{MAX_SEQUENTIAL_TOTAL_IMAGES}"
            f"（参考图最多{max_reference}）",
            field="image",
            value={"reference_images": reference_count, "max_images": max_images},
        )


def resolve_sequential_max_images(
    max_images: int | None,
    reference_images: list[str] | None = None,
) -> int:
    """根据参考图数量推导组图最大生成数量。

    未显式指定 max_images 时默认为 15 - len(reference_images)，保证「参考图数量 +
    生成数量 <= 15」。
    """
    if max_images is not None:
        return max_images
    reference_count = len(reference_images) if reference_images else 0
    return MAX_SEQUENTIAL_TOTAL_IMAGES - reference_count


class ValidatedCommonParams(NamedTuple):
    """生成类工具公共参数的校验结果，供 context 与 client 共享单一校验入口。

    Attributes:
        prompt: 校验后的提示词文本；图层拆分场景未提供提示词时为 None。
        optimize_prompt_options: 校验后的提示词优化选项，未指定时为 None。
        size: 校验后的尺寸规格。
        watermark: 标准化后的水印开关。
        response_format: 小写标准化后的响应格式。
        output_format: 小写标准化后的输出格式，未指定时为 None。
        stream: 校验通过后的流式输出开关。
        tools: 校验后的生成工具数组，未指定时为 None。
    """

    prompt: str | None
    optimize_prompt_options: dict[str, Any] | None
    size: str
    watermark: bool
    response_format: str
    output_format: str | None
    stream: bool
    tools: list[dict[str, Any]] | None


def validate_common_generation_params(
    *,
    prompt: str | None,
    optimize_prompt_options: dict[str, Any] | None,
    size: str,
    watermark: bool,
    response_format: str,
    output_format: str | None,
    stream: bool,
    tools: list[dict[str, Any]] | None,
    model_id: str,
    layer_decomposition: bool = False,
) -> ValidatedCommonParams:
    """集中校验生成类工具的公共参数并返回校验后的各值，字段语义见 ValidatedCommonParams。

    供 client 生成方法入口做公共库 API 自校验：新增公共参数校验规则只需在此扩展，
    调用方自动受益；工具层的值域校验由 schemas.py 的 Field 约束承担，与本函数构成
    defense-in-depth。图层拆分场景下 prompt 允许缺省、size 额外接受 auto。

    Raises:
        SeedreamValidationError: 非图层拆分场景缺省 prompt，或任一公共参数在其
            对应单项校验中非法时抛出。
    """
    if prompt is None and not layer_decomposition:
        raise SeedreamValidationError("prompt 不能为空", field="prompt", value=None)
    return ValidatedCommonParams(
        prompt=validate_prompt(prompt) if prompt is not None else None,
        optimize_prompt_options=validate_optimize_prompt_options(optimize_prompt_options, model_id),
        size=validate_size_for_model(size, model_id, layer_decomposition=layer_decomposition),
        watermark=validate_watermark(watermark),
        response_format=validate_response_format(response_format),
        output_format=validate_output_format(output_format, model_id),
        stream=validate_stream(stream, model_id),
        tools=validate_generation_tools(tools, model_id),
    )
