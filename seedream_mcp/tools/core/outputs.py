"""Seedream MCP 工具结构化输出模型。

作为 outputSchema 的单一来源：FastMCP 依据本模块的 pydantic 模型生成各工具的
structuredContent schema，handler 仅负责填充字段。基类通过 extra='allow' 容纳 API
透传的新字段以保持向前兼容。
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
