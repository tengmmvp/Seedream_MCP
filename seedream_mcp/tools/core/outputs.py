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
    """

    model_config = ConfigDict(extra="allow")

    tool: str
    success: bool
    status: str | None = None
    error: dict[str, Any] | None = None


class GenerationStructuredOutput(_BaseStructuredOutput):
    """生成类工具的结构化输出 schema，覆盖文生图、图文生图、多图融合与组图输出。"""

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


class BrowseImagesStructuredOutput(_BaseStructuredOutput):
    """图片浏览工具的结构化输出 schema。"""

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
    """
    return {"type": error_type, "message": message}


def build_error_structured(
    tool_name: str,
    error_type: str,
    message: str,
    status: str = "failed",
) -> dict[str, Any]:
    """构建失败路径的 structuredContent，经 GenerationStructuredOutput 与声明 schema 绑定。

    仅输出已赋值字段，字段集与既有错误兜底分支一致，均为必填的 tool、success、
    status 与 error。
    """
    return GenerationStructuredOutput(
        tool=tool_name,
        success=False,
        status=status,
        error=build_error_dict(error_type, message),
    ).model_dump(exclude_none=True)
