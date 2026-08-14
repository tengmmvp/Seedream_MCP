"""目录图片扫描的进程级缓存。

以 (目录路径, recursive, max_depth, 格式过滤元组) 为键缓存有序扫描结果，供 browse_images
翻页共享，消除深翻页的重复文件系统扫描。扫描底层函数由调用方注入，本模块只负责缓存策略
与失效判定。非递归扫描以目录 mtime 失效，新增图片立即反映；递归扫描因子目录变更不改变
顶层 mtime，改用 TTL 失效，接受短时陈旧换取翻页性能。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.logs import get_logger
from .io_path import find_images_in_directory

logger = get_logger(__name__)


# 进程级目录图片列表缓存。键为 (目录路径, recursive, max_depth, 格式过滤元组)，不含
# scan_limit：同目录同扫描配置的不同翻页共享一份有序列表，命中时返回浅拷贝供调用方切片，
# 将深翻页从每页扫描降为首次扫描加 O(1) 命中。单事件循环内各请求按目录串行 await，跨请求
# 并发经由 GIL 保证 dict 读写原子性，最坏情况为缓存击穿即多请求各扫一次再覆写，仅影响性能。
_DIRECTORY_SCAN_CACHE: dict[tuple[str, bool, int, tuple[str, ...]], _DirectoryScanCacheEntry] = {}
_DIRECTORY_SCAN_CACHE_MAX_ENTRIES = 64
# 单条目图片列表长度上限：超过的大目录不缓存全量列表，回退每页扫描，避免无界内存占用
_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN = 10000
# 递归扫描缓存 TTL：子目录新增图片不改变顶层目录 mtime，以 TTL 兜底保证最终一致。
_DIRECTORY_SCAN_CACHE_TTL_SECONDS = 5.0


@dataclass
class _DirectoryScanCacheEntry:
    """单条目录扫描缓存。

    mtime_ns 为非递归扫描捕获的目录 mtime 指纹，递归扫描留 None 改用 TTL 失效。images 为
    有序扫描结果，complete 标记其是否已扫到目录末尾；False 时 images 为稳定前缀，随更大
    scan_limit 重扫扩展，回看与同范围重复请求直接命中。
    """

    mtime_ns: int | None
    captured_at: float
    images: list[Path]
    complete: bool


def _get_directory_mtime_ns(path: Path) -> int | None:
    """返回目录 mtime 纳秒值，stat 失败返回 None。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _is_scan_entry_fresh(
    entry: _DirectoryScanCacheEntry, resolved_dir: Path, recursive: bool
) -> bool:
    """判定缓存条目是否仍然有效：递归按 TTL，非递归按目录 mtime 指纹。"""
    if recursive:
        return time.monotonic() - entry.captured_at < _DIRECTORY_SCAN_CACHE_TTL_SECONDS
    return _get_directory_mtime_ns(resolved_dir) == entry.mtime_ns


def _store_scan_entry(
    cache_key: tuple[str, bool, int, tuple[str, ...]],
    *,
    mtime_ns: int | None,
    images: list[Path],
    complete: bool,
) -> None:
    """写入扫描缓存，条目数超限时驱逐最旧条目。"""
    if len(images) > _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN:
        # 超过单条目列表上限的大目录不缓存，回退每页扫描，避免无界内存占用
        return
    if len(_DIRECTORY_SCAN_CACHE) >= _DIRECTORY_SCAN_CACHE_MAX_ENTRIES:
        # 驱逐最旧条目；并发 to_thread 下 next(iter()) 与 pop 可能竞态抛 KeyError，捕获容错
        try:
            _DIRECTORY_SCAN_CACHE.pop(next(iter(_DIRECTORY_SCAN_CACHE)))
        except KeyError:
            pass
    _DIRECTORY_SCAN_CACHE[cache_key] = _DirectoryScanCacheEntry(
        mtime_ns=mtime_ns,
        captured_at=time.monotonic(),
        images=images,
        complete=complete,
    )


def _cached_find_images_in_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    scan_limit: int,
    scanner: Callable[..., list[Path]] | None = None,
) -> list[Path]:
    """带进程级缓存的目录图片扫描，翻页共享有序结果并支持前缀增量扩展。

    缓存键不含 scan_limit，同目录同扫描配置的不同翻页共享一份有序列表。命中且条目完整或
    前缀不少于 scan_limit 时返回浅拷贝，将深翻页从每页文件系统扫描降为首次扫描加 O(1) 命中。
    命中但前缀不足 scan_limit 时按更大 scan_limit 重扫并扩展缓存，大目录深翻页每页至多一次
    扫描，回看与同范围重复请求直接命中；扫描到目录末尾即标记 complete，后续任意 scan_limit
    均不再扫描。scanner 可注入，默认使用 path_utils.find_images_in_directory，便于调用方在
    自身模块作用域内替换底层扫描。命中与未命中两个出口均返回独立 list 副本，调用方对返回值
    原地修改不会篡改缓存内列表。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        scan_limit: 扫描数量上限，用于未命中或前缀扩展时的早停与是否扫到目录末尾的判定。
        scanner: 底层扫描函数，签名同 path_utils.find_images_in_directory；None 时使用默认实现。

    Returns:
        排序后的图片路径列表，缓存命中时为已缓存的有序前缀或全量，未命中时至多 scan_limit 条。
    """
    cache_key = (
        str(resolved_dir),
        recursive,
        max_depth,
        tuple(format_filter) if format_filter else (),
    )
    scan = scanner if scanner is not None else find_images_in_directory
    cached = _DIRECTORY_SCAN_CACHE.get(cache_key)
    if cached is not None and _is_scan_entry_fresh(cached, resolved_dir, recursive):
        # 完整列表或前缀已覆盖本次 scan_limit 时直接复用，前缀不足则按更大 scan_limit 重扫扩展。
        # 切片返回独立副本，调用方原地修改不会篡改缓存内列表。
        if cached.complete or len(cached.images) >= scan_limit:
            return cached.images[:]
    # 扫描前捕获目录 mtime，使缓存写入的指纹与 images 自洽：扫描与 stat 之间若有并发写入，
    # 扫描后捕获的 mtime 会反映新增而 images 未含，命中时持续返回陈旧列表。递归扫描不依赖
    # mtime 失效，跳过捕获。
    base_mtime = None if recursive else _get_directory_mtime_ns(resolved_dir)
    images = scan(
        directory=str(resolved_dir),
        recursive=recursive,
        max_depth=max_depth,
        extensions=format_filter,
        limit=scan_limit,
    )
    complete = len(images) < scan_limit
    # 递归扫描靠 TTL 失效故总是缓存，mtime 字段留空；非递归仅在 stat 成功时缓存
    if recursive or base_mtime is not None:
        _store_scan_entry(
            cache_key,
            mtime_ns=base_mtime,
            images=images,
            complete=complete,
        )
    # 新扫描结果已存入缓存本体，切片返回独立副本，调用方原地修改不会篡改缓存
    return images[:]
