"""目录图片扫描：遍历算法与进程级缓存同域。

find_images_in_directory 为按 normcase 稳定顺序的深度优先遍历，条目预算封顶
物化量；cached_find_images_in_directory 以 (目录路径, recursive, max_depth,
格式过滤元组, 扫描函数, 读范围签名) 为键缓存有序扫描结果，供 browse_images
翻页共享，消除深翻页的重复文件系统扫描。条目存储 ScanEntry（resolved 路径
与 normcase 分量），扫描完成时 resolve 一次，深翻页命中免于逐文件重复 resolve；
结果以冻结 ScanResult 外传，是否扫到目录末尾按原始扫描量判定，
resolve 失败剔除不折损早停事实，不完整成因（截断/停滞/剔除）
由本层单点归一并随缓存条目回放，resolve 剔除数经 skipped_count
随结果外传；resolve 剔除缩短的窗口由本层倍增扩窗跨过坏段，
扩窗有进展时未扫尽且未截断即返回条目不少于请求量，
连续两轮翻倍零新增可解析条目才判停，有限坏段由次轮扩窗跨过，
停滞谱系随缓存条目跨调用累计零进展次数（条目过期未逐出照常接续），非停滞出口归零、
停滞条目量变化从 1 重计，谱系达阈值按永久处理并以 stall_permanent 外传；全窗条目均
被谓词剔除且无 resolve 失败的停滞为确定性重试复现，首答即升永久并随缓存条目持久化；
重探间隔内扫描范围不超冻结窗覆盖量的命中直接回放冻结窗，免注定原地复现的扩窗重扫；
前缀扩展的基数同样取原始扫描量，持续 resolve 失败缩短的缓存列表不使扩展停摆。
调用方 include 谓词的条目剔除与 resolve 剔除同口径进入配额与扩窗判定，
配额按满足谓词的可解析条目计、增长与停滞语义由本层单点拥有；
缓存条目存未过滤的 resolved 窗口，键并入调用方读范围签名，
停滞成因等谓词派生信号不跨读范围回放，同读范围命中按当次谓词过滤后返回，
凑满配额即停，谓词剔除数经 excluded_count 随结果回传；
携带 include 的调用必须配对非空读范围签名，失配拒绝。
非递归扫描以目录 mtime 失效，捕获时已沉淀超 FAT 时间戳粒度窗口的条目
mtime 未变即新鲜，未沉淀条目叠加 TTL 上界兜底粗粒度时间戳；递归扫描改用
TTL 失效，接受短时陈旧换取翻页性能。组内依赖 scan → path、scan →
files 单向。
"""

from __future__ import annotations

import enum
import heapq
import os
import sys
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from ..core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ..core.logs import get_logger
from .files import has_reparse_attribute
from .path import ResolveResultCache, fold_case, is_unc_path, normcase_parts

logger = get_logger()

# 目录扫描的物化条目预算：单次 find_images_in_directory 调用物化的条目（含非
# 图片，重扫趟只计新增物化段）累计超过该值即停止遍历，约束递归广度与前缀物化量；
# heapq.nsmallest 选前缀须消费整个目录迭代器，单目录单趟枚举不在此预算内，由
# 扫描缓存的 mtime+TTL 缓解重复扫描。
_SCAN_ENTRY_BUDGET = 20000


# 进程级目录图片列表缓存。键不含 scan_limit：同目录同扫描配置的不同翻页共享一份
# 有序列表，命中返回浅拷贝供调用方切片；键并入 scanner 身份与调用方读范围签名，
# 注入不同扫描实现或携带不同读范围的调用不共享条目，谓词派生信号不跨读范围回放。
# 条目按 LRU 管理，命中与覆写均刷新热度，超限驱逐最久未使用目录。
# 跨线程并发下 get 与 move_to_end、迭代与 pop 等复合操作不保证原子，竞态由各操作
# 点内联捕获化解，最坏情况为缓存击穿即多请求各扫一次再覆写，仅影响性能。
_ScanCacheKey = tuple[str, bool, int, tuple[str, ...], Callable[..., list[Path]], str]
_DIRECTORY_SCAN_CACHE: OrderedDict[_ScanCacheKey, _DirectoryScanCacheEntry] = OrderedDict()
_DIRECTORY_SCAN_CACHE_MAX_ENTRIES = 64
# 单条目图片列表长度上限：超过的大目录不缓存全量列表，回退每页扫描，避免无界内存占用。
_DIRECTORY_SCAN_CACHE_MAX_LIST_LEN = 10000
# 扫描缓存 TTL：递归扫描以 TTL 失效，子目录新增图片不改变顶层目录 mtime；非递归
# 扫描的未沉淀条目在目录 mtime 之上叠加同一 TTL，兜底粗粒度时间戳。取 30 秒平衡
# 翻页缓存命中与新增图片的可见延迟，过短会使间隔翻页必然全量重扫。
_DIRECTORY_SCAN_CACHE_TTL_SECONDS = 30.0
# FAT/exFAT 目录时间戳的 2 秒粒度窗口。静止超该窗口后任何变更必然使粗粒度
# mtime 前移，据此判定的沉淀条目 mtime 未变即新鲜，无需 TTL 重扫。
_FAT_TIMESTAMP_GRANULARITY_SECONDS = 2.0
# 扫描窗口的几何倍率：命中但前缀不足时按 max(scan_limit, 倍率×最近扫描的原始扫描量)
# 重扫，使第 K 页的前缀扩展按指数预取，累计扫描代价从 O(K²) 降为 O(K)，
# 摊销单次扫描开销；resolve 剔除缩短窗口时同倍率扩窗重扫跨过坏段。
# 基数取 resolve 剔除前的原始扫描量，持续 resolve 失败不使扩展窗口停在坏段之前。
_SCAN_PREFIX_GROWTH_FACTOR = 2
# 扩窗判停的连续零进展轮数：连续两轮翻倍无新增可解析条目才判停，
# 恰占满扩展窗的有限坏段由次轮扩窗跨过，真停滞目录多付一轮翻倍扫描。
_SCAN_STALL_ZERO_PROGRESS_ROUNDS = 2
# 停滞谱系达阈值按永久处理：命中回放冻结窗，仅按重探间隔付一次扩窗重扫；
# 谱系计数容忍线程竞态欠计，仅影响永久态收敛时序。
STALL_PERMANENT_STREAK_THRESHOLD = 3

