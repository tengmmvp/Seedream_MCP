"""生成类工具 impl 共享的工具元数据与开始日志参数构造。

四个生成 handler 透传给 ``execute_generation_handler`` 的工具名、完成标题、失败前缀、
排查建议与开始日志模板高度同构，集中收敛为不可变 ``ToolMetadata`` 描述，各 handler 解包
后传入流水线。开始日志的参数构造回调由本模块提供：文生图、图文生图、多图融合共享
``_default_start_log_values``；组图输出的日志须包含运行时 max_images，由
``_sequential_start_log_values_factory`` 产出捕获该值的 builder。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ..core.context import GenerationExecutionContext


def _default_start_log_values(
    context: "GenerationExecutionContext",
) -> tuple[Any, ...]:
    """构造文生图、图文生图、多图融合共享的开始日志参数元组。"""
    return (
        len(context.prompt or ""),
        context.size,
        context.stream,
        context.request_count,
        context.parallelism,
    )


def _sequential_start_log_values_factory(
    max_images: int | None,
) -> Callable[["GenerationExecutionContext"], tuple[Any, ...]]:
    """构造组图输出开始日志参数的 builder，闭包捕获运行时 max_images。"""

    def builder(context: "GenerationExecutionContext") -> tuple[Any, ...]:
        return (
            len(context.prompt or ""),
            max_images,
            context.size,
            context.stream,
            context.request_count,
            context.parallelism,
        )

    return builder


@dataclass(frozen=True)
class ToolMetadata:
    """单个生成工具透传给 ``execute_generation_handler`` 的标量元数据。

    仅收纳各工具逐字不同且为常量的字符串字段；开始日志参数构造回调因组图输出依赖
    运行时入参，由各 handler 显式传入，数据表与运行时逻辑各司其职。
    """

    tool_name: str
    completion_title: str
    failure_prefix: str
    guidance: str
    start_log_message: str

    def as_handler_kwargs(self) -> dict[str, Any]:
        """展开为 ``execute_generation_handler`` 的标量关键字参数。"""
        return {
            "tool_name": self.tool_name,
            "completion_title": self.completion_title,
            "failure_prefix": self.failure_prefix,
            "guidance": self.guidance,
            "start_log_message": self.start_log_message,
        }


TEXT_TO_IMAGE = ToolMetadata(
    tool_name="seedream_text_to_image",
    completion_title="文生图任务完成",
    failure_prefix="文生图生成",
    guidance="请检查提示词长度、尺寸与模型兼容性，确认 API Key 和网络可用后重试。",
    start_log_message=(
        "文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
)

IMAGE_TO_IMAGE = ToolMetadata(
    tool_name="seedream_image_to_image",
    completion_title="图文生图任务完成",
    failure_prefix="图文生图生成",
    guidance="请检查图片路径/URL 与尺寸参数，确认 API Key 和网络可用后重试。",
    start_log_message=(
        "图文生图开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
)

MULTI_IMAGE_FUSION = ToolMetadata(
    tool_name="seedream_multi_image_fusion",
    completion_title="多图融合任务完成",
    failure_prefix="多图融合",
    guidance="请检查图片列表与尺寸参数，确认 API Key 和网络可用后重试。",
    start_log_message=(
        "多图融合开始: prompt_len={}, size={}, stream={}, request_count={}, parallelism={}"
    ),
)

SEQUENTIAL_GENERATION = ToolMetadata(
    tool_name="seedream_sequential_generation",
    completion_title="组图输出任务完成",
    failure_prefix="组图输出",
    guidance="请检查提示词、数量与图片参数，确认 API Key 和网络可用后重试。",
    start_log_message=(
        "组图输出开始: prompt_len={}, max_images={}, size={}, stream={}, "
        "request_count={}, parallelism={}"
    ),
)
