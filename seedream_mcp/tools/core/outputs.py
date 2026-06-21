"""
Seedream MCP 工具结构化输出模型
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class _BaseStructuredOutput(BaseModel):
    """结构化输出基类：所有工具共有的字段 + 允许额外字段以向前兼容。"""

    model_config = ConfigDict(extra="allow")

    tool: str
    success: bool
    status: Optional[str] = None
    error: Optional[Dict[str, Any]] = None


class GenerationStructuredOutput(_BaseStructuredOutput):
    """生成类工具（文生图/图文生图/多图融合/组图输出）的结构化输出 schema。"""

    prompt: Optional[str] = None
    size: Optional[str] = None
    response_format: Optional[str] = None
    output_format: Optional[str] = None
    stream: Optional[bool] = None
    tools: Optional[List[Dict[str, Any]]] = None
    request_count: Optional[int] = None
    parallelism: Optional[int] = None
    data: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, Any]] = None
    batch: Optional[Dict[str, Any]] = None
    auto_save: Optional[Dict[str, Any]] = None


class BrowseImagesStructuredOutput(_BaseStructuredOutput):
    """图片浏览工具的结构化输出 schema。"""

    directory: Optional[str] = None
    resolved_directories: Optional[List[str]] = None
    workspace_roots: Optional[List[str]] = None
    count: Optional[int] = None
    total_count: Optional[int] = None
    offset: Optional[int] = None
    has_more: Optional[bool] = None
    next_offset: Optional[int] = None
    images: Optional[List[Dict[str, Any]]] = None
    recursive: Optional[bool] = None
    max_depth: Optional[int] = None
    limit: Optional[int] = None
    show_details: Optional[bool] = None
    format_filter: Optional[List[str]] = None