# raw→ScanEntry 的进程级复用缓存，TTL 过期重扫免逐文件重复 resolve 与分量
# 重建，扩窗重扫的已缓存条目零重算；缓存值同时供回显与读权限判定消费，
# 符号链接改向的陈旧窗口由 TTL 封顶在扫描条目陈旧窗口的两倍。
# 骨架与配置根缓存共用 path 的 ResolveResultCache。
# 扫描 resolve 复用缓存的进程级条目上限：派生自扫描缓存单目录上限，恒容纳
# 一个最大可缓存目录的全量条目。
_RESOLVE_CACHE_TTL_SECONDS = 60.0
_SCAN_RESOLVE_CACHE: ResolveResultCache[ScanEntry] = ResolveResultCache(
    _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN, ttl_seconds=_RESOLVE_CACHE_TTL_SECONDS
)

# resolve 失败键的 TTL 负缓存：坏挂载与链接环在倍增重扫的后续各轮
# 不再逐条重试阻塞调用，到期自动重试；容量与成功缓存同源。失败重试窗口
# 独立于成功缓存 TTL 收紧，坏挂载恢复后条目至多一个短窗口内不可见。
_RESOLVE_FAILURE_TTL_SECONDS = 15.0
_SCAN_RESOLVE_FAILURE_CACHE = ResolveResultCache[bool](
    _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN, ttl_seconds=_RESOLVE_FAILURE_TTL_SECONDS
)
# 永久停滞重探间隔绑定负缓存 TTL：到期前重探仍被负缓存略过，注定原地复现冻结窗。
_STALL_REPROBE_INTERVAL_SECONDS = _RESOLVE_FAILURE_TTL_SECONDS


class ScanIncompleteCause(enum.Enum):
    """扫描结果不完整的成因，由扫描层单点产出并贯穿各消费层。"""

    TRUNCATED = enum.auto()  # 条目预算截断
    STALLED = enum.auto()  # 扩窗停滞早退
    SKIPPED = enum.auto()  # resolve 失败或负缓存剔除条目


class ScanEntry(NamedTuple):
    """扫描与缓存链路的单图片条目。

    Attributes:
        resolved_path: resolve 后路径，展示与包含判定均以其为准。
        resolved_nc_parts: resolved 路径各分量的 normcase 形式，缓存命中回放的
            包含判定免逐条重算。
    """

    resolved_path: Path
    resolved_nc_parts: tuple[str, ...]


@dataclass(frozen=True)
class ScanResult:
    """cached_find_images_in_directory 的冻结返回结构，新扫描信号以加属性扩展。

    Attributes:
        images: 有序扫描条目列表的独立副本，条目形态见 ScanEntry。
        complete: 扫描是否已到目录末尾，按原始扫描量判定。
        incomplete_cause: 结果不完整的成因，完整或普通前缀早停为 None；SKIPPED
            陈述窗口内有条目被剔除，不预设扫尽事实，可与 complete 并存。
        skipped_count: 产出 images 的最近一次扫描窗口内被 resolve 失败
            或负缓存剔除的条目数，供调用方推导可见条目与目录实际条目的差额。
        excluded_count: 同一窗口内被 include 谓词剔除的条目数；扩窗各轮窗口为
            前轮超集，最终轮计数即全程去重总量，命中回放按考查前缀计。
        stall_streak: 同一缓存谱系内连续零进展停滞的累计次数：
            相邻两次停滞的配额口径条目量相同即递增，条目量变化则从 1 重计，非停滞出口
            归零，确定性停滞经 as_permanent 升至永久阈值；随缓存条目回放，供本层判定
            冻结窗回放。
        stall_permanent: 谱系达 STALL_PERMANENT_STREAK_THRESHOLD 的永久态标记，
            由 stall_streak 派生的只读属性，消费方直接读取、不重推阈值；确定性
            停滞经 as_permanent 升级路径置位。
        rescan_futile: 截断且零跳过的徒劳重扫标记，由 incomplete_cause 与
            skipped_count 派生的只读属性；该组合下扩窗与负缓存到期两条恢复
            路径均不存在，续扫注定复现同一前沿。
    """

    images: list[ScanEntry]
    complete: bool
    incomplete_cause: ScanIncompleteCause | None
    skipped_count: int = 0
    excluded_count: int = 0
    stall_streak: int = 0

    @property
    def stall_permanent(self) -> bool:
        """谱系达永久阈值的停滞标记，读取时由 stall_streak 派生。"""
        return self.stall_streak >= STALL_PERMANENT_STREAK_THRESHOLD

    @property
    def rescan_futile(self) -> bool:
        """截断且零跳过的徒劳重扫标记，读取时派生：扩窗与负缓存到期均无从恢复，续扫必复现同一前沿。"""
        return self.incomplete_cause is ScanIncompleteCause.TRUNCATED and self.skipped_count == 0

    def as_permanent(self) -> ScanResult:
        """返回按永久停滞计的副本，其余字段原样保留，谱系计数不因升级回退。

        供确定性停滞（读范围不随重试变化的重试复现）单点升级永久态，
        阈值知识不外泄给消费方。

        Returns:
            stall_streak 升至永久阈值的副本。
        """
        return replace(self, stall_streak=max(self.stall_streak, STALL_PERMANENT_STREAK_THRESHOLD))


