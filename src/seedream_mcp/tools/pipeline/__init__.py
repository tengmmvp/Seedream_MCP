"""生成类工具通用处理门面。

内部按职责拆分到 _execute/context/results/parallel/auto_save/_progress/_failures/_paths
子模块，本门面聚合公共符号供 handlers 与测试导入。``ToolMetadata`` 收纳各工具的静态元数据，
``execute_generation_handler`` 是四类生成工具的统一处理流水线，各阶段职责与异常降级
契约见该函数 docstring。
"""

from __future__ import annotations

from ._execute import (  # noqa: F401
    ToolMetadata as ToolMetadata,
    execute_generation_handler as execute_generation_handler,
)
from ._progress import (  # noqa: F401
    PROGRESS_COMPLETE as PROGRESS_COMPLETE,
    safe_report_progress as safe_report_progress,
)
from ._failures import log_tiered_failure as log_tiered_failure  # noqa: F401
from ..outputs.channels import output_channels_scope as output_channels_scope  # noqa: F401
from .auto_save import (  # noqa: F401
    auto_save_from_base64 as auto_save_from_base64,
    auto_save_from_urls as auto_save_from_urls,
)
from .context import (  # noqa: F401
    GenerationExecutionContext as GenerationExecutionContext,
    build_generation_context as build_generation_context,
)
from .parallel import get_lifespan_resource as get_lifespan_resource  # noqa: F401
from .results import (  # noqa: F401
    aggregate_parallel_generation_results as aggregate_parallel_generation_results,
    extract_images as extract_images,
    format_generation_response as format_generation_response,
    update_result_with_auto_save as update_result_with_auto_save,
)

__all__ = [
    "GenerationExecutionContext",
    "PROGRESS_COMPLETE",
    "ToolMetadata",
    "aggregate_parallel_generation_results",
    "auto_save_from_base64",
    "auto_save_from_urls",
    "build_generation_context",
    "execute_generation_handler",
    "extract_images",
    "format_generation_response",
    "get_lifespan_resource",
    "log_tiered_failure",
    "output_channels_scope",
    "safe_report_progress",
    "update_result_with_auto_save",
]
