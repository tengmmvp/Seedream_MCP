"""
通用工具处理辅助函数

本模块提供图片数据提取、自动保存及结果格式化等功能，
支持基于 URL 和 Base64 的图片保存，并生成规范化的响应信息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...config import SeedreamConfig
from ...utils.auto_save import AutoSaveManager
from ...utils.logging import get_logger

logger = get_logger(__name__)


def extract_images(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从生成结果中提取图片数据列表。

    支持多种数据结构，兼容嵌套字典与数组格式，统一转换为列表输出。

    Args:
        result: 图片生成结果字典，包含响应数据及元信息。

    Returns:
        包含图片数据的字典列表，每个字典代表一张图片的完整信息。
    """
    data = result.get("data", {})
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data:
        nested = data.get("data", [])
        return nested if isinstance(nested, list) else [nested]
    return [data]


def _resolve_base_dir(config: SeedreamConfig, save_path: Optional[str]) -> Optional[Path]:
    """
    解析自动保存的基础目录路径。

    优先使用用户指定路径，若未指定则使用配置中的默认路径。

    Args:
        config: Seedream 配置实例，包含自动保存相关参数。
        save_path: 用户指定的保存路径，可选。

    Returns:
        解析后的路径对象，若两者均未指定则返回 None。
    """
    if save_path:
        return Path(save_path)
    if config.auto_save_base_dir:
        return Path(config.auto_save_base_dir)
    return None


async def auto_save_from_urls(
    result: Dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: Optional[str],
    custom_name: Optional[str],
    tool_name: str,
) -> List:
    """
    从 URL 异步下载并保存图片。

    根据配置项自动解析基础目录，支持批量下载并记录保存结果，
    包含超时控制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典，包含 URL 等信息。
        prompt: 生成图片所用的提示词，用于元数据记录。
        config: Seedream 配置实例，包含保存参数。
        save_path: 用户指定的保存路径，可选。
        custom_name: 自定义文件名前缀，可选。
        tool_name: 工具名称标识，用于路径组织。

    Returns:
        保存结果对象列表，每个对象包含成功状态、路径及错误信息。
    """
    base_dir = _resolve_base_dir(config, save_path)
    auto_save_manager = AutoSaveManager(
        base_dir=base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
    )

    images = extract_images(result)
    image_data = []
    for i, image in enumerate(images):
        if isinstance(image, dict) and image.get("url"):
            image_data.append(
                {
                    "url": image["url"],
                    "prompt": prompt,
                    "custom_name": f"{custom_name}_{i + 1}" if custom_name else None,
                    "alt_text": f"Generated image {i + 1}: {prompt[:50]}...",
                }
            )

    if not image_data:
        logger.warning("未找到可保存的图片 URL")
        return []

    return await auto_save_manager.save_multiple_images(image_data, tool_name=tool_name)


async def auto_save_from_base64(
    result: Dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: Optional[str],
    custom_name: Optional[str],
    tool_name: str,
) -> List:
    """
    从 Base64 数据异步解码并保存图片。

    根据配置项自动解析基础目录，支持批量解码并保存，
    包含文件大小限制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典,包含 b64_json 等信息。
        prompt: 生成图片所用的提示词,用于元数据记录。
        config: Seedream 配置实例,包含保存参数。
        save_path: 用户指定的保存路径,可选。
        custom_name: 自定义文件名前缀,可选。
        tool_name: 工具名称标识,用于路径组织。

    Returns:
        保存结果对象列表,每个对象包含成功状态、路径及错误信息。
    """
    base_dir = _resolve_base_dir(config, save_path)
    auto_save_manager = AutoSaveManager(
        base_dir=base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
    )

    images = extract_images(result)
    image_data = []
    for i, image in enumerate(images):
        if isinstance(image, dict) and image.get("b64_json"):
            image_data.append(
                {
                    "b64_json": image["b64_json"],
                    "prompt": prompt,
                    "custom_name": f"{custom_name}_{i + 1}" if custom_name else None,
                    "alt_text": f"Generated image {i + 1}: {prompt[:50]}...",
                }
            )

    if not image_data:
        logger.warning("未找到可保存的 base64 图片数据")
        return []

    return await auto_save_manager.save_multiple_base64_images(image_data, tool_name=tool_name)