def _derive_incomplete_cause(
    *, truncated: bool, stalled: bool, skipped_count: int
) -> ScanIncompleteCause | None:
    """单点归一不完整成因：截断优先于停滞，剔除仅在无早退信号时独立成立。"""
    if truncated:
        return ScanIncompleteCause.TRUNCATED
    if stalled:
        return ScanIncompleteCause.STALLED
    if skipped_count > 0:
        return ScanIncompleteCause.SKIPPED
    return None


@dataclass
class _DirectoryScanCacheEntry:
    """单条目录扫描缓存。

    Attributes:
        mtime_ns: 非递归扫描捕获的目录 mtime 指纹，递归扫描为 None 改用 TTL 失效。
        settled: 非递归扫描捕获时目录 mtime 已静止超 FAT 时间戳粒度窗口的标记，
            mtime 未变即永久新鲜；递归条目恒 False，仅按 TTL 失效。
        captured_at: 缓存写入时的单调时钟时间戳，供 TTL 失效与永久停滞重探窗口判定。
        result: 最近一次扫描的冻结结果，images 为未过滤的 resolved 窗口，resolve
            在扫描完成时执行一次并随条目缓存，可能为目录末尾前的前缀；complete
            恒按原始扫描量取值，条目预算截断的扫描不标记 complete，确保后续更大
            扫描可重新尝试；命中回放经 replace 换独立 images 列表外传，
            resolve 结果变化可插行缩行，消费方不得依赖位置稳定。前缀扩展的基数取
            len(result.images) + result.skipped_count 推导的原始扫描量。
        unreadable_dirs: 本次扫描中因权限或系统错误无法读取的目录列表，随条目缓存，
            缓存命中时同样透传给调用方。
        stalled_included_count: 最近一次停滞结果的配额口径条目量，非停滞出口为
            None；跨调用零进展判定以它为对照基线，接续 result.stall_streak。
    """

    mtime_ns: int | None
    settled: bool
    captured_at: float
    result: ScanResult
    unreadable_dirs: list[Path]
    stalled_included_count: int | None = None


def reset_directory_scan_cache() -> None:
    """清空目录扫描与 resolve 复用（含失败负缓存）的进程级缓存，供测试隔离与进程复位使用。

    runtime._reset_lifespan_state 的复位协议经本函数登记此三处缓存；模块内
    其余名称均为不可变常量或纯函数，无其他需要复位的可变状态。
    """
    _DIRECTORY_SCAN_CACHE.clear()
    _SCAN_RESOLVE_CACHE.clear()
    _SCAN_RESOLVE_FAILURE_CACHE.clear()


def _get_directory_mtime_ns(path: Path) -> int | None:
    """返回目录 mtime 纳秒值，stat 失败返回 None。"""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _is_scan_entry_fresh(
    entry: _DirectoryScanCacheEntry, resolved_dir: Path, recursive: bool
) -> bool:
    """判定缓存条目是否仍然有效。

    递归条目仅按 TTL 失效；非递归条目按目录 mtime 指纹失效，沉淀条目 mtime 未变
    即新鲜，未沉淀条目叠加 TTL 上界兜底粗粒度时间戳。
    """
    if recursive:
        return time.monotonic() - entry.captured_at < _DIRECTORY_SCAN_CACHE_TTL_SECONDS
    if _get_directory_mtime_ns(resolved_dir) != entry.mtime_ns:
        return False
    if entry.settled:
        return True
    return time.monotonic() - entry.captured_at < _DIRECTORY_SCAN_CACHE_TTL_SECONDS


def _resolve_entry(image_path: Path) -> ScanEntry:
    """resolve 单个扫描路径并派生其 normcase 分量。"""
    resolved = image_path.resolve()
    return ScanEntry(resolved, normcase_parts(resolved))


