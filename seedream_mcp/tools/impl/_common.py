"""生成类工具 impl 共享的工具元数据常量与开始日志参数构造。

四个生成 handler 传给 ``execute_generation_handler`` 的工具名、完成标题、失败前缀、
开始日志模板与参数构造回调高度同构，收敛为不可变的 ``ToolMetadata`` 常量；数据类
定义于 core 门面，依赖方向保持 core <- impl。开始日志参数构造回调：文生图、图文
生图、多图融合共享 ``_default_start_log_values``；组图输出须包含运行时 max_images，
由 ``_sequential_start_log_values`` 从执行上下文读取。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.common import ToolMetadata

if TYPE_CHECKING:
    from ..core.context import GenerationExecutionContext


def _default_start_log_values(
    context: "GenerationExecutionContext",
) -> tuple[Any, ...]:
    """构造三个生成工具共享的开始日志参数元组。"""
    return (
        len(context.prompt or ""),
        context.size,
        context.stream,
        context.request_count,
        context.parallelism,
    )


def _sequential_start_log_values(
    context: "GenerationExecutionContext",
) -> tuple[Any, ...]:
    """构造组图输出开始日志参数元组，携带 schema 推导后的生效 max_images。"""
    return (
        len(context.prompt or ""),
        context.max_images,
        context.size,
        context.stream,
        context.request_count,
        context.parallelism,
    )


TEXT_TO_IMAGE = ToolMetadata(
    tool_name="text_to_image",
    completion_title="文生图任务完成",
    failure_prefix="文生图生成",
    start_log_message=(
        "文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
    start_log_values_builder=_default_start_log_values,
)

IMAGE_TO_IMAGE = ToolMetadata(
    tool_name="image_to_image",
    completion_title="图文生图任务完成",
    failure_prefix="图文生图生成",
    start_log_message=(
        "图文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
    start_log_values_builder=_default_start_log_values,
)

MULTI_IMAGE_FUSION = ToolMetadata(
    tool_name="multi_image_fusion",
    completion_title="多图融合任务完成",
    failure_prefix="多图融合",
    start_log_message=(
        "多图融合开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
    start_log_values_builder=_default_start_log_values,
)

SEQUENTIAL_GENERATION = ToolMetadata(
    tool_name="sequential_generation",
    completion_title="组图输出任务完成",
    failure_prefix="组图输出",
    start_log_message=(
        "组图输出开始: prompt_len={}, max_images={}, size={}, stream={}, "
        "request_count={}, parallelism={}"
    ),
    start_log_values_builder=_sequential_start_log_values,
)
