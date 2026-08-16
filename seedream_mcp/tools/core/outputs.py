"""Seedream MCP 工具结构化输出模型。

作为 outputSchema 的单一来源：FastMCP 依据本模块的 pydantic 模型生成各工具的
structuredContent schema；runtime 的 structuredContent 也须经本模块模型构造后
model_dump，使声明 schema 与实际输出绑定、不漂移。基类通过 extra='allow' 容纳
API 透传的新字段以保持向前兼容；build_error_dict 与 build_error_structured 收敛
各错误分支的错误结构。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _BaseStructuredOutput(BaseModel):
    """结构化输出基类。

    声明所有工具共有的字段，并通过 extra='allow' 允许额外字段以向前兼容 API 透传的
    新字段。客户端解析 structuredContent 时应对未列出的字段容错。

    Attributes:
        tool: 产生该结果的工具标识。
        success: 工具执行是否成功。
        status: 执行状态标签，如 completed、failed 或 empty，未携带时为 None。
        error: 结构化错误载荷，含 type 与 message 两键，无错误时为 None。
    """

    model_config = ConfigDict(extra="allow")

    tool: str
    success: bool
    status: str | None = None
    error: dict[str, Any] | None = None


class GenerationStructuredOutput(_BaseStructuredOutput):
    """生成类工具的结构化输出 schema，覆盖文生图、图文生图、多图融合与组图输出。

    Attributes:
        prompt: 生成使用的提示词。
        size: 生成图片的尺寸规格。
        response_format: 响应格式，取值为 url 或 b64_json。
        output_format: 输出图片格式，未指定时为 None。
        stream: 是否启用流式输出。
        tools: 模型工具配置列表，未启用时为 None。
        request_count: 本批次请求数量。
        parallelism: 本批次并行度上限。
        data: 图片条目列表，条目含 url 或 b64_json 及自动保存回填的本地路径信息。
        usage: 用量统计字典，键由上游透传。
        batch: 并行批次统计，单次请求时为 None。
        auto_save: 自动保存摘要，未启用时仅含 enabled 键。
        truncated_events: SSE 解析因单事件体积超限丢弃的事件数，未发生丢弃时为
            None；字段随本模型自动进入声明的 outputSchema，FastMCP 声明端无需额外动作。
    """

    prompt: str | None = None
    size: str | None = None
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


class BrowseImagesStructuredOutput(_BaseStructuredOutput):
    """图片浏览工具的结构化输出 schema。

    Attributes:
        directory: 用户请求的目录字符串，未提供时归一为当前目录 "."。
        resolved_directories: 实际解析并扫描的目录列表；边界来自回退配置时为占位符回显。
        workspace_roots: 工作区根回显；边界来自回退配置时为占位符回显。
        count: 当前页返回的图片条数。
        total_count: 匹配图片总数，未扫完全量时为 None。
        offset: 当前页起始偏移。
        has_more: 是否仍有未返回的图片。
        next_offset: 下一页起始偏移，无更多图片时为 None。
        images: 当前页图片条目，含 index 与 path，可选 size_mb 与 modified。
        recursive: 是否递归扫描子目录。
        max_depth: 递归扫描的最大深度。
        limit: 单页条数上限。
        show_details: 条目是否携带文件大小与修改时间详情。
        format_filter: 生效的图片扩展名过滤列表，未提供时为 None。
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
    show_details: bool | None = None
    format_filter: list[str] | None = None


def build_error_dict(error_type: str, message: str) -> dict[str, Any]:
    """构建结构化错误载荷，各工具错误分支共用同一字段集。

    error_type 传入 errors 模块 resolve_error_profile 归约出的 error_code，使
    structuredContent.error.type 与单发、并发、浏览各路径的错误码契约一致。

    Args:
        error_type: 归约档案错误码，作为载荷的 type 取值。
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

    经 GenerationStructuredOutput 构造后 model_dump。dump 策略与
    results._build_generation_structured_result 的失败分支一致：全字段输出、未赋值
    字段以 None 填充，使异常兜底与流水线失败两类错误分支的字段集相同，消费方无需按
    错误来源区分字段断言。

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