def _resolve_scan_entries(images: list[Path]) -> list[ScanEntry]:
    """将扫描结果的每个原始路径 resolve 一次，返回携带预计算分量的扫描条目。

    resolve 是扫描链路中开销最大的逐文件调用，经进程级 TTL 缓存复用，normcase
    分量随 resolved 同键缓存，TTL 过期重扫免逐文件重复 resolve；网络挂载临时
    不可达与符号链接环等 resolve 失败的条目按更短 TTL 负缓存，倍增重扫的后续
    各轮不再逐条重试，到期自动重试。失败键判定以趟为单位：负缓存未过期键快照
    与趟窗口键集求交，新失败按趟收集后批量登记；快照的单个锁定小节遍历缓存
    条目而非趟窗口，临界区随失败键数封顶，热路径不付逐条目锁往返。
    """
    entries: list[ScanEntry] = []
    # 与首笔失败登记的竞态仅多付一次 resolve 重试。
    recent_failures = _SCAN_RESOLVE_FAILURE_CACHE.fresh_keys()
    new_failures: list[str] = []
    for image_path in images:
        cache_key = str(image_path)
        if cache_key in recent_failures:
            continue
        try:
            entry = _SCAN_RESOLVE_CACHE.get_or_resolve(
                cache_key, lambda: _resolve_entry(image_path)
            )
        except (OSError, ValueError, RuntimeError):
            new_failures.append(cache_key)
            continue
        entries.append(entry)
    if new_failures:
        _SCAN_RESOLVE_FAILURE_CACHE.update((cache_key, True) for cache_key in new_failures)
    return entries


def _extend_unique(target: list[Path], additions: list[Path]) -> None:
    """把新增目录并入收集列表，已存在条目不追加。

    补扫各轮与缓存回放会对同一目录重复报告，去重合并使收集列表恒无重复。
    """
    known = set(target)
    for item in additions:
        if item in known:
            continue
        known.add(item)
        target.append(item)


def _store_scan_entry(
    cache_key: _ScanCacheKey,
    *,
    mtime_ns: int | None,
    settled: bool,
    unreadable_dirs: list[Path],
    result: ScanResult,
    stalled_included_count: int | None,
) -> None:
    """写入扫描缓存，覆写已存在键时刷新 LRU 位，条目数超限时驱逐最近最少使用条目。"""
    if len(result.images) > _DIRECTORY_SCAN_CACHE_MAX_LIST_LEN:
        return
    while len(_DIRECTORY_SCAN_CACHE) >= _DIRECTORY_SCAN_CACHE_MAX_ENTRIES:
        # 驱逐 LRU 链首即最久未命中条目。跨线程并发下另一线程增删键会使 next 抛
        # RuntimeError，取得键后 pop 前键被移除会抛 KeyError，各以内联捕获化解并
        # 循环重试，容量降回上限之下即退出。
        try:
            evict_key = next(iter(_DIRECTORY_SCAN_CACHE))
        except (RuntimeError, StopIteration):
            continue
        try:
            _DIRECTORY_SCAN_CACHE.pop(evict_key)
        except KeyError:
            pass
    # 覆写已存在键时显式刷新 LRU 位：OrderedDict 对既有键赋值保持原位置，否则热
    # 条目滞留旧位置反被优先逐出；键被并发驱逐时 move_to_end 抛 KeyError，静默
    # 放弃后按新键追加。
    try:
        _DIRECTORY_SCAN_CACHE.move_to_end(cache_key)
    except KeyError:
        pass
    _DIRECTORY_SCAN_CACHE[cache_key] = _DirectoryScanCacheEntry(
        mtime_ns=mtime_ns,
        settled=settled,
        captured_at=time.monotonic(),
        result=result,
        unreadable_dirs=unreadable_dirs,
        stalled_included_count=stalled_included_count,
    )


def _filter_included(
    entries: list[ScanEntry],
    include: Callable[[ScanEntry], bool] | None,
    limit: int | None = None,
) -> tuple[list[ScanEntry], int]:
    """按谓词过滤扫描条目，返回 (结果列表, 谓词剔除数)。

    limit 非 None 时凑满即停止考查，谓词求值成本随配额而非缓存窗口规模放大，
    剔除数按考查前缀计；None 时全窗考查。无谓词时维持全量浅拷贝的翻页共享契约
    （limit 失效），剔除数恒 0，返回列表独立于缓存条目，外传不被原地修改篡改。
    """
    if include is None:
        return entries[:], 0
    collected: list[ScanEntry] = []
    excluded = 0
    for entry in entries:
        if include(entry):
            collected.append(entry)
            if limit is not None and len(collected) >= limit:
                break
        else:
            excluded += 1
    return collected, excluded


def read_scope_signature(read_scope: list[Path]) -> str:
    """已 resolve 读范围集合的规范化签名，供调用方并入扫描缓存键。

    排序与去重消除根集合的次序与重复差异；NUL 不出现在合法路径内，作无歧义分隔。
    """
    return "\x00".join(sorted({path.as_posix() for path in read_scope}))


