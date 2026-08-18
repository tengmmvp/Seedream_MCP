"""目录图片扫描的进程级缓存。

以 (目录路径, recursive, max_depth, 格式过滤元组) 为键缓存有序扫描结果，供 browse_images
翻页共享，消除深翻页的重复文件系统扫描。缓存条目存储 (原始路径, resolved 路径) 对，扫描
完成时对每个原始路径 resolve 一次，深翻页命中缓存免于逐文件重复 resolve。扫描底层函数由
调用方注入，本模块只负责缓存策略与失效判定。非递归扫描以目录 mtime 失效，新增图片立即
反映；递归扫描因子目录变更不改变顶层 mtime，改用 TTL 失效，接受短时陈旧换取翻页性能。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.logs import get_logger
from .io_path import find_images_in_directory

logger = get_logger(__name__)


# 进程级目录图片列表缓存。键为 (目录路径, recursive, max_depth, 格式过滤元组)，不含
# scan_limit：同目录同扫描配置的不同翻页共享一份有序列表，命中时返回浅拷贝供调用方切片，
# 将深翻页从每页扫描降为首次扫描加 O(1) 命中。条目按 LRU 管理：命中与覆写均刷新热度，
# 超限驱逐最久未使用目录，轮询目录数超过上限时热目录不因先插入而被逐出。单事件循环内
# 各请求按目录串行 await，跨线程并发调用下 GIL 仅保证单键读写原子，get 与 move_to_end、
# 迭代与 pop 等复合操作不保证原子，竞态由各操作点内联捕获化解，最坏情况为缓存击穿即多
# 请求各扫一次再覆写，仅影响性能。
_DIRECTORY_SCAN_CACHE: OrderedDict[
    tuple[str, bool, int, tuple[str, ...]], _DirectoryScanCacheEntry
] = OrderedDict()
_DIRECTORY_SCAN_CACHE_MAX_ENTRIES = 64
# 单条目图片列表长度上限：超过的大目录不缓存全量列表，回退每页扫描，避免无界内存占用。
_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN = 10000
# 递归扫描缓存 TTL：子目录新增图片不改变顶层目录 mtime，以 TTL 兜底保证最终一致。
# 取 30 秒平衡翻页缓存命中与新增图片的可见延迟，过短会使间隔翻页必然全量重扫。
_DIRECTORY_SCAN_CACHE_TTL_SECONDS = 30.0
# 前缀扩展的几何倍率：命中但前缀不足时按 max(scan_limit, 倍率×已缓存条数) 重扫，
# 使第 K 页的前缀扩展按指数预取，累计扫描代价从 O(K²) 降为 O(K)，摊销单次扫描开销。
_SCAN_PREFIX_GROWTH_FACTOR = 2


@dataclass
class _DirectoryScanCacheEntry:
    """单条目录扫描缓存。

    Attributes:
        mtime_ns: 非递归扫描捕获的目录 mtime 指纹，递归扫描为 None 改用 TTL 失效。
        captured_at: 缓存写入时的单调时钟时间戳，供 TTL 失效判定。
        images: 有序 (原始路径, resolved 路径) 对列表，resolve 在扫描完成时执行一次并
            随条目缓存，可能为目录末尾前的稳定前缀。
        complete: images 是否已扫到目录末尾；False 时为稳定前缀，随更大 scan_limit
            重扫扩展，回看与同范围重复请求直接命中。
        unreadable_dirs: 本次扫描中因权限或系统错误无法读取的目录列表，随条目缓存，
            缓存命中时同样透传给调用方。
    """

    mtime_ns: int | None
    captured_at: float
    images: list[tuple[Path, Path]]
    complete: bool
    unreadable_dirs: list[Path]


def reset_directory_scan_cache() -> None:
    """清空目录扫描的进程级缓存，供测试隔离与进程复位使用。

    清除 _DIRECTORY_SCAN_CACHE 内全部条目；模块内其余名称均为不可变常量或纯函数，
    无其他需要复位的模块级可变状态。resources._reset_lifespan_state 的复位协议经
    本函数登记此缓存。
    """
    _DIRECTORY_SCAN_CACHE.clear()


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


def _resolve_scan_pairs(images: list[Path]) -> list[tuple[Path, Path]]:
    """将扫描结果的每个原始路径 resolve 一次，返回 (原始路径, resolved 路径) 对列表。

    resolve 是扫描链路中开销最大的逐文件文件系统调用，故只在扫描完成时执行一次并随
    缓存条目共享，深翻页命中缓存时调用方直接取已 resolve 对。resolve 失败的条目
    （OSError/ValueError，如网络挂载目录临时不可达）跳过不缓存，待下次扫描重试。
    """
    pairs: list[tuple[Path, Path]] = []
    for image_path in images:
        try:
            pairs.append((image_path, image_path.resolve()))
        except (OSError, ValueError):
            continue
    return pairs


def _store_scan_entry(
    cache_key: tuple[str, bool, int, tuple[str, ...]],
    *,
    mtime_ns: int | None,
    images: list[tuple[Path, Path]],
    complete: bool,
    unreadable_dirs: list[Path],
) -> None:
    """写入扫描缓存，覆写已存在键时刷新 LRU 位，条目数超限时驱逐最近最少使用条目。"""
    if len(images) > _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN:
        # 超过单条目列表上限的大目录不缓存，回退每页扫描，避免无界内存占用。
        return
    while len(_DIRECTORY_SCAN_CACHE) >= _DIRECTORY_SCAN_CACHE_MAX_ENTRIES:
        # 驱逐 LRU 链首即最久未命中条目。本函数经 to_thread 跨线程并发调用，另一
        # 线程在迭代器存活期间增删键会使 next 抛 RuntimeError，取得键之后 pop 之前
        # 键被移除会抛 KeyError；两种竞态各以内联捕获化解并循环重试，容量降回上限
        # 之下即退出。
        try:
            evict_key = next(iter(_DIRECTORY_SCAN_CACHE))
        except (RuntimeError, StopIteration):
            continue
        try:
            _DIRECTORY_SCAN_CACHE.pop(evict_key)
        except KeyError:
            pass
    # 覆写已存在键时显式刷新 LRU 位：OrderedDict 对既有键赋值保持条目原位置，TTL
    # 过期重扫覆写后热条目会滞留旧位置，缓存压力下最常访问目录反被优先逐出。键在
    # 判定后写入前被并发驱逐时 move_to_end 抛 KeyError，静默放弃后按新键追加。
    try:
        _DIRECTORY_SCAN_CACHE.move_to_end(cache_key)
    except KeyError:
        pass
    _DIRECTORY_SCAN_CACHE[cache_key] = _DirectoryScanCacheEntry(
        mtime_ns=mtime_ns,
        captured_at=time.monotonic(),
        images=images,
        complete=complete,
        unreadable_dirs=unreadable_dirs,
    )


def cached_find_images_in_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    scan_limit: int,
    scanner: Callable[..., list[Path]] | None = None,
    unreadable_dirs: list[Path] | None = None,
) -> list[tuple[Path, Path]]:
    """扫描目录图片并经进程级缓存翻页共享有序结果，支持前缀增量扩展。

    缓存键不含 scan_limit，同目录同扫描配置的不同翻页共享一份有序列表。命中且条目完整或
    前缀不少于 scan_limit 时返回浅拷贝，将深翻页从每页文件系统扫描降为首次扫描加 O(1) 命中。
    命中但前缀不足 scan_limit 时按更大 scan_limit 重扫并扩展缓存，大目录深翻页每页至多一次
    扫描，回看与同范围重复请求直接命中；扫描到目录末尾即标记 complete，后续任意 scan_limit
    均不再扫描。scanner 可注入，默认使用 io_path.find_images_in_directory，便于调用方在
    自身模块作用域内替换底层扫描。命中与未命中两个出口均返回独立 list 副本，调用方对返回值
    原地修改不会篡改缓存内列表。不可读目录信号随条目缓存并经 unreadable_dirs 收集器透传，
    缓存命中时同样可得。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        scan_limit: 扫描数量上限，用于未命中或前缀扩展时的早停与是否扫到目录末尾的判定。
        scanner: 底层扫描函数，签名同 io_path.find_images_in_directory；None 时使用默认实现。
        unreadable_dirs: 可选收集列表，扫描中无法读取的目录追加至此；缓存命中时回放条目内
            记录的不可读目录，深翻页与首页获取一致的不可读信号。

    Returns:
        排序后的 (原始路径, resolved 路径) 元组列表，resolve 在扫描完成时执行一次并随缓存
        共享，深翻页命中缓存不再逐文件 resolve；缓存命中时为已缓存的有序前缀或全量，
        未命中时至多 scan_limit 条。
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
        # 命中刷新条目热度为最近使用，缓存超限时驱逐最久未命中目录。本函数经 to_thread
        # 在工作线程执行，get 与 move_to_end 之间可被另一线程的驱逐 pop 移除该键，
        # KeyError 静默放弃热度刷新即可，与 _store_scan_entry 的 pop 侧防护对称。
        try:
            _DIRECTORY_SCAN_CACHE.move_to_end(cache_key)
        except KeyError:
            pass
        # 完整列表或前缀已覆盖本次 scan_limit 时直接复用，前缀不足则按几何倍率扩展的
        # scan_limit 重扫，摊销深翻页的累计扫描代价。切片返回独立副本，调用方原地
        # 修改不会篡改缓存内列表。
        if cached.complete or len(cached.images) >= scan_limit:
            if unreadable_dirs is not None:
                unreadable_dirs.extend(cached.unreadable_dirs)
            return cached.images[:]
        # 前缀不足时按几何倍率扩展 scan_limit 重扫，摊销深翻页的累计扫描代价。扩展量
        # 不受单条目列表上限约束：该上限只决定扫描结果是否写入缓存（见 _store_scan_entry），
        # 若同时截断实际扫描量，超过上限的大目录深翻页会得到短页并被误判为扫完全量。
        scan_limit = max(scan_limit, len(cached.images) * _SCAN_PREFIX_GROWTH_FACTOR)
    # 扫描前捕获目录 mtime，使缓存写入的指纹与 images 自洽：扫描与 stat 之间若有并发写入，
    # 扫描后捕获的 mtime 会反映新增而 images 未含，命中时持续返回陈旧列表。递归扫描不依赖
    # mtime 失效，跳过捕获。
    base_mtime = None if recursive else _get_directory_mtime_ns(resolved_dir)
    scan_unreadable: list[Path] = []
    scanned_images = scan(
        directory=str(resolved_dir),
        recursive=recursive,
        max_depth=max_depth,
        extensions=format_filter,
        limit=scan_limit,
        unreadable_dirs=scan_unreadable,
    )
    # 扫描完成时对全部原始路径 resolve 一次并缓存 (原始, resolved) 对，翻页命中直接复用。
    # complete 按扫描器的原始返回量判定：resolve 失败被剔除不影响目录已枚举完毕的事实。
    images = _resolve_scan_pairs(scanned_images)
    complete = len(scanned_images) < scan_limit
    # 递归扫描靠 TTL 失效故总是缓存，mtime 字段留空；非递归仅在 stat 成功时缓存。
    if recursive or base_mtime is not None:
        _store_scan_entry(
            cache_key,
            mtime_ns=base_mtime,
            images=images,
            complete=complete,
            unreadable_dirs=list(scan_unreadable),
        )
    if unreadable_dirs is not None:
        unreadable_dirs.extend(scan_unreadable)
    # 新扫描结果已存入缓存本体，切片返回独立副本，调用方原地修改不会篡改缓存。
    return images[:]
