"""生成结果的自动保存：从 URL 下载或从 Base64 解码并落盘。

``_auto_save`` 为两个公开入口的公共骨架，每次调用独立构造并在结束后关闭
AutoSaveManager，下载连接池不跨工具调用残留；共享 DownloadManager 由调用方经
lifespan 注入传入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ...config import SeedreamConfig
from ...utils.core.errors import SeedreamConfigError
from ...utils.io.io_save import AutoSaveManager, AutoSaveResult
from ...utils.io.io_download import DownloadManager
from ...utils.core.logs import get_logger
from ._helpers import _resolve_base_dir
from ...utils.io.io_path import resolve_save_root
from .results import extract_images, is_saveable_image

logger = get_logger()

BatchSaveMethod = Callable[
    [AutoSaveManager, list[dict[str, Any]], str], Awaitable[list[AutoSaveResult]]
]

# 自动保存结果与可保存图片原始索引的二元组，索引列表供回填阶段按位置写入。
AutoSaveOutcome = tuple[list[AutoSaveResult], list[int]]


# 两个公开入口的差异收敛为取值键、批量保存方法名与空数据告警文案三常量；方法以
# 名字携带、调用期经类属性解析，保持与直接属性访问一致的迟绑定语义。
@dataclass(frozen=True)
class _AutoSaveSpec:
    """单一自动保存入口的差异常量。

    Attributes:
        data_key: 图片项的取值键，url 或 b64_json。
        save_method_name: AutoSaveManager 批量保存方法的属性名。
        empty_warning: 无可保存数据时的告警文案。
    """

    data_key: str
    save_method_name: str
    empty_warning: str


_URL_SPEC = _AutoSaveSpec("url", "save_multiple_images", "未找到可保存的图片 URL")
_BASE64_SPEC = _AutoSaveSpec(
    "b64_json", "save_multiple_base64_images", "未找到可保存的 base64 图片数据"
)


def _build_auto_save_manager(
    config: SeedreamConfig,
    base_dir: Path,
    cleanup_base_dir: Path | None,
    download_manager: DownloadManager | None,
) -> AutoSaveManager:
    """按配置构造自动保存管理器。"""
    return AutoSaveManager(
        base_dir=base_dir,
        cleanup_base_dir=cleanup_base_dir,
        download_timeout=config.auto_save_download_timeout,
        max_retries=config.auto_save_max_retries,
        max_file_size=config.auto_save_max_file_size,
        max_concurrent=config.auto_save_max_concurrent,
        date_folder=config.auto_save_date_folder,
        cleanup_days=config.auto_save_cleanup_days,
        max_total_bytes=config.auto_save_max_total_bytes,
        download_manager=download_manager,
        fsync=config.auto_save_fsync,
    )


async def _auto_save(
    result: dict[str, Any],
    prompt: str | None,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    spec: _AutoSaveSpec,
    download_manager: DownloadManager | None = None,
    images: list[dict[str, Any]] | None = None,
) -> AutoSaveOutcome:
    """执行 URL 与 Base64 两个自动保存入口共用的保存流程。

    Args:
        spec: 保存路径差异常量，携带取值键、保存方法名与空数据告警文案。
        images: 调用方预提取的图片列表，None 时从 result 提取。

    Returns:
        (保存结果列表, 可保存图片在归一化列表中的原始索引列表)，索引列表供回填
        阶段按位置写入。
    """

    def _resolve_and_build() -> AutoSaveManager:
        # 清理与配额边界恒为部署级存储区：save_path 指定的目录可能同时存放其他
        # 文件，不属服务专有，把按天清理与配额驱逐扩展到该处会误删非本服务文件；
        # save_path 仅决定本次写入位置。
        base_dir = _resolve_base_dir(save_path)
        try:
            cleanup_base_dir: Path | None = resolve_save_root()
        except SeedreamConfigError as exc:
            # 相对与缺省 save_path 的写入目录派生自存储区，其不可解析已在
            # _resolve_base_dir 内先行抛出，此分支仅在绝对 save_path 下可达。
            # 绝对 save_path 不依赖基准，写入不受部署级存储声明可解析性阻塞；
            # 清理边界不可用时清理整体关闭，写入目录可能存放非本服务文件，
            # .part 清扫与空目录回收不区分来源，不得作用于该目录。
            logger.warning("存储区不可解析，本次保存关闭自动清理: {}", exc.message)
            cleanup_base_dir = None
        return _build_auto_save_manager(config, base_dir, cleanup_base_dir, download_manager)

    if images is None:
        images = extract_images(result)
    image_data: list[dict[str, Any]] = []
    saveable_indices: list[int] = []
    for idx, image in enumerate(images):
        if not is_saveable_image(image, spec.data_key):
            continue
        saveable_indices.append(idx)
        # 序号基于可保存图计数，避免失败占位项导致文件名跳号。
        save_ordinal = len(image_data) + 1
        image_data.append(
            {
                spec.data_key: image[spec.data_key],
                "prompt": prompt or "",
                "custom_name": f"{custom_name}_{save_ordinal}" if custom_name else None,
                "alt_text": f"Generated image {save_ordinal}",
            }
        )

    if not image_data:
        logger.warning(spec.empty_warning)
        return [], []

    # manager 构造含同步文件系统调用，经 to_thread 避免阻塞事件循环。
    auto_save_manager = await asyncio.to_thread(_resolve_and_build)
    save_method: BatchSaveMethod = getattr(AutoSaveManager, spec.save_method_name)
    # async with 确保 save 阶段任意异常均释放 manager 自建的下载连接池。
    async with auto_save_manager:
        results = await save_method(auto_save_manager, image_data, tool_name)
        return results, saveable_indices


async def auto_save_from_urls(
    result: dict[str, Any],
    prompt: str | None,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    download_manager: DownloadManager | None = None,
    images: list[dict[str, Any]] | None = None,
) -> AutoSaveOutcome:
    """从 URL 异步下载并保存图片。

    Args:
        prompt: 用于派生保存文件名，图层拆分场景可为 None。
        download_manager: 可选共享下载管理器，复用 aiohttp 连接池，未提供时内部新建。
        images: 调用方预提取的图片列表，None 时从 result 提取。

    Returns:
        (保存结果列表, 可保存图片原始索引列表) 二元组，索引列表供回填阶段按位置
        写入本地路径。

    Raises:
        SeedreamValidationError: save_path 路径无效。
        SeedreamConfigError: 存储区配置无法解析，经 resolve_save_root 抛出。
    """
    return await _auto_save(
        result=result,
        prompt=prompt,
        config=config,
        save_path=save_path,
        custom_name=custom_name,
        tool_name=tool_name,
        spec=_URL_SPEC,
        download_manager=download_manager,
        images=images,
    )


async def auto_save_from_base64(
    result: dict[str, Any],
    prompt: str | None,
    config: SeedreamConfig,
    save_path: str | None,
    custom_name: str | None,
    tool_name: str,
    download_manager: DownloadManager | None = None,
    images: list[dict[str, Any]] | None = None,
) -> AutoSaveOutcome:
    """从 Base64 数据异步解码并保存图片。

    Args:
        prompt: 用于派生保存文件名，图层拆分场景可为 None。
        download_manager: 可选共享下载管理器，复用 aiohttp 连接池，未提供时内部新建。
        images: 调用方预提取的图片列表，None 时从 result 提取。

    Returns:
        (保存结果列表, 可保存图片原始索引列表) 二元组，索引列表供回填阶段按位置
        写入本地路径。

    Raises:
        SeedreamValidationError: save_path 路径无效。
        SeedreamConfigError: 存储区配置无法解析，经 resolve_save_root 抛出。
    """
    return await _auto_save(
        result=result,
        prompt=prompt,
        config=config,
        save_path=save_path,
        custom_name=custom_name,
        tool_name=tool_name,
        spec=_BASE64_SPEC,
        download_manager=download_manager,
        images=images,
    )
