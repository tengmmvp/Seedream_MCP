"""Seedream MCP 工具结构化输出模型。

作为 outputSchema 的单一来源：MCPServer 依据本模块模型生成各工具的 structuredContent
schema，runtime 输出也须经模型构造后 model_dump，使声明与实际输出绑定、不漂移。
build_error_dict 与 build_error_structured 收敛各错误分支的错误结构；
build_structured_json_text 将 structuredContent 序列化为紧凑 JSON 的 TextContent，
字符串值继承摘要通道净化、二进制载荷替换为长度占位；
dump_compact_strict_json 是紧凑严格 JSON 的单一序列化原语，Web 控制台的结构化
JSON 同源委托；
build_structured_tool_result 把镜像块回传进 content 数组，是「摘要+镜像（+尾部块）」
结果组装的单一构造点。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from mcp.types import CallToolResult, ContentBlock, TextContent
from pydantic import BaseModel, ConfigDict, field_validator

from ._sanitize import _unique_flat_key
from ...utils.core.executors import run_in_cpu_pool, should_offload_to_cpu_pool
from ...utils.core.logs import get_logger
from ...utils.core.sanitizers import (
    sanitize_data_text,
    strip_unpaired_surrogates,
    utf8_value_leaf_length,
)

logger = get_logger()


class _BaseStructuredOutput(BaseModel):
    """结构化输出基类。

    声明所有工具共有字段。顶层字段全部为本侧装配自构，forbid 使声明与实际输出强
    绑定，多写或拼错顶层键在构造期即暴露；上游透传内容位于 data/usage 等声明为
    Any 的嵌套字段内，不受顶层封闭约束影响。

    Attributes:
        status: 执行状态标签，如 completed、failed 或 empty，未携带时为 None。
        error: 结构化错误载荷，含 type 与 message 两键，上游携带错误码时另含 code
            键；无错误时为 None。
    """

    model_config = ConfigDict(extra="forbid")

    tool: str
    success: bool
    status: str | None = None
    error: dict[str, Any] | None = None


def _stringify_keys(mapping: dict[Any, Any]) -> dict[str, Any]:
    """非字符串键字符串化，键压平与碰撞后缀委托 _unique_flat_key 单点；全字符串键原样返回。"""
    if all(isinstance(key, str) for key in mapping):
        return mapping
    stringified: dict[str, Any] = {}
    for key, value in mapping.items():
        text_key = _unique_flat_key(stringified, key if isinstance(key, str) else str(key))
        stringified[text_key] = value
    return stringified


class GenerationStructuredOutput(_BaseStructuredOutput):
    """生成类工具的结构化输出 schema，覆盖文生图、图文生图、多图融合与组图输出。

    参数回显字段序与生成工具的输入参数序一致。

    Attributes:
        prompt: 生成提示词回显；图文生图的图层拆分场景可为 None。
        optimize_prompt_options: 生效的提示词优化选项，未指定时为 None。
        layer_decomposition: 是否开启图层拆分，非 False 取值仅出现在图文生图。
        background: 透明通道取值，非 None 取值仅出现在图文生图显式指定时。
        size: 生效的生成尺寸。
        watermark: 生效的水印开关。
        max_images: 组图单次请求的生成数量上限，未显式传入时为按参考图数量推导的
            生效值；非组图工具为 None。
        response_format: 响应格式，url 或 b64_json。
        output_format: 输出图片格式，未指定时为 None。
        stream: 是否启用流式输出。
        tools: 模型工具配置，未指定时为 None。
        request_count: 同一提示并行发起的独立生成次数。
        parallelism: 并行度上限。
        data: 图片条目列表，条目含 url 或 b64_json 及自动保存回填的本地路径信息；
            图层拆分场景条目另含 z_index、name、description、bounding_box 字段。
        usage: 用量统计字典，键由上游透传；部分模型另含 input_images 输入图片数。
        batch: 并行批次统计，单次请求时为 None。
        auto_save: 自动保存摘要，未启用时仅含 enabled 键。
        truncated_events: SSE 解析因超限或解析失败丢弃的事件数，未发生丢弃时为
            None。
        deadline_exceeded: SSE 流因总时长预算超限提前终止且保留了已收结果，未
            发生时为 None。
    """

    prompt: str | None = None
    optimize_prompt_options: dict[str, Any] | None = None
    layer_decomposition: bool | None = None
    background: str | None = None
    size: str | None = None
    watermark: bool | None = None
    max_images: int | None = None
    response_format: str | None = None
    output_format: str | None = None
    stream: bool | None = None
    tools: list[dict[str, Any]] | None = None
    request_count: int | None = None
    parallelism: int | None = None
    data: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    batch: dict[str, Any] | None = None
    auto_save: dict[str, Any] | None = None
    truncated_events: int | None = None
    deadline_exceeded: bool | None = None

    @field_validator("data", mode="before")
    @classmethod
    def _stringify_data_item_keys(cls, value: Any) -> Any:
        """图片条目的非字符串键在核心校验前字符串化，净化层保留的此类键不使构造抛错。"""
        if not isinstance(value, list):
            return value
        return [_stringify_keys(item) if isinstance(item, dict) else item for item in value]

    @field_validator("usage", "batch", "error", mode="before")
    @classmethod
    def _stringify_mapping_keys(cls, value: Any) -> Any:
        """dict 字段的非字符串键在核心校验前字符串化，净化层保留的此类键不使构造抛错。"""
        if not isinstance(value, dict):
            return value
        return _stringify_keys(value)


class BrowseImagesStructuredOutput(_BaseStructuredOutput):
    """图片浏览工具的结构化输出 schema。

    Attributes:
        directory: 用户请求的目录字符串，未提供时归一为当前目录 "."。
        resolved_directories: 实际解析并扫描的目录列表，正斜杠绝对路径。
        workspace_roots: 工作区根回显，正斜杠绝对路径。
        count: 当前页返回的图片条数。
        total_count: 全量匹配图片总数，未扫完全量时为 None；与表达当前页条数的
            count 分页语义不同。
        offset: 当前页起始偏移。
        has_more: 是否仍有未返回的匹配图片。
        next_offset: 下一页起始偏移，无更多图片时为 None。
        images: 当前页图片条目，含 index 与 path，可选 size_mb 与 modified。
        recursive: 是否递归查找子目录。
        max_depth: 递归查找的最大深度。
        limit: 单页返回的最大文件数量。
        format_filter: 生效的图片扩展名过滤列表，未提供时为 None。
        show_details: 图片条目是否包含文件大小与修改时间详情。
    """

    directory: str | None = None
    resolved_directories: list[str] | None = None
    workspace_roots: list[str] | None = None
    count: int | None = None
    total_count: int | None = None
    offset: int | None = None
    has_more: bool | None = None
    next_offset: int | None = None
    images: list[dict[str, Any]] | None = None
    recursive: bool | None = None
    max_depth: int | None = None
    limit: int | None = None
    format_filter: list[str] | None = None
    show_details: bool | None = None


class GetModelInfoStructuredOutput(_BaseStructuredOutput):
    """模型信息工具的结构化输出 schema。

    Attributes:
        model_id: 当前配置的模型标识，别名已在配置层归一为 Model ID。
        display_name: 家族展示名，未识别家族为 None。
        aliases: 归一到当前模型 ID 的全部友好别名。
        family: 模型家族规范名，未识别家族为 unknown。
        capabilities: 能力字段，按调用流程排序：optimize_prompt_modes、
            max_reference_images、layer_decomposition、transparent_background、
            size_presets、min_size_pixels、max_size_pixels、output_format、stream、
            web_search、sequential_generation。
    """

    model_id: str | None = None
    display_name: str | None = None
    aliases: list[str] | None = None
    family: str | None = None
    capabilities: dict[str, Any] | None = None


def build_error_dict(error_type: str, message: str) -> dict[str, Any]:
    """构建结构化错误载荷，各工具错误分支共用同一字段集。

    Args:
        error_type: 载荷的 type 取值，为归约档案错误码或浏览工具的 browse_failed。
        message: 面向用户的错误消息。

    Returns:
        含 type 与 message 两键的错误字典。
    """
    return {"type": error_type, "message": message}


def build_error_structured(
    tool_name: str,
    error_type: str,
    message: str,
    status: str = "failed",
) -> dict[str, Any]:
    """构建失败路径的 structuredContent 并绑定声明 schema。

    dump 策略与流水线失败分支一致：全字段输出、未赋值字段以 None 填充，异常兜底与
    流水线失败两类错误分支的字段集相同，消费方无需按错误来源区分断言。

    Args:
        tool_name: 工具标识。
        error_type: 归约档案错误码，作为 error.type 取值。
        message: 面向用户的错误消息。
        status: 结构化输出的 status 取值，默认 failed。

    Returns:
        失败路径的 structuredContent 字典，全字段输出。
    """
    return GenerationStructuredOutput(
        tool=tool_name,
        success=False,
        status=status,
        error=build_error_dict(error_type, message),
    ).model_dump()


# 二进制载荷键集合，镜像占位替换与卸载门控的长度计量共用。
_BINARY_PAYLOAD_KEYS = frozenset({"b64_json"})


def format_base64_placeholder(length: int) -> str:
    """返回 b64 载荷的长度占位文案，镜像与文本摘要两通道共用同一措辞。"""
    return f"Base64 数据: {length} 字符"


def _mirror_string(key: Any, value: str) -> str:
    """镜像字符串值：载荷键取长度占位，其余经数据通道净化，干净文本由净化入口的恒等快路直接透传。"""
    if key in _BINARY_PAYLOAD_KEYS:
        return format_base64_placeholder(len(value))
    return sanitize_data_text(value)


def _mirror_node(key: Any, value: Any) -> Any:
    """重建镜像视图：字符串值占位或净化，容器无条件新建，未知类型拒绝。

    dict/list/str 与 JSON 标量之外的容器类型可能携带绕过净化与卸载门控的嵌套
    字符串，一律拒绝，与极端深树的 RecursionError 同样由组装点降级为无镜像
    结果；键序随遍历保持与原树一致，原树不被修改。
    """
    if isinstance(value, dict):
        return {sub_key: _mirror_node(sub_key, item) for sub_key, item in value.items()}
    if isinstance(value, list):
        return [_mirror_node(None, item) for item in value]
    if isinstance(value, str):
        return _mirror_string(key, value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError(f"镜像不支持 {type(value).__name__} 类型的值")


def dump_compact_strict_json(value: Any) -> str:
    """紧凑严格 JSON 序列化：非 ASCII 原文输出、分隔符省空白、非有限浮点抛 ValueError。

    模型可见镜像与 Web 控制台结构化 JSON 共用本函数，序列化形态单源不漂移。
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def build_structured_json_text(structured: dict[str, Any]) -> TextContent:
    """将 structuredContent 序列化为紧凑 JSON 的 TextContent，字符串值净化、二进制载荷换长度占位。

    规范建议返回 structuredContent 的工具同时在 content 中回传序列化 JSON，
    仅消费 content 的客户端也能取得完整结构化结果；紧凑分隔符省 token。镜像的
    全部字符串值继承摘要通道的数据净化口径（敏感键值掩码、控制字符压平与防御性
    截断），未经出口净化的回显字段不借镜像进入模型可见通道；b64_json 字符串
    载荷以「Base64 数据: N 字符」占位，与文本摘要口径一致且不限阈值，防镜像
    把完整 base64 复制进模型上下文；structuredContent 字段本身不受影响。非有限
    浮点使序列化按严格 JSON 抛 ValueError，由组装点降级为无镜像结果，镜像块
    不产出裸 NaN/Infinity。

    Args:
        structured: 工具结果的 structuredContent 字典。

    Returns:
        承载序列化 JSON 的文本内容块。
    """
    mirror = _mirror_node(None, structured)
    return TextContent(
        type="text",
        text=dump_compact_strict_json(mirror),
    )