def cached_find_images_in_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    scan_limit: int,
    scanner: Callable[..., list[Path]] | None = None,
    unreadable_dirs: list[Path] | None = None,
    include: Callable[[ScanEntry], bool] | None = None,
    scope_signature: str = "",
) -> ScanResult:
    """扫描目录图片并经进程级缓存翻页共享有序结果，支持前缀增量扩展。

    缓存键不含 scan_limit，同目录同配置的不同翻页共享一份有序列表；键并入 scanner
    身份与调用方读范围签名，注入不同扫描实现或携带不同读范围的调用不共享条目，
    停滞成因等谓词派生信号不跨读范围回放。命中且条目完整或前缀不少于 scan_limit
    时返回浅拷贝，谓词求值凑满 scan_limit 条即停；命中但前缀不足时按几何倍率扩展
    scan_limit 重扫并扩展缓存，扩展基数取最近扫描原始扫描量的推导值（images 与
    skipped_count 之和），持续 resolve 失败缩短的缓存前缀不使扩展停摆；
    resolve 剔除使可解析条目短于请求量时同倍率扩窗重扫跨过坏段，扩窗有进展时
    未扫尽且未截断返回条目不少于请求量，连续两轮翻倍零新增可解析条目才判停；
    全窗条目均被谓词剔除且无 resolve 失败的确定性停滞不等谱系 latch、首答即升
    永久并随缓存条目持久化；停滞谱系达永久阈值且距捕获不足重探间隔、扫描范围
    不超冻结窗覆盖量的命中回放冻结窗，
    不付注定原地复现的扩窗重扫，间隔届满或请求范围更大时落回扩窗重扫并照常接续谱系；
    扫描到目录末尾即标记 complete，后续任意 scan_limit 均不再扫描。
    条目预算截断的扫描未到目录末尾，不按 complete 缓存，后续更大扫描可重新尝试。
    scanner 可注入，默认本模块的 find_images_in_directory。两个出口均返回独立副本，
    调用方原地修改不会篡改缓存；不可读目录随条目缓存并经收集列表去重透传。
    include 谓词与 resolve 剔除同口径由本层端到端承担配额填充：
    足量契约与停滞判定按满足谓词的可解析条目计，扩窗在循环内部完成；
    缓存条目恒存未过滤的 resolved 窗口，同读范围签名内共享，
    命中时按当次谓词过滤后返回。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        scan_limit: 扫描数量上限，用于未命中或前缀扩展时的早停与是否扫到目录末尾
            的判定。
        scanner: 底层扫描函数，签名同 find_images_in_directory；None 时使用
            默认实现。
        unreadable_dirs: 可选收集列表，扫描中无法读取的目录追加至此；缓存命中时
            回放条目内记录的不可读目录。
        include: 可选扫描条目的保留谓词，resolve 后过滤返回条目；None 表示不过滤；
            非 None 时必须配对传入非空 scope_signature。
        scope_signature: 调用方读范围的规范化签名，经 read_scope_signature 从实际
            读范围派生后传入，并入缓存键；不同读范围的调用不共享条目，谓词派生
            信号不跨读范围回放。缺省空串为不分读范围调用的共享键，携带 include
            时不得缺省。

    Returns:
        ScanResult；images 为缓存或本次扫描产出的有序前缀或全量
        且仅含满足谓词的条目，未扫尽且未截断时不少于 scan_limit 条，
        停滞短窗除外，此时 incomplete_cause 为 STALLED，调用方依显式信号退出、
        不得以窗口形态推断停滞；complete 按原始扫描量与截断标记判定，
        谓词剔除与 resolve 失败剔除同样不改变目录已枚举完毕的事实，
        缓存命中回放条目的 complete 与 incomplete_cause，供调用方翻页判定；
        skipped_count 随结果与缓存条目透传窗口内 resolve 剔除数，不含谓词剔除；
        excluded_count 为窗口内谓词剔除数，扩窗各轮窗口为前轮超集，
        计最终轮即全程去重总量，命中回放凑满即停、按考查前缀计；
        stall_streak 为同谱系连续零进展停滞的累计次数，随缓存条目回放，
        非停滞出口归零、停滞条目量变化从 1 重计，条目过期未逐出时谱系照常接续；
        stall_permanent 由 stall_streak 达永久阈值派生（确定性停滞首答即升永久），
        消费方直接读取、不重推阈值；rescan_futile 由截断且零跳过派生，同样直接读取。

    Raises:
        ValueError: include 非 None 而 scope_signature 为缺省空串，谓词派生
            信号必须按读范围隔离，失配拒绝。
        OSError: 底层扫描函数抛出时原样透传，缓存层不吞不包装。
    """
    if include is not None and not scope_signature:
        raise ValueError("include 谓词必须配对传入非空 scope_signature，谓词派生信号须按读范围隔离")
    if scanner is not None:
        scan = scanner
    else:
        scan = find_images_in_directory
    cache_key = (
        str(resolved_dir),
        recursive,
        max_depth,
        tuple(format_filter) if format_filter else (),
        scan,
        scope_signature,
    )
    # 调用方请求量先于命中扩展留底，扩窗足量按原始请求判定。
    requested_limit = scan_limit
    # 停滞基线：未命中无前值可对照，-1 使首轮不可相等。
    previous_included = -1
    cached = _DIRECTORY_SCAN_CACHE.get(cache_key)
    # 命中条目兼作停滞谱系对照：过期未逐出的条目照常接续谱系，永久标记不因 TTL
    # 到期翻转回瞬时；条目被 LRU 逐出或重扫成功改写后即失去原谱系。
    if cached is not None and _is_scan_entry_fresh(cached, resolved_dir, recursive):
        # 命中刷新条目热度。本函数在工作线程执行，get 与 move_to_end 之间可被另一
        # 线程的驱逐 pop 移除该键，KeyError 静默放弃刷新，与 _store_scan_entry 的
        # pop 侧防护对称。
        try:
            _DIRECTORY_SCAN_CACHE.move_to_end(cache_key)
        except KeyError:
            pass
        # 缓存条目存未过滤的 resolved 窗口，按当次谓词过滤后判足量与返回；谓词求值
        # 凑满 scan_limit 条即停，成本随配额而非缓存窗口规模放大，无谓词时
        # 全量浅拷贝维持翻页共享契约。
        hit_window, hit_excluded = _filter_included(cached.result.images, include, scan_limit)
        # 基数取推导的原始扫描量：images 与 skipped_count 之和还原 resolve 剔除前的扫描量，
        # 按缩短后的 images 扩展过不了坏段。
        raw_count = len(cached.result.images) + cached.result.skipped_count
        # 完备或足量命中直接回放；永久停滞的扩窗重扫注定原地复现，重探间隔内请求范围
        # 不超冻结窗覆盖量的命中同样回放冻结窗，不重写条目、不刷新 captured_at，
        # 间隔届满或请求范围更大时落回下方扩窗重扫并照常接续谱系。
        if (
            cached.result.complete
            or len(hit_window) >= scan_limit
            or (
                cached.result.stall_permanent
                and scan_limit <= raw_count
                and time.monotonic() - cached.captured_at < _STALL_REPROBE_INTERVAL_SECONDS
            )
        ):
            # 回放仅替换为独立 images 与当次剔除数，终点与不完整信号随冻结结果原样透传。
            if unreadable_dirs is not None:
                _extend_unique(unreadable_dirs, cached.unreadable_dirs)
            return replace(cached.result, images=hit_window, excluded_count=hit_excluded)
        # 扩展量不受单条目列表上限约束：该上限只决定 _store_scan_entry 是否写入缓存，
        # 若同时截断实际扫描量，超过上限的大目录深翻页会得到短页并被误判为扫完全量。
        scan_limit = max(scan_limit, raw_count * _SCAN_PREFIX_GROWTH_FACTOR)
        # 停滞基线取缓存窗口的谓词满足量：扩窗首轮对照即计入零进展，免多烧一趟
        # 注定无果的加倍列举。
        previous_included = len(hit_window)
    # 扫描前捕获目录 mtime 使指纹与 images 自洽：扫描后捕获会在并发写入时反映新增
    # 而 images 未含，命中时持续返回陈旧列表。递归扫描不依赖 mtime 失效，跳过捕获。
    base_mtime = None if recursive else _get_directory_mtime_ns(resolved_dir)
    # 沉淀判定依据见 _FAT_TIMESTAMP_GRANULARITY_SECONDS 注释。
    settled = base_mtime is not None and (
        time.time() - base_mtime / 1_000_000_000 >= _FAT_TIMESTAMP_GRANULARITY_SECONDS
    )
    scan_unreadable: list[Path] = []
    stalled = False
    zero_progress_rounds = 0
    while True:
        # 每轮全新收集列表后并入累计：从零重扫会重复报告同一不可读目录，
        # 缓存条目不携带重复项。
        round_unreadable: list[Path] = []
        round_truncated: list[bool] = []
        scanned_images = scan(
            directory=str(resolved_dir),
            recursive=recursive,
            max_depth=max_depth,
            extensions=format_filter,
            limit=scan_limit,
            unreadable_dirs=round_unreadable,
            truncated=round_truncated,
        )
        _extend_unique(scan_unreadable, round_unreadable)
        scan_truncated = any(round_truncated)
        # complete 按扫描器原始扫描量判定：resolve 失败与谓词剔除不影响目录
        # 已枚举完毕的事实；条目预算截断的扫描未到目录末尾，不得按 complete 缓存。
        resolved_entries = _resolve_scan_entries(scanned_images)
        included_entries, excluded_count = _filter_included(resolved_entries, include)
        complete = len(scanned_images) < scan_limit and not scan_truncated
        if complete or len(included_entries) >= requested_limit or scan_truncated:
            break
        if len(included_entries) == previous_included:
            zero_progress_rounds += 1
            if zero_progress_rounds >= _SCAN_STALL_ZERO_PROGRESS_ROUNDS:
                # 连续两轮扩窗零新增即停：恰占满扩展窗的有限坏段由次轮扩窗跨过，
                # 坏段贯穿窗口才判停滞；短窗与未扫尽事实照常返回，失败负缓存到期
                # 自动重试。
                stalled = True
                break
        else:
            zero_progress_rounds = 0
        previous_included = len(included_entries)
        # resolve 或谓词剔除使满足配额口径的条目短于请求量：倍增扩窗跨过坏段重扫，
        # 足量契约由本层兑现。
        scan_limit *= _SCAN_PREFIX_GROWTH_FACTOR
    skipped_count = len(scanned_images) - len(resolved_entries)
    stall_streak = 0
    stalled_included_count: int | None = None
    if stalled:
        # 谱系接续：上次停滞的窗口同为该条目量即零进展递增，否则从 1 重计。
        stalled_included_count = len(included_entries)
        stall_streak = 1
        if cached is not None and cached.stalled_included_count == stalled_included_count:
            stall_streak = cached.result.stall_streak + 1
    incomplete_cause = _derive_incomplete_cause(
        truncated=scan_truncated,
        stalled=stalled,
        skipped_count=skipped_count,
    )
    scan_result = ScanResult(
        images=resolved_entries,
        complete=complete,
        incomplete_cause=incomplete_cause,
        skipped_count=skipped_count,
        stall_streak=stall_streak,
    )
    if stalled and not skipped_count and not included_entries:
        # 全窗条目均被谓词剔除的停滞是确定性的：读范围不随重试变化，原地重扫必
        # 复现同一空窗，立即升永久并随缓存条目持久化，重探窗口内免注定复现的
        # 扩窗重扫，不等谱系三次 latch。
        scan_result = scan_result.as_permanent()
    # 递归扫描靠 TTL 失效故总是缓存，mtime 字段留空且不沉淀；非递归仅在 stat 成功
    # 时缓存；条目恒存未过滤的 resolved 窗口，同读范围签名内共享。
    if recursive or base_mtime is not None:
        _store_scan_entry(
            cache_key,
            mtime_ns=base_mtime,
            settled=settled,
            unreadable_dirs=list(scan_unreadable),
            result=scan_result,
            stalled_included_count=stalled_included_count,
        )
    if unreadable_dirs is not None:
        _extend_unique(unreadable_dirs, scan_unreadable)
    return replace(scan_result, images=included_entries, excluded_count=excluded_count)


