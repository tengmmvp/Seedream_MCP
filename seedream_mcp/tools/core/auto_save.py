"""生成结果的自动保存：从 URL 下载或从 Base64 解码并落盘。

``_auto_save`` 作为两个公开入口的公共骨架，按 resolve 得到的基础目录为每次调用独立构造
AutoSaveManager 并在 finally 中关闭，使下载连接池的作用域限定在单次保存任务内，不跨工具
调用残留状态。共享 DownloadManager 由调用方通过 lifespan 注入传入。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...config import SeedreamConfig
from ...utils.auto_save import AutoSaveManager, AutoSaveResult
from ...utils.download_manager import DownloadManager
from ...utils.logging import get_logger
from ._helpers import _resolve_base_dir
from .results import extract_images, is_saveable_image

logger = get_logger(__name__)

# AutoSaveManager 批量保存方法的可调用类型：接收 manager、image_data、tool_name，返回保存结果列表。
BatchSaveMethod = Callable[
    [AutoSaveManager, list[dict[str, Any]], str], Awaitable[list[AutoSaveResult]]
]

# 自动保存结果与可保存图片原始索引的二元组：索引列表供回填阶段按位置写入。
AutoSaveOutcome = tuple[list[AutoSaveResult], list[int]]


def _build_auto_save_manager(
    config: SeedreamConfig,
    base_dir: Path,
    download_manager: DownloadManager | None,
) -> AutoSaveManager:
    """按配置构造自动保存管理器。"""
    return AutoSaveManager(
        base_dir=base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
        date_folder=config.auto_save_date_folder,
        cleanup_days=config.auto_save_cleanup_days,
        max_total_bytes=config.auto_save_max_total_bytes,
        download_manager=download_manager,
    )


async def _auto_save(
    result: dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    data_key: str,
    save_method: BatchSaveMethod,
    empty_warning: str,
    download_manager: DownloadManager | None = None,
) -> AutoSaveOutcome:
    """auto_save_from_urls / auto_save_from_base64 的公共骨架。

    data_key 区分结果字典中取值的键，取值为 url 或 b64_json；save_method 为
    AutoSaveManager 的批量保存方法，即 save_multiple_images 或
    save_multiple_base64_images；empty_warning 为无可保存数据时的告警文案。

    Returns:
        (保存结果列表, 可保存图片在归一化列表中的原始索引列表)。索引列表供回填
        阶段按位置写入，消除收集与回填两次独立过滤可能错位的风险。
    """
    base_dir = await asyncio.to_thread(_resolve_base_dir, config, save_path)

    images = extract_images(result)
    image_data: list[dict[str, Any]] = []
    # 记录每个待保存图片在归一化列表中的原始索引，供回填阶段按索引写入。
    saveable_indices: list[int] = []
    for idx, image in enumerate(images):
        if not is_saveable_image(image, data_key):
            continue
        saveable_indices.append(idx)
        # 序号基于可保存图计数，避免失败占位项导致文件名跳号
        save_ordinal = len(image_data) + 1
        image_data.append(
            {
                data_key: image[data_key],
                "prompt": prompt,
                "custom_name": f"{custom_name}_{save_ordinal}" if custom_name else None,
                "alt_text": f"Generated image {save_ordinal}",
            }
        )

    if not image_data:
        logger.warning(empty_warning)
        return [], []

    # async with 确保 save 阶段任意异常均释放 manager 自建的下载连接池，不依赖手动 close
    async with _build_auto_save_manager(config, base_dir, download_manager) as auto_save_manager:
        results = await save_method(auto_save_manager, image_data, tool_name)
        return results, saveable_indices


async def auto_save_from_urls(
    result: dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    download_manager: DownloadManager | None = None,
) -> AutoSaveOutcome:
    """从 URL 异步下载并保存图片。

    根据配置项自动解析基础目录，支持批量下载并记录保存结果，
    包含超时控制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典，包含 URL 等信息。
        prompt: 生成图片所用的提示词，用于元数据记录。
        config: Seedream 配置实例，包含保存参数。
        save_path: 用户指定的保存路径，可选。
        custom_name: 自定义文件名前缀，可选。
        tool_name: 工具名称标识，用于路径组织。
        download_manager: 可选的共享下载管理器，复用 aiohttp 连接池；未提供时由内部新建。

    Returns:
        (保存结果对象列表, 可保存图片原始索引列表) 二元组。索引列表供回填阶段
        按位置写入本地路径。
    """
    return await _auto_save(
        result=result,
        prompt=prompt,
        config=config,
        save_path=save_path,
        custom_name=custom_name,
        tool_name=tool_name,
        data_key="url",
        save_method=AutoSaveManager.save_multiple_images,
        empty_warning="未找到可保存的图片 URL",
        download_manager=download_manager,
    )


async def auto_save_from_base64(
    result: dict[str, Any],
    prompt: str,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    download_manager: DownloadManager | None = None,
) -> AutoSaveOutcome:
    """从 Base64 数据异步解码并保存图片。

    根据配置项自动解析基础目录，支持批量解码并保存，
    包含文件大小限制、重试机制及并发管理。

    Args:
        result: 图片生成结果字典，包含 b64_json 等信息。
        prompt: 生成图片所用的提示词，用于元数据记录。
        config: Seedream 配置实例，包含保存参数。
        save_path: 用户指定的保存路径，可选。
        custom_name: 自定义文件名前缀，可选。
        tool_name: 工具名称标识，用于路径组织。
        download_manager: 可选的共享下载管理器，复用 aiohttp 连接池；未提供时由内部新建。

    Returns:
        (保存结果对象列表, 可保存图片原始索引列表) 二元组。索引列表供回填阶段
        按位置写入本地路径。
    """
    return await _auto_save(
        result=result,
        prompt=prompt,
        config=config,
        save_path=save_path,
        custom_name=custom_name,
        tool_name=tool_name,
        data_key="b64_json",
        save_method=AutoSaveManager.save_multiple_base64_images,
        empty_warning="未找到可保存的 base64 图片数据",
        download_manager=download_manager,
    )
