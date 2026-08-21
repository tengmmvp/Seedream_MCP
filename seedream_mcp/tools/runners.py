"""MCP 工具适配器层，作为 composition root 组装 core 流水线与 impl 处理器。

每个 ``run_*`` 函数经 ``workspace_roots_scope_from_result`` 应用 MCP Roots 工作区
边界，roots 结果由 server 层按协商版本取回注入；再将 pydantic 校验后的入参模型委托
给对应 ``handle_*``。本模块位于 tools/ 顶层，依赖方向为 core <- impl <- runners，
避免 core 反向依赖 impl。``include_previews`` 开关经 ``preview_inclusion_scope``
沿异步上下文传入流水线，impl 处理器签名不感知该开关。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, ListRootsResult

from ..config import SeedreamConfig
from ..utils.io.io_path import workspace_roots_scope_from_result
from .core.common import preview_inclusion_scope
from .core.schemas import (
    BrowseImagesInput,
    GenerationInputParams,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from .impl.browse_images import handle_browse_images
from .impl.image_to_image import handle_image_to_image
from .impl.multi_image_fusion import handle_multi_image_fusion
from .impl.sequential_generation import handle_sequential_generation
from .impl.text_to_image import handle_text_to_image

# 泛型绑定具体输入模型，避免 Callable 逆变要求 handler 接受任意协议实现。
_GenerationInputT = TypeVar("_GenerationInputT", bound=GenerationInputParams)

_GenerationHandler = Callable[
    [_GenerationInputT, SeedreamConfig, Context | None],
    Awaitable[CallToolResult],
]


async def _run_generation_tool(
    params: _GenerationInputT,
    config: SeedreamConfig,
    ctx: Context | None,
    handler: _GenerationHandler[_GenerationInputT],
    workspace_roots: ListRootsResult | None = None,
    include_previews: bool = True,
) -> CallToolResult:
    """在工作区边界与预览开关作用域内将类型化入参模型委托给生成 handler。"""
    async with workspace_roots_scope_from_result(workspace_roots):
        with preview_inclusion_scope(include_previews):
            return await handler(params, config, ctx)


async def run_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
    workspace_roots: ListRootsResult | None = None,
    include_previews: bool = True,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_text_to_image`` 处理文生图请求。"""
    return await _run_generation_tool(
        params, config, ctx, handle_text_to_image, workspace_roots, include_previews
    )


async def run_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
    workspace_roots: ListRootsResult | None = None,
    include_previews: bool = True,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_image_to_image`` 处理图文生图请求。"""
    return await _run_generation_tool(
        params, config, ctx, handle_image_to_image, workspace_roots, include_previews
    )


async def run_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
    workspace_roots: ListRootsResult | None = None,
    include_previews: bool = True,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_multi_image_fusion`` 处理多图融合请求。"""
    return await _run_generation_tool(
        params, config, ctx, handle_multi_image_fusion, workspace_roots, include_previews
    )


async def run_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
    workspace_roots: ListRootsResult | None = None,
    include_previews: bool = True,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_sequential_generation`` 处理组图输出请求。"""
    return await _run_generation_tool(
        params, config, ctx, handle_sequential_generation, workspace_roots, include_previews
    )


async def run_browse_images(
    params: BrowseImagesInput,
    ctx: Context | None = None,
    workspace_roots: ListRootsResult | None = None,
) -> CallToolResult:
    """注入工作区边界后委托 ``handle_browse_images`` 处理图片浏览请求。"""
    async with workspace_roots_scope_from_result(workspace_roots):
        return await handle_browse_images(params, ctx=ctx)
