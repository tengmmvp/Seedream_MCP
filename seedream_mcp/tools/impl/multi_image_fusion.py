"""
多图融合工具模块

本模块提供多图融合功能的核心处理逻辑，支持将多张图片融合生成新图像。
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

# 模块日志记录器
logger = get_logger(__name__)


async def handle_multi_image_fusion(arguments: Dict[str, Any]) -> List[TextContent]:
    """
    处理多图融合请求。

    根据提供的参数执行多图融合任务，包括参数验证、API调用、结果处理及可选的自动保存功能。
    支持流式响应和提示词优化，可将结果返回为URL或Base64编码格式。

    Args:
        arguments: 融合任务参数字典，包含以下字段：
            - prompt (str, optional): 融合描述提示词，默认为空字符串
            - image (List): 待融合的图片列表
            - size (str, optional): 生成图片尺寸，默认使用配置中的默认值
            - watermark (bool, optional): 是否添加水印，默认使用配置中的默认值
            - response_format (str, optional): 响应格式，支持 'url' 或 'b64_json'，默认为 'url'
            - stream (bool, optional): 是否启用流式响应，默认为 False
            - optimize_prompt_options (Dict, optional): 提示词优化选项
            - auto_save (bool, optional): 是否自动保存结果，默认使用配置中的默认值
            - save_path (str, optional): 保存路径，默认使用配置中的路径
            - custom_name (str, optional): 自定义文件名

    Returns:
        List[TextContent]: 包含融合结果的文本内容列表，成功时返回结果详情，失败时返回错误信息

    Raises:
        Exception: 当融合过程中发生错误时抛出异常，异常信息会被捕获并格式化返回给用户
    """
    try:
        # 获取全局配置
        config = get_global_config()

        # 提取并验证请求参数
        prompt = arguments.get("prompt", "")
        image = arguments.get("image")
        # 验证图片尺寸是否符合当前模型要求
        size = validate_size_for_model(arguments.get("size") or config.default_size, config.model_id)
        # 处理水印参数，优先使用请求参数，否则使用配置默认值
        watermark_value = arguments.get("watermark")
        watermark = (
            validate_watermark(watermark_value)
            if watermark_value is not None
            else config.default_watermark
        )
        # 验证响应格式
        response_format = validate_response_format(arguments.get("response_format", "url"))
        stream = bool(arguments.get("stream", False))
        # 验证提示词优化选项
        optimize_prompt_options = validate_optimize_prompt_options(
            arguments.get("optimize_prompt_options"), config.model_id
        )
        # 自动保存相关参数
        auto_save = arguments.get("auto_save")
        save_path = arguments.get("save_path")
        custom_name = arguments.get("custom_name")

        # 确定是否启用自动保存功能
        enable_auto_save = auto_save if auto_save is not None else config.auto_save_enabled

        # 记录任务开始日志
        logger.info(
            "多图融合开始: prompt='{}...', size={}, stream={}",
            (prompt or "")[:50],
            size,
            stream,
        )

        # 创建客户端并执行融合请求
        async with SeedreamClient(config) as client:
            result = await client.multi_image_fusion(
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
            # 根据响应格式选择对应的保存方式
            if response_format == "url":
                auto_save_results = await auto_save_from_urls(
                    result, prompt, config, save_path, custom_name, "multi_image_fusion"
                )
            else:
                auto_save_results = await auto_save_from_base64(
                    result, prompt, config, save_path, custom_name, "multi_image_fusion"
                )

            # 将保存结果合并到原始结果中
            if auto_save_results:
                result = update_result_with_auto_save(result, auto_save_results)

        # 格式化响应文本
        response_text = format_generation_response(
            "多图融合任务完成",
            result,
            prompt,
            size,
            auto_save_results,
            enable_auto_save,
        )

        return [TextContent(type="text", text=response_text)]
    except Exception as exc:
        # 记录错误日志
        logger.error("多图融合处理失败", exc_info=True)
        # 提供用户友好的错误指引
        guidance = "请检查图片列表与尺寸参数，确认 API Key 和网络可用后重试。"
        return [
            TextContent(
                type="text",
                text=f"多图融合失败：{format_error_for_user(exc)}\n{guidance}",
            )
        ]
