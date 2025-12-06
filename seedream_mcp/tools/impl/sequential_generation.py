"""
组图/连续生成工具模块

提供组图批量生成功能的核心处理逻辑，支持连续生成多张图片、自动保存及流式输出。
"""

from __future__ import annotations

# 标准库导入
from typing import Any, Dict, List

# 第三方库导入
from mcp.types import TextContent

# 项目内部导入 - 客户端与配置
from ...client import SeedreamClient
from ...config import get_global_config

# 项目内部导入 - 工具模块
from ...utils.errors import format_error_for_user
from ...utils.logging import get_logger
from ...utils.validation import (
    validate_optimize_prompt_options,
    validate_response_format,
    validate_size_for_model,
    validate_watermark,
)

# 项目内部导入 - 核心功能
from ..core.common import (
    auto_save_from_base64,
    auto_save_from_urls,
    format_generation_response,
    update_result_with_auto_save,
)

# 模块日志记录器
logger = get_logger(__name__)


async def handle_sequential_generation(arguments: Dict[str, Any]) -> List[TextContent]:
    """
    处理组图/连续生成请求。

    执行批量图片生成任务，支持提示词优化、多种响应格式、水印配置及自动保存功能。
    根据用户配置参数调用 API 生成多张图片，并可选择性地将结果保存至本地。

    Args:
        arguments: 请求参数字典，包含以下键值：
            - prompt (str): 生成图片的提示词描述
            - max_images (int, optional): 最大生成图片数量，默认为 4
            - image (str, optional): 参考图片的 URL 或 Base64 编码
            - size (str, optional): 图片尺寸规格
            - watermark (bool, optional): 是否添加水印
            - response_format (str, optional): 响应格式，"url" 或 "b64_json"，默认为 "url"
            - stream (bool, optional): 是否启用流式输出，默认为 False
            - optimize_prompt_options (dict, optional): 提示词优化选项
            - auto_save (bool, optional): 是否自动保存图片
            - save_path (str, optional): 自定义保存路径
            - custom_name (str, optional): 自定义文件名前缀

    Returns:
        包含文本内容的列表，通常只有一个元素，描述生成任务的执行结果、图片信息及保存状态。

    Raises:
        Exception: 当生成过程中发生错误时，捕获异常并返回格式化的错误提示信息。
    """
    try:
        # 加载全局配置
        config = get_global_config()

        # 提取并验证请求参数
        prompt = arguments.get("prompt", "")
        max_images = arguments.get("max_images", 4)
        image = arguments.get("image")
        size = validate_size_for_model(
            arguments.get("size") or config.default_size, config.model_id
        )
        watermark_value = arguments.get("watermark")
        watermark = (
            validate_watermark(watermark_value)
            if watermark_value is not None
            else config.default_watermark
        )
        response_format = validate_response_format(arguments.get("response_format", "url"))
        stream = bool(arguments.get("stream", False))
        optimize_prompt_options = validate_optimize_prompt_options(
            arguments.get("optimize_prompt_options"), config.model_id
        )
        auto_save = arguments.get("auto_save")
        save_path = arguments.get("save_path")
        custom_name = arguments.get("custom_name")

        # 确定自动保存配置
        enable_auto_save = auto_save if auto_save is not None else config.auto_save_enabled

        # 记录任务开始信息
        logger.info(
            "组图生成开始: prompt='{}...', max_images={}, size={}, stream={}",
            (prompt or "")[:50],
            max_images,
            size,
            stream,
        )

        # 执行组图生成请求
        async with SeedreamClient(config) as client:
            result = await client.sequential_generation(
                prompt=prompt,
                max_images=max_images,
                size=size,
                watermark=watermark,
                response_format=response_format,
                image=image,
                stream=stream,
                optimize_prompt_options=optimize_prompt_options,
            )

        # 处理自动保存逻辑
        auto_save_results: List[Any] = []
        if enable_auto_save and result.get("success"):
            # 根据响应格式选择对应的保存方法
            if response_format == "url":
                auto_save_results = await auto_save_from_urls(
                    result, prompt, config, save_path, custom_name, "sequential_generation"
                )
            else:
                auto_save_results = await auto_save_from_base64(
                    result, prompt, config, save_path, custom_name, "sequential_generation"
                )

            # 将保存结果合并到响应数据中
            if auto_save_results:
                result = update_result_with_auto_save(result, auto_save_results)

        # 格式化最终响应文本
        response_text = format_generation_response(
            "组图生成任务完成",
            result,
            prompt,
            size,
            auto_save_results,
            enable_auto_save,
        )

        return [TextContent(type="text", text=response_text)]

    except Exception as exc:
        # 记录异常详情
        logger.error("组图生成处理失败", exc_info=True)

        # 提供用户友好的故障排除指导
        guidance = "请检查提示词、数量与图片参数，确认 API Key 和网络可用后重试。"
        return [
            TextContent(
                type="text",
                text=f"组图生成失败：{format_error_for_user(exc)}\n{guidance}",
            )
        ]