def binary_placeholder_value_leaf_length(key: Any, value: Any, limit: int) -> int | None:
    """二进制载荷键按占位长度、其余字符串经 UTF-8 字节钩子计量，非字符串返回 None 走默认计量，镜像构建门控使用。"""
    if isinstance(value, str):
        if key in _BINARY_PAYLOAD_KEYS:
            return len(format_base64_placeholder(len(value)))
        return utf8_value_leaf_length(key, value, limit)
    return None


def _strip_result_payload_surrogates(value: Any) -> Any:
    """迭代剥离结构化载荷字符串与键的未配对代理，干净子树保持原引用。

    显式栈后序遍历不消耗递归配额，极端深树不再使装配点抛 RecursionError；变更
    子级记入所属容器的补丁表，补丁为空的子树返回原容器引用，仅含变更的分支
    物化新容器。
    """
    if isinstance(value, str):
        return strip_unpaired_surrogates(value)
    if not isinstance(value, (dict, list)):
        return value

    # 容器帧为 (原容器, 补丁表) 二元组：list 补丁按下标存新值，dict 补丁按原始键存
    # (剥离键, 新值)，空补丁表即整个容器未变更。
    root_result: Any = value
    stack: list[tuple[Any, Any, Any, Any]] = [(None, None, None, value)]
    while stack:
        parent, slot, write_key, node = stack.pop()
        if isinstance(node, tuple):
            original, patches = node
            result: Any
            if not patches:
                result = original
            elif isinstance(original, list):
                result = list(original)
                for index, new_value in patches.items():
                    result[index] = new_value
            else:
                result = {}
                for key, item in original.items():
                    new_key, new_value = patches.get(key, (key, item))
                    result[new_key] = new_value
            if parent is None:
                root_result = result
            elif result is not original or write_key is not slot:
                parent_original, parent_patches = parent
                if isinstance(parent_original, dict):
                    parent_patches[slot] = (write_key, result)
                else:
                    parent_patches[slot] = result
            continue
        frame: tuple[Any, dict[Any, Any]] = (node, {})
        stack.append((parent, slot, write_key, frame))
        patches = frame[1]
        if isinstance(node, dict):
            for key, item in node.items():
                stripped_key = strip_unpaired_surrogates(key) if isinstance(key, str) else key
                if isinstance(item, str):
                    stripped_item = strip_unpaired_surrogates(item)
                    if stripped_key is not key or stripped_item is not item:
                        patches[key] = (stripped_key, stripped_item)
                elif isinstance(item, (dict, list)):
                    stack.append((frame, key, stripped_key, item))
                elif stripped_key is not key:
                    patches[key] = (stripped_key, item)
        else:
            for index, item in enumerate(node):
                if isinstance(item, str):
                    stripped_item = strip_unpaired_surrogates(item)
                    if stripped_item is not item:
                        patches[index] = stripped_item
                elif isinstance(item, (dict, list)):
                    stack.append((frame, index, index, item))
    return root_result


