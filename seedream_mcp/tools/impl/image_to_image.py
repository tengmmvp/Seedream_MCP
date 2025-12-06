"""
图生图工具模块

本模块提供图生图功能的核心处理逻辑，支持基于输入图像和提示词生成新图像，
并提供流式输出、自动保存、提示词优化等增强功能。
"""

from __future__ import annotations

from typing import Any, Dict, List

from mcp.types import TextContent

from ...client import SeedreamClient
from ...config import get_global_config
from ...utils.errors import format_error_for_user
from ...utils.logging import get_logger
from ...utils.validation import (
    validate_optimize_prompt_options,
    validate_response_format,
    validate_size_for_model,
    validate_watermark,
)
from ..core.common import (
    auto_save_from_base64,
    auto_save_from_urls,
    format_generation_response,
    update_result_with_auto_save,
)

logger = get_logger(__name__)


async def handle_image_to_image(arguments: Dict[str, Any]) -> List[TextContent]:
    """
    处理图生图请求。

    根据输入的图像和提示词生成新图像，支持多种配置选项包括尺寸、水印、
    响应格式、流式输出、提示词优化及自动保存等功能。

    Args:
        arguments: 请求参数字典，支持以下键值：
            - prompt (str, optional): 生成图像的提示词描述
            - image (str): 输入图像的路径或URL
            - size (str, optional): 生成图像尺寸，默认使用配置中的默认值
            - watermark (bool, optional): 是否添加水印，默认使用配置中的默认值
            - response_format (str, optional): 响应格式，支持 "url" 或 "b64_json"，默认为 "url"
            - stream (bool, optional): 是否启用流式输出，默认为 False
            - optimize_prompt_options (dict, optional): 提示词优化选项
            - auto_save (bool, optional): 是否自动保存生成的图像，默认使用配置中的默认值
            - save_path (str, optional): 自定义保存路径
            - custom_name (str, optional): 自定义文件名

    Returns:
        List[TextContent]: 包含生成结果或错误信息的文本内容列表，
                          成功时返回图像URL/Base64数据及元数据，
                          失败时返回格式化的错误信息和操作指引。

    Raises:
        Exception: 捕获所有异常并转换为用户友好的错误消息，不向上层抛出。
    """
    try:
        # 获取全局配置
        config = get_global_config()

        # 提取并验证请求参数
        prompt = arguments.get("prompt", "")
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

        # 确定是否启用自动保存功能
        enable_auto_save = auto_save if auto_save is not None else config.auto_save_enabled

        # 记录请求信息
        logger.info(
            "图生图开始: prompt='{}...', size={}, stream={}",
            (prompt or "")[:50],
            size,
            stream,
        )

        # 调用客户端执行图生图请求
        async with SeedreamClient(config) as client:
            result = await client.image_to_image(
                prompt=prompt,
                image=image,
                size=size,
                watermark=watermark,
                response_format=response_format,
                stream=stream,
                optimize_prompt_options=optimize_prompt_options,
            )

        # 处理自动保存逻辑
        auto_save_results: List[Any] = []
        if enable_auto_save and result.get("success"):
            if response_format == "url":
                # URL格式：从远程URL下载并保存
                auto_save_results = await auto_save_from_urls(
                    result, prompt, config, save_path, custom_name, "image_to_image"
                )
            else:
                # Base64格式：直接解码并保存
                auto_save_results = await auto_save_from_base64(
                    result, prompt, config, save_path, custom_name, "image_to_image"
                )

            # 将保存结果合并到响应中
            if auto_save_results:
                result = update_result_with_auto_save(result, auto_save_results)

        # 格式化响应文本
        response_text = format_generation_response(
            "图生图任务完成",
            result,
            prompt,
            size,
            auto_save_results,
            enable_auto_save,
        )

        return [TextContent(type="text", text=response_text)]
    except Exception as exc:
        # 记录异常详情
        logger.error("图生图处理失败", exc_info=True)

        # 生成用户友好的错误信息和操作指引
        guidance = "请检查图片路径/URL 与尺寸参数，确认 API Key 和网络可用后重试。"
        return [
            TextContent(
                type="text",
                text=f"图生图生成失败：{format_error_for_user(exc)}\n{guidance}",
            )
        ]