def update_result_with_auto_save(result: Dict[str, Any], auto_save_results: List) -> Dict[str, Any]:
    """
    将自动保存结果合并到生成结果中。

    在原结果基础上添加保存统计信息，并为每张图片补充本地路径和 Markdown 引用，
    不修改原结果对象，返回新的字典副本。

    Args:
        result: 图片生成结果字典,包含原始响应数据。
        auto_save_results: 自动保存结果对象列表。

    Returns:
        更新后的结果字典,包含自动保存信息及本地路径。
    """
    updated_result = result.copy()

    auto_save_info = {
        "enabled": True,
        "total_images": len(auto_save_results),
        "successful_saves": sum(1 for r in auto_save_results if getattr(r, "success", False)),
        "failed_saves": sum(1 for r in auto_save_results if not getattr(r, "success", False)),
        "results": [r.to_dict() for r in auto_save_results],
    }
    updated_result["auto_save"] = auto_save_info

    images = extract_images(updated_result)
    for image, save_result in zip(images, auto_save_results):
        if isinstance(image, dict) and getattr(save_result, "success", False):
            image["local_path"] = save_result.local_path
            image["markdown_ref"] = save_result.markdown_ref

    return updated_result


def format_generation_response(
    title: str,
    result: Dict[str, Any],
    prompt: str,
    size: str,
    auto_save_results: Optional[List] = None,
    auto_save_enabled: bool = False,
) -> str:
    """
    格式化图片生成结果为可读文本。

    将生成结果、提示词、尺寸、保存信息及使用统计等数据，
    按规范化格式输出为结构清晰的多行文本字符串。

    Args:
        title: 响应标题,用于标识生成任务类型。
        result: 图片生成结果字典,包含图片数据及使用统计。
        prompt: 生成图片所用的提示词。
        size: 生成图片的尺寸规格。
        auto_save_results: 自动保存结果列表,可选。
        auto_save_enabled: 是否启用自动保存功能,默认 False。

    Returns:
        格式化后的响应文本,包含完整生成信息及元数据。
    """
    if not result.get("success"):
        return f"图片生成失败: {result.get('error', '未知错误')}"

    images = extract_images(result)
    usage = result.get("usage", {})

    parts: List[str] = []
    parts.append(title)
    parts.append(f"提示词: {prompt}")
    parts.append(f"尺寸: {size}")
    parts.append("")

    for i, image in enumerate(images, 1):
        if isinstance(image, dict):
            parts.append(f"图片 {i}:")
            if image.get("url"):
                parts.append(f"  URL: {image['url']}")
            if "size" in image:
                parts.append(f"  尺寸: {image['size']}")
            if "image_index" in image:
                parts.append(f"  序号: {image['image_index']}")
            if "local_path" in image:
                parts.append(f"  本地路径: {image['local_path']}")
            if "markdown_ref" in image:
                parts.append(f"  Markdown 引用: {image['markdown_ref']}")
            if "b64_json" in image:
                b64_data = image.get("b64_json")
                parts.append(f"  Base64 数据: {len(b64_data)} 字符" if b64_data else "  Base64 数据: 无")
            parts.append("")

    if auto_save_enabled:
        if auto_save_results:
            parts.append("自动保存信息:")
            successful_saves = sum(1 for r in auto_save_results if getattr(r, "success", False))
            failed_saves = len(auto_save_results) - successful_saves
            parts.append(f"  总图片数: {len(auto_save_results)}")
            parts.append(f"  成功保存: {successful_saves}")
            if failed_saves:
                parts.append(f"  保存失败: {failed_saves}")
            for i, save_result in enumerate(auto_save_results, 1):
                if getattr(save_result, "success", False):
                    parts.append(f"  图片 {i}: 已保存到 {save_result.local_path}")
                else:
                    parts.append(f"  图片 {i}: 保存失败 - {getattr(save_result, 'error', '未知原因')}")
            parts.append("")
        else:
            parts.append("自动保存: 已开启但未生成可保存的图片")
            parts.append("")

    if usage:
        parts.append("使用统计:")
        if "generated_images" in usage:
            parts.append(f"  生成图片数: {usage['generated_images']}")
        if "output_tokens" in usage:
            parts.append(f"  输出 tokens: {usage['output_tokens']}")
        if "total_tokens" in usage:
            parts.append(f"  总 tokens: {usage['total_tokens']}")
        if "prompt_tokens" in usage:
            parts.append(f"  提示词 tokens: {usage['prompt_tokens']}")
        if "completion_tokens" in usage:
            parts.append(f"  完成 tokens: {usage['completion_tokens']}")
        if "cost" in usage:
            parts.append(f"  成本: {usage['cost']}")
        parts.append("")

    return "\n".join(parts)