async def build_structured_tool_result(
    message: str,
    structured: dict[str, Any],
    *,
    is_error: bool,
    trailing: Sequence[ContentBlock] = (),
) -> CallToolResult:
    """组装携带 structuredContent 的工具结果，content 为「摘要 + JSON 镜像 + 尾部块」。

    规范建议返回 structuredContent 的工具同时在 content 中回传序列化 JSON；本函数是
    该不变量的单一组装点，全部工具出口共用，尾部块承载缩略图预览等内容。摘要文本
    与结构化载荷共享严格 JSON 出口，未配对代理在装配点统一剥离，含代理的文件名等
    保留原文内容不再使整个响应翻为序列化失败。载荷尺寸估算达到卸载阈值时镜像构建
    的树遍历、净化与序列化下沉专用 CPU 线程池执行，小载荷内联构建免线程往返；两条
    路径失败时同样降级为无镜像结果并记录告警，is_error 取值保持不变。

    Args:
        message: 用户可见的摘要文本，作为首个文本块。
        structured: 工具结果的 structuredContent 字典。
        is_error: 工具结果的错误标记。
        trailing: 追加在 JSON 镜像块之后的内容块。

    Returns:
        组装完成的工具结果。
    """
    sanitized_message = strip_unpaired_surrogates(message)
    sanitized_structured = _strip_result_payload_surrogates(structured)
    blocks: list[ContentBlock] = [TextContent(type="text", text=sanitized_message)]
    try:
        if should_offload_to_cpu_pool(
            sanitized_structured, value_leaf_cost=binary_placeholder_value_leaf_length
        ):
            mirror = await run_in_cpu_pool(build_structured_json_text, sanitized_structured)
        else:
            mirror = build_structured_json_text(sanitized_structured)
    except Exception:
        logger.opt(exception=True).warning("结构化镜像构建失败，降级为无镜像结果")
    else:
        blocks.append(mirror)
    return CallToolResult(
        content=[*blocks, *trailing],
        structured_content=sanitized_structured,
        is_error=is_error,
    )
