"""MCP 工具适配器层，作为 composition root 组装 core 流水线与 impl 处理器。

每个 ``run_*`` 函数经 ``workspace_roots_scope`` 注入 MCP Roots 工作区边界，再将经
pydantic 校验的入参 model_dump 后委托给对应 ``handle_*``。本模块位于 tools/ 顶层而非
core/，使依赖方向为 core <- impl <- runners，避免 core 反向依赖 impl 造成包级循环。
"""

from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.types import CallToolResult

from ..config import SeedreamConfig
from ..utils.path_utils import workspace_roots_scope
from .core.schemas import (
    BrowseImagesInput,
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


async def run_text_to_image(
    params: TextToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """执行文生图工具的 composition root 入口。

    在 MCP Roots 工作区边界内，将入参委托给 ``handle_text_to_image`` 执行。

    Args:
        params: 经 pydantic 校验的 ``TextToImageInput`` 实例，字段规则见 schemas。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_text_to_image(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_image_to_image(
    params: ImageToImageInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """执行图文生图工具的 composition root 入口。

    在 MCP Roots 工作区边界内，将入参委托给 ``handle_image_to_image`` 执行。

    Args:
        params: 经 pydantic 校验的 ``ImageToImageInput`` 实例，字段规则见 schemas。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_image_to_image(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_multi_image_fusion(
    params: MultiImageFusionInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """执行多图融合工具的 composition root 入口。

    在 MCP Roots 工作区边界内，将入参委托给 ``handle_multi_image_fusion`` 执行。

    Args:
        params: 经 pydantic 校验的 ``MultiImageFusionInput`` 实例，字段规则见 schemas。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_multi_image_fusion(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_sequential_generation(
    params: SequentialGenerationInput,
    config: SeedreamConfig,
    ctx: Context | None = None,
) -> CallToolResult:
    """执行组图输出工具的 composition root 入口。

    在 MCP Roots 工作区边界内，将入参委托给 ``handle_sequential_generation`` 执行。

    Args:
        params: 经 pydantic 校验的 ``SequentialGenerationInput`` 实例，字段规则见 schemas。
        config: 当前生效的 SeedreamConfig。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_sequential_generation(
            params.model_dump(exclude_none=True),
            config=config,
            ctx=ctx,
        )


async def run_browse_images(
    params: BrowseImagesInput,
    ctx: Context | None = None,
) -> CallToolResult:
    """执行图片浏览工具的 composition root 入口。

    在 MCP Roots 工作区边界内，将入参委托给 ``handle_browse_images`` 执行。

    Args:
        params: 经 pydantic 校验的 ``BrowseImagesInput`` 实例，字段规则见 schemas。
        ctx: MCP 上下文，用于进度上报与日志推送，无会话时可为 None。

    Returns:
        MCP 结构化工具结果。
    """
    async with workspace_roots_scope(ctx):
        return await handle_browse_images(
            params.model_dump(exclude_none=True),
            ctx=ctx,
        )