def find_images_in_directory(
    directory: str,
    recursive: bool = True,
    max_depth: int = 3,
    extensions: list[str] | None = None,
    limit: int | None = None,
    unreadable_dirs: list[Path] | None = None,
    truncated: list[bool] | None = None,
) -> list[Path]:
    """在目录中查找图片文件。

    安全前置条件：本函数不做工作区越界校验，调用方必须先确认 directory 位于允许
    的工作区根之内。UNC 形式的入参与 normalize_path 同口径在 resolve 前拒绝，返回
    空列表并记录告警。
    单次调用物化的条目数（含非图片）受 _SCAN_ENTRY_BUDGET 预算封顶，重扫趟只计
    新增物化段使倍增摊销不翻倍计数，超限丢弃当批、终止遍历并返回已收集结果，向
    truncated 追加截断标记供调用方感知结果不完整；heapq.nsmallest 选前缀须消费
    整个目录迭代器，单目录单趟枚举不在此预算内。
    单个目录的条目列表按需物化：limit 场景只物化排序前缀，非图片条目占位致结果
    不足且目录未扫尽时倍增前缀重扫，重扫趟按已处理路径去重以容忍两趟间的目录
    并发变更，无 limit 时一次物化全量有序列表；limit 亦使
    跨目录递归提前终止，重复扫描的成本由 mtime 加 TTL 缓存缓解。

    Args:
        directory: 搜索目录。
        recursive: 是否递归搜索。
        max_depth: 最大搜索深度。
        extensions: 指定的文件扩展名列表。
        limit: 返回数量上限，<=0 时返回空列表；扫描按 normcase 稳定顺序，凑够即提前停止。
        unreadable_dirs: 可选收集列表，不可读目录追加至此供调用方区分「目录不可读」
            与「目录内无图片」；未提供时仅记日志跳过。
        truncated: 可选截断标记收集列表，扫描因条目预算截断时追加 True，供调用方
            区分「扫完全量」与「截断的部分结果」。

    Returns:
        找到的图片文件路径列表。目录不存在或预检失败时返回空列表。

    Raises:
        OSError: 扫描中途的文件系统错误向上传播，供调用方区分「扫完」与「中途
            出错」；单个目录不可读经 unreadable_dirs 收集后跳过，不视为失败。
    """
    images: list[Path] = []

    if limit is not None and limit <= 0:
        return images

    if is_unc_path(directory):
        # 与 normalize_path 等 resolve 站点同口径在 resolve 前拦截。
        logger.warning("拒绝 UNC 形式的目录扫描入参，返回空结果: {}", directory)
        return images

    try:
        dir_path = Path(directory).resolve()

        if not dir_path.exists() or not dir_path.is_dir():
            logger.warning("目录不存在或不是目录: {}", directory)
            return images
    except Exception as e:
        logger.error("搜索图片文件失败 {}: {}", directory, e)
        return images

    target_extensions = set(extensions) if extensions else SUPPORTED_IMAGE_EXTENSIONS
    target_extensions = {ext.lower() for ext in target_extensions}

    # 无上限时记为 -1 表示收集全部。
    target_count = limit if limit is not None else -1

    scanned_entries = 0

    def scan_directory(path: Path, current_depth: int = 0) -> bool:
        """按 normcase 稳定顺序深度优先扫描；凑够 target_count 或条目超预算即返回 True 终止。"""
        nonlocal scanned_entries
        if current_depth > max_depth:
            return False

        # 排序前缀按需扩展：heapq.nsmallest 与 sorted 前缀同序，物化量与前缀长度
        # 成正比而非目录全量；无 limit 时一次全量排序。
        prefix_len = target_count
        processed: set[str] = set()
        while True:
            try:
                with os.scandir(path) as it:
                    if target_count >= 0:
                        entries = heapq.nsmallest(
                            prefix_len,
                            it,
                            key=lambda entry: fold_case(entry.path),
                        )
                    else:
                        entries = sorted(it, key=lambda entry: fold_case(entry.path))
            except OSError as e:
                logger.warning("无法访问目录 {}: {}", path, e)
                if unreadable_dirs is not None:
                    unreadable_dirs.append(path)
                return False

            # 重扫趟按已处理路径取新增条目：两趟间目录并发变更使排序前缀漂移时，
            # 已物化条目不重复入列、前移的新条目不漏采。
            fresh_entries = []
            for entry in entries:
                if entry.path in processed:
                    continue
                processed.add(entry.path)
                fresh_entries.append(entry)
            # 条目预算按物化量累计：重扫趟只按新增条目计费，目录条目数可超预算
            # 而分页不中断；超限丢弃本批并终止遍历。
            scanned_entries += len(fresh_entries)
            if scanned_entries > _SCAN_ENTRY_BUDGET:
                logger.warning(
                    "目录扫描条目数超过预算 {}，停止遍历并返回已收集结果: {}",
                    _SCAN_ENTRY_BUDGET,
                    path,
                )
                if truncated is not None:
                    truncated.append(True)
                return True

            for entry in fresh_entries:
                entry_path = Path(entry.path)
                # follow_symlinks=False：不跟随符号链接，避免符号链接环与经由符号链接越界遍历。
                if (
                    entry.is_file(follow_symlinks=False)
                    and entry_path.suffix.lower() in target_extensions
                ):
                    # OneDrive 占位文件等 reparse 非 symlink，is_file 不拒绝，与目录分支
                    # 同规则剔除；复用遍历条目的 lstat 结果判定，不付逐文件二次 lstat，
                    # 后缀命中后才判定，非图片条目不付 stat 开销。reparse 判定仅
                    # Windows 有意义，POSIX 上短路跳过 stat 求值。
                    if sys.platform == "win32" and has_reparse_attribute(
                        entry.stat(follow_symlinks=False)
                    ):
                        logger.warning("跳过 reparse point 文件: {}", entry_path)
                        continue
                    images.append(entry_path)
                    if target_count >= 0 and len(images) >= target_count:
                        return True
                elif (
                    entry.is_dir(follow_symlinks=False) and recursive and current_depth < max_depth
                ):
                    # junction 等 reparse 非 symlink，is_dir 不拒绝，与文件分支同口径
                    # 复用遍历条目的 no-follow stat 判定；POSIX 上短路跳过 stat 求值。
                    if sys.platform == "win32" and has_reparse_attribute(
                        entry.stat(follow_symlinks=False)
                    ):
                        logger.warning("跳过 reparse point 目录: {}", entry_path)
                        continue
                    if scan_directory(entry_path, current_depth + 1):
                        return True

            if target_count < 0 or len(entries) < prefix_len:
                # 无 limit 或返回条目少于前缀长度即目录已扫尽，不存在可扩展前缀。
                return False
            prefix_len *= 2

    scan_directory(dir_path)

    return images


def suggest_similar_paths(target_path: str, search_dirs: list[str] | None = None) -> list[str]:
    """在搜索目录下建议与目标路径拼写相近的图片路径，供路径校验失败时纠错。

    扫描经 mtime/TTL 缓存，重复的校验失败不重复全量扫描目录。

    Args:
        target_path: 目标路径。
        search_dirs: 搜索目录列表；未提供时返回空列表，强制调用方显式指定边界，
            避免公开导出后以 CWD 为界泄露本地图片文件名。

    Returns:
        相似路径建议列表取 resolve 后路径，最多 5 条。目标文件名归一为空串时
        不产生建议，避免空串子串匹配误报。
    """
    suggestions: list[str] = []

    try:
        target_name = Path(target_path).name.lower()
        if not target_name:
            return suggestions
        search_directories = search_dirs or []

        for search_dir in search_directories:
            # UNC 形式的搜索目录在 resolve 前跳过，与 find_images_in_directory 的
            # 入参拦截同口径，避免建议扫描触发 SMB 连接。
            if is_unc_path(search_dir):
                logger.warning("拒绝 UNC 形式的建议搜索目录，跳过该目录: {}", search_dir)
                continue
            resolved_dir = Path(search_dir).resolve()
            matched_entries = cached_find_images_in_directory(
                resolved_dir=resolved_dir,
                recursive=True,
                max_depth=2,
                format_filter=None,
                scan_limit=500,
                scanner=find_images_in_directory,
            ).images

            for entry in matched_entries:
                if target_name in entry.resolved_path.name.lower():
                    suggestions.append(str(entry.resolved_path))

                if len(suggestions) >= 5:
                    break

            if len(suggestions) >= 5:
                break

    except Exception as e:
        logger.error("生成路径建议失败: {}", e)

    return suggestions
