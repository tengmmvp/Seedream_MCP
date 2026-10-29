"""图片浏览工具处理器。

读权限求值（工作区 ∪ 图片目录）、请求目录解析、经 include 谓词下沉扫描层的越界
过滤与配额填充、分页与 structuredContent 装配，外加薄壳入口 ``handle_browse_images``
的异常兜底降级；不经 ``execute_generation_handler`` 生成流水线，字段规则与校验由
``BrowseImagesInput`` 单一定义。
"""

from __future__ import annotations

import asyncio
import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from mcp.types import CallToolResult

from ...definitions.conventions import BROWSE_FAILED_ERROR_TYPE, BROWSE_IMAGES_TOOL_NAME
from ...utils.core.errors import format_error_for_user
from ...utils.core.sanitizers import (
    CONTROL_CHARS_PATTERN,
    sanitize_data_text,
    sanitize_error_text,
)
from ...utils.core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ...utils.core.logs import get_logger
from ...utils.io.path import (
    get_read_context,
    get_workspace_roots,
    is_within_any,
    is_within_any_nc_parts,
    is_within_resolved,
    normcase_parts,
    read_scope_denial_message,
    resolve_against_base,
)
from ...utils.io.scan import (
    ScanEntry,
    ScanIncompleteCause,
    ScanResult,
    _scan_resolved_directory,
    cached_find_images_in_directory,
    read_scope_signature,
)
from ..pipeline._failures import log_tiered_failure
from ..pipeline._progress import (
    PROGRESS_COMPLETE,
    PROGRESS_SCAN_START,
    safe_report_progress,
)
from ..outputs import (
    BrowseImagesStructuredOutput,
    build_error_dict,
    build_structured_tool_result,
)
from ..schemas import BrowseImagesInput

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = get_logger()

# 空结果文案中不可读目录的列举上限，超出部分按计数提示，全部明细保留在日志。
_MAX_UNREADABLE_DIR_LISTING = 5


# TRUNCATED 的文本通道可见标记为固定文案；SKIPPED 与 STALLED 依赖伴随信号，
# 在 _incomplete_marker_text 内拼出。
_TRUNCATED_MARKER_TEXT = "目录条目过多，结果可能不完整"
# 未扫尽页的续扫导引前缀，空结果与成功两条装配通道共用。
_CONTINUE_SCAN_HINT_TEXT = "扫描未完成，继续扫描请传 offset="


def _incomplete_marker_text(scan_result: ScanResult | None) -> str | None:
    """从扫描结果的不完整成因派生文本通道的可见标记，未扫描或无成因时为 None。

    STALLED 且 skipped_count 为 0 时窗口内无 resolve 失败，停滞全部来自读权限
    越界剔除的永久状态，不以「暂时」表述诱导模型按瞬时故障重试；
    stall_permanent 置位的停滞同样撤下「暂时」表述。
    """
    if scan_result is None or scan_result.incomplete_cause is None:
        return None
    cause = scan_result.incomplete_cause
    if cause is ScanIncompleteCause.SKIPPED:
        return f"部分图片暂时无法访问（跳过 {scan_result.skipped_count} 张），结果可能不完整"
    if cause is ScanIncompleteCause.STALLED:
        if not scan_result.skipped_count:
            return "部分图片不在读取范围内，结果可能不完整"
        if scan_result.stall_permanent:
            return "部分图片持续无法访问，结果可能不完整"
        return "部分图片暂时无法访问，结果可能不完整"
    return _TRUNCATED_MARKER_TEXT


@dataclass(frozen=True)
class _BrowseRequestState:
    """单次浏览请求的状态快照，供成功、空结果与错误分支共享取值。

    Attributes:
        workspace_roots: 本次请求生效的工作区根列表，会话声明时为 MCP Roots，
            否则为环境回退根，保留原始形态供回显。
        directory: 请求目录字符串，未提供时归一为 "."。
        resolved_directories: 外层创建的共享列表，解析结果逐步填充，异常兜底分支
            经同一引用读取。
        recursive: 是否递归扫描子目录。
        max_depth: 递归扫描的最大深度。
        limit: 单页图片数量上限。
        offset: 分页起始偏移。
        show_details: 图片条目是否附带大小与修改时间详情。
        format_filter: 生效的扩展名过滤值，部分受支持时仅保留受支持后缀，全部不受
            支持时保留原始输入供回显；None 表示不限制。
    """

    workspace_roots: list[Path]
    directory: str
    resolved_directories: list[Path]
    recursive: bool
    max_depth: int
    limit: int
    offset: int
    show_details: bool
    format_filter: list[str] | None

    @classmethod
    def from_params(
        cls,
        params: BrowseImagesInput,
        *,
        workspace_roots: list[Path],
        resolved_directories: list[Path],
        format_filter: list[str] | None,
    ) -> _BrowseRequestState:
        """从类型化入参模型与既有状态构建请求快照。

        Args:
            params: 经 pydantic 校验的工具输入模型。
            workspace_roots: 本次请求生效的工作区根列表。
            resolved_directories: 外层创建的共享已解析目录列表。
            format_filter: 经支持列表过滤后的扩展名白名单。

        Returns:
            请求状态快照。
        """
        return cls(
            workspace_roots=workspace_roots,
            directory=params.effective_directory,
            resolved_directories=resolved_directories,
            recursive=params.recursive,
            max_depth=params.max_depth,
            limit=params.limit,
            offset=params.offset,
            show_details=params.show_details,
            format_filter=format_filter,
        )


def _format_file_info(
    display_path: str,
    entry: ScanEntry,
    show_details: bool,
) -> tuple[str, dict[str, Any]]:
    """格式化单个扫描条目的展示文本与结构化详情字段。

    show_details 为真时附带大小与修改时间，对条目的 resolved_path 活体 stat；
    文本格式为「路径 | 大小 | 修改时间」，结构化详情含 size_mb 与 modified
    两键；stat 或时间戳解析失败时降级为「文件信息不可用」。show_details
    为假时仅返回路径文本与空详情字典。

    Args:
        display_path: 展示给用户的路径字符串。
        entry: 待展示的扫描条目。
        show_details: 是否附带大小与修改时间详情。

    Returns:
        (展示文本, 结构化详情字典) 二元组，无详情时字典为空。
    """
    if not show_details:
        return display_path, {}
    try:
        stat_result = entry.resolved_path.stat()
    except OSError:
        return f"{display_path} | 文件信息不可用", {"size_mb": None, "modified": None}
    size_mb = stat_result.st_size / (1024 * 1024)
    # astimezone 为 naive 本地时间标注时区，输出携带 UTC 偏移以消除歧义；负值或
    # 超范围的畸形时间戳与 stat 失败同样降级，不落入兜底错误分支。
    try:
        mtime = (
            datetime.datetime.fromtimestamp(stat_result.st_mtime)
            .astimezone()
            .isoformat(sep=" ", timespec="seconds")
        )
    except (ValueError, OSError, OverflowError):
        return f"{display_path} | 文件信息不可用", {"size_mb": None, "modified": None}
    return (
        f"{display_path} | {size_mb:.2f} MB | 修改: {mtime}",
        {"size_mb": size_mb, "modified": mtime},
    )


def _build_browse_structured_result(
    state: _BrowseRequestState,
    *,
    status: str,
    success: bool = True,
    images: list[dict[str, Any]] | None = None,
    total_count: int | None = None,
    has_more: bool | None = None,
    next_offset: int | None = None,
    rescan_futile: bool = False,
    scan_result: ScanResult | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """集中构建 browse_images 的 structuredContent，成功、空结果与失败三分支共用。

    请求回显字段取自 state，差异字段经关键字参数传入。经
    ``BrowseImagesStructuredOutput`` 构造后 model_dump，使输出与声明的 outputSchema
    绑定，字段漂移在构造时暴露。

    Args:
        state: 单次浏览请求的状态快照，提供回显字段。
        status: 执行状态标签，completed、empty 或 failed。
        success: 是否成功，失败分支为 False。
        images: 当前页结构化图片条目，None 视为空列表。
        total_count: 匹配图片总数，未扫完全量时为 None。
        has_more: 是否仍有更多图片。
        next_offset: 下一页或未扫尽页续扫的起始偏移，无更多图片、截断徒劳页或永久停滞时为 None。
        rescan_futile: 无跳过条目且窗口耗尽的截断页重扫徒劳标记，分页阶段派生。
        scan_result: 浏览扫描结果，skipped_count 与 stall_permanent 由其派生；
            未扫描或计数为 0 时 skipped_count 为 None。
        error: 结构化错误载荷，无错误时为 None。

    Returns:
        structuredContent 字典，无错误时排除 error 键。
    """
    # 计数为 0 与未扫描统一置 None，与无跳过不携带计数的输出契约一致。
    skipped_count = None if scan_result is None else (scan_result.skipped_count or None)
    payload: dict[str, Any] = {
        "tool": BROWSE_IMAGES_TOOL_NAME,
        "success": success,
        "status": status,
        "directory": state.directory,
        "resolved_directories": [item.as_posix() for item in state.resolved_directories],
        "workspace_roots": [root.as_posix() for root in state.workspace_roots],
        "images": images if images is not None else [],
        "count": len(images) if images is not None else 0,
        "total_count": total_count,
        "offset": state.offset,
        "has_more": has_more,
        "next_offset": next_offset,
        "stall_permanent": scan_result is not None and scan_result.stall_permanent,
        "rescan_futile": rescan_futile,
        "skipped_count": skipped_count,
        "recursive": state.recursive,
        "max_depth": state.max_depth,
        "limit": state.limit,
        "show_details": state.show_details,
        "format_filter": state.format_filter,
    }
    output = BrowseImagesStructuredOutput(**payload, error=error)
    if error is None:
        return output.model_dump(exclude={"error"})
    return output.model_dump()


async def _build_browse_error(
    *,
    state: _BrowseRequestState,
    message: str,
    error_type: str = BROWSE_FAILED_ERROR_TYPE,
) -> CallToolResult:
    """集中构造错误 CallToolResult，各错误分支仅 message 不同。

    统一 is_error=True 语义与请求状态回显；模型可自纠的参数错误经 error_type 传
    validation_error，与输入模型构造失败口径一致，其余分支保持 browse_failed；
    message 同时作为可见文本与结构化错误原因。

    Args:
        state: 单次浏览请求的状态快照，供回显字段取值。
        message: 面向用户的错误消息。
        error_type: structuredContent.error.type 取值，缺省 browse_failed。

    Returns:
        is_error=True 的工具结果。
    """
    structured = _build_browse_structured_result(
        state,
        status="failed",
        success=False,
        error=build_error_dict(error_type, message),
    )
    return await build_structured_tool_result(message, structured, is_error=True)


def _scan_and_filter_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    remaining: int,
    read_scope: list[Path],
    unreadable_dirs: list[Path],
) -> ScanResult:
    """扫描单个目录并按读权限过滤，返回扫描层的完整结果信号。

    同步执行，由调用方经 ``asyncio.to_thread`` 在线程内调用。图片的 resolve 结果由
    扫描缓存共享；越界复核不随缓存固化，经 include 谓词传入扫描层、每次按
    当前读权限重新执行，读范围签名随调用并入缓存键，谓词派生信号不跨读范围
    回放。包含判定按预计算分量执行：条目分量随扫描条目携带，读范围分量每次
    调用预计算一次。配额填充与扩窗由扫描层端到端承担，
    本函数不叠加补扫循环，增长与停滞语义单层收拢。越界剔除数由扫描层经
    excluded_count 回传（扩窗轮次间不重复计数，命中回放按考查前缀计），
    本函数按计数落单条聚合告警，防大目录翻页时越界项逐条刷屏；
    扫描中途异常时结果不可得，改落无计数告警。
    读范围为空（替代界整界剔除）时条目必然全剔，免注定停滞的扫描直接
    返回按永久态计的空停滞窗；全窗条目均解析在范围外的确定性停滞由扫描层
    首答升永久并随缓存条目持久化，重探窗口内的重试直接冻结窗回放。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        remaining: 返回条数的配额下限。
        read_scope: 已 resolve 的条目过滤界，为读权限或其与替代界的交集。
        unreadable_dirs: 不可读目录收集列表，就地更新，供空结果分支区分目录
            不可读与目录内无图片。

    Returns:
        ScanResult；images 仅含界内条目，未扫尽且未截断时不少于 remaining 条，
        停滞短窗除外；成因与终点信号原样透传，供分页判定。
    """
    if not read_scope:
        return ScanResult(
            images=[],
            complete=False,
            incomplete_cause=ScanIncompleteCause.STALLED,
        ).as_permanent()

    # 读范围分量随调用预计算一次，条目按分量原语直判，免逐条目重建范围分量。
    scope_parts = [normcase_parts(base) for base in read_scope]

    def _within_read_scope(entry: ScanEntry) -> bool:
        # 条目分量已随扫描条目预计算，包含判定仅做分量比较、不触发文件系统解析。
        return is_within_any_nc_parts(entry.resolved_nc_parts, scope_parts)

    try:
        # 扫描核心 _scan_resolved_directory 经本模块作用域注入，外部替换本模块同名属性即可生效。
        scan_result = cached_find_images_in_directory(
            resolved_dir=resolved_dir,
            recursive=recursive,
            max_depth=max_depth,
            format_filter=format_filter,
            scan_limit=remaining,
            scanner=_scan_resolved_directory,
            unreadable_dirs=unreadable_dirs,
            include=_within_read_scope,
            scope_signature=read_scope_signature(read_scope),
        )
    except BaseException:
        logger.warning("扫描中途异常，越界图片剔除计数不可得")
        raise
    if scan_result.excluded_count:
        logger.warning("检测到越界图片路径 {} 条，已忽略", scan_result.excluded_count)
    return scan_result


def _build_display_entries(
    *,
    image_entries: list[ScanEntry],
    show_details: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """组装展示文本与结构化图片条目，条目为可直接使用的绝对路径。

    文件系统相关计算集中在本函数同步执行，由调用方经 ``asyncio.to_thread`` 在线程内
    调用，避免网络挂载目录的 stat 阻塞事件循环。条目恒为 resolved 绝对路径并归一
    正斜杠，可直接填入参考图参数；文件名来自服务器自己的文件系统扫描结果，原样
    回显不做脱敏，保证模型按条目回流时路径可命中。

    Args:
        image_entries: 当前页的扫描条目列表，已经扫描层越界过滤与去重。
        show_details: 是否附带大小与修改时间详情。

    Returns:
        (展示文本行列表，结构化图片条目列表)，文本行不含「图片列表:」标题头。
    """
    lines: list[str] = []
    structured_images: list[dict[str, Any]] = []
    for idx, image_entry in enumerate(image_entries, 1):
        img_resolved = image_entry.resolved_path
        # as_posix 仅归一分隔符，POSIX 文件名中的字面反斜杠保持原样可回流。
        display_path = img_resolved.as_posix()
        # 文本通道压平控制字符，防止含控制字符文件名伪造清单行；口径与 errors 的
        # 净化共用 CONTROL_CHARS_PATTERN；结构化路径保持原样供回流。
        text_path = CONTROL_CHARS_PATTERN.sub(" ", display_path)
        detail_text, details = _format_file_info(text_path, image_entry, show_details)
        lines.append(f"{idx}. {detail_text}")
        structured: dict[str, Any] = {"index": idx, "path": display_path}
        structured.update(details)
        structured_images.append(structured)
    return lines, structured_images


def _normalize_format_filter(raw: list[str] | None) -> tuple[list[str] | None, bool]:
    """过滤出受支持的图片扩展名，返回 (过滤值, 是否无有效后缀)。

    仅保留受支持的扩展名；空列表与全部不受支持的输入均标记为无有效后缀，并保留原始
    输入供 structuredContent 回显。空列表不能透传给 ``_scan_resolved_directory``，其将
    空列表视为未限制而扫描全部。None 表示不限制、不标记。

    Args:
        raw: 用户原始提交的扩展名列表。

    Returns:
        (过滤值, 是否无有效后缀) 二元组，过滤值保留原始输入供回显。
    """
    if raw is None:
        return None, False
    if not raw:
        return raw, True
    supported_only = [ext for ext in raw if ext in SUPPORTED_IMAGE_EXTENSIONS]
    if supported_only:
        return supported_only, False
    return raw, True


def _narrow_scope_to_bounds(read_scope: list[Path], bounds_scope: list[Path]) -> list[Path]:
    """仅保留落在读权限内的替代界，未落入者整界剔除。"""
    # 读范围分量随调用预计算一次，替代界按分量原语直判，免逐替代界重建范围分量。
    read_scope_parts = [normcase_parts(base) for base in read_scope]
    return [
        bound
        for bound in bounds_scope
        if is_within_any_nc_parts(normcase_parts(bound), read_scope_parts)
    ]


async def build_browse_fallback_result(
    params: BrowseImagesInput,
    resolved_directories: list[Path],
    user_message: str,
) -> CallToolResult:
    """构建未预期异常的兜底错误 CallToolResult。

    回退根读取仅在无会话 Roots 时触发现算，与预解析同口径下沉线程执行；过滤值经
    同一过滤规则回显，保留用户原始输入。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        resolved_directories: 外层创建的共享列表，兜底分支经同一引用回显已解析目录。
        user_message: 已格式化的用户可见错误消息。

    Returns:
        is_error=True 的工具结果。
    """
    try:
        fallback_roots = await asyncio.to_thread(get_workspace_roots)
    except Exception as exc:
        # 兜底分支的回显字段降级原因可追溯。
        logger.warning("浏览兜底分支重读工作区根失败，按无工作区处理: {}", exc)
        fallback_roots = []
    fallback_filter, _ = _normalize_format_filter(params.format_filter)
    return await _build_browse_error(
        state=_BrowseRequestState.from_params(
            params,
            workspace_roots=fallback_roots,
            resolved_directories=resolved_directories,
            format_filter=fallback_filter,
        ),
        message=f"浏览图片失败：{user_message}；请确认目录路径有效且位于工作区内。",
    )


async def _resolve_browse_directories(
    directory: str,
    resolved_directory: Path | None = None,
) -> tuple[list[Path], list[Path], Path | None, str | None]:
    """目录解析阶段：求值读权限并解析请求目录，供越界判定与扫描取用。

    resolved_directory 为调用方经 resolve_against_base 同口径预解析的结果，提供时
    免重复解析，相对与读权限判定照常执行。绝对目录判读权限，相对目录仅限图片
    目录内。解析成功返回单个已 resolve 目录；路径无效与相对越界携带错误消息，
    绝对越界返回 None。
    """

    # 读权限求值与请求目录的 resolve/normalize 可能阻塞网络挂载目录，整体下沉
    # 线程；会话 Roots 时工作区为 ContextVar 直读，下沉无额外开销。后续以已
    # resolve 的目录直接比较；structuredContent 仍回显原始 workspace_roots。
    def _read_scope_and_resolve_dir() -> tuple[list[Path], list[Path], Path | None, str | None]:
        """求值工作区、图片目录与读权限并解析请求目录，返回四元组。

        三类位置经 get_read_context 单点求值共享，消除本函数内对图片目录与工作区
        的重复解析；请求目录解析经 resolve_against_base 与 Web 图库共用同一组合。
        """
        workspace_roots, images_root, read_scope = get_read_context()
        if resolved_directory is not None:
            resolved_dir = resolved_directory
            within_images_root = is_within_resolved(resolved_directory, images_root)
        else:
            try:
                # 相对路径以图片目录为基准；默认目录 "." 即图片目录本身。
                resolved_dir, within_images_root = resolve_against_base(directory, images_root)
            except ValueError as exc:
                # 异常消息内含用户输入路径，经净化后才进入错误通道。
                return (
                    workspace_roots,
                    read_scope,
                    None,
                    sanitize_error_text(f"目录路径无效: {exc}"),
                )
        if not os.path.isabs(directory) and not within_images_root:
            return (
                workspace_roots,
                read_scope,
                None,
                "相对路径仅限图片保存目录内，其他位置请使用绝对路径",
            )
        # 基集合只用一次，判定走 is_within_any，内部逐基现算恰宜。
        if not is_within_any(resolved_dir, read_scope):
            return workspace_roots, read_scope, None, None
        return workspace_roots, read_scope, resolved_dir, None

    return await asyncio.to_thread(_read_scope_and_resolve_dir)


async def _scan_browse_entries(
    *,
    ctx: Context[Any, Any] | None,
    state: _BrowseRequestState,
    resolved_dir: Path,
    read_scope: list[Path],
    format_filter_exhausted: bool,
) -> tuple[list[ScanEntry], list[Path], ScanResult | None]:
    """扫描阶段：扫描请求目录收集越界过滤后的条目，上报扫描进度并回传扫描信号。"""
    # scan_limit 多取一张用于判定 has_more；越界剔除经 include 谓词由扫描层端到端
    # 承担，见 _scan_and_filter_directory。format_filter_exhausted 时跳过扫描与
    # 进度上报，由空结果分支统一返回。
    scan_limit = state.offset + state.limit + 1
    image_entries: list[ScanEntry] = []
    unreadable_dirs: list[Path] = []
    scan_result: ScanResult | None = None
    if not format_filter_exhausted:
        await safe_report_progress(ctx, progress=PROGRESS_SCAN_START, message="开始扫描图片目录")
        scan_result = await asyncio.to_thread(
            _scan_and_filter_directory,
            resolved_dir=resolved_dir,
            recursive=state.recursive,
            max_depth=state.max_depth,
            format_filter=state.format_filter,
            remaining=scan_limit,
            read_scope=read_scope,
            unreadable_dirs=unreadable_dirs,
        )
        image_entries = scan_result.images
    return image_entries, unreadable_dirs, scan_result


class _BrowsePage(NamedTuple):
    """分页切片阶段的产出：当前页条目与派生的翻页信号。

    Attributes:
        entries: 当前页扫描条目。
        has_more: 是否仍有未返回条目，未扫尽未知时为 None。
        next_offset: 下一页或未扫尽页续扫的起始偏移，无更多图片、截断徒劳页或
            永久停滞时为 None。
        total_count: 匹配图片总数，未扫完全量时为 None。
        rescan_futile: 无跳过条目且窗口耗尽的截断页重扫徒劳标记。
    """

    entries: list[ScanEntry]
    has_more: bool | None
    next_offset: int | None
    total_count: int | None
    rescan_futile: bool


def _paginate_browse_images(
    *,
    image_entries: list[ScanEntry],
    offset: int,
    limit: int,
    scan_result: ScanResult | None,
) -> _BrowsePage:
    """分页切片阶段：切出当前页条目并派生 has_more、next_offset、total_count 与 rescan_futile。

    存在不完整成因（条目预算截断、扩窗停滞或条目剔除）时总数未知：total_count 置 None。
    截断或停滞使窗口未扫尽时，未翻满页也不以 has_more=False 声称无更多，
    改置 None 表示未知，next_offset 给出跳过本页已取得条目的续扫偏移，
    空停滞页等于原偏移原地重扫（负缓存到期重试可恢复）；窗口耗尽的截断页
    无跳过条目时截断预算静态且与 offset 无关，续扫必落回同一前沿，与永久停滞
    同撤续扫偏移并置 rescan_futile，供消费方门控注定徒劳的原地重扫；有跳过
    条目时扩窗与负缓存到期恢复仍可增长窗口，维持原地续扫导引；
    SKIPPED 且窗口已扫尽时按事实判定 has_more=False、next_offset 维持无更多取值，
    总数仍因少计不可信置 None，可见条目与目录实际条目的差额经 skipped_count 可推导。
    不完整性以 scan_result 单一来源传入，None 表示未发生扫描（format_filter_exhausted）。
    """
    # has_more 时未扫完全量、总数未知，total_count 置 None。
    page_end = offset + limit
    page_entries = image_entries[offset:page_end]
    has_more: bool | None = len(image_entries) > page_end
    next_offset: int | None = page_end if has_more else None
    rescan_futile = False
    if has_more:
        total_count = None
    elif scan_result is None or scan_result.incomplete_cause is None:
        total_count = len(image_entries)
    else:
        total_count = None
        if not (
            scan_result.incomplete_cause is ScanIncompleteCause.SKIPPED and scan_result.complete
        ):
            has_more = None
            # 截断预算与 offset 无关：无跳过条目的截断页重扫必复现，有跳过条目可增长窗口。
            rescan_futile = scan_result.rescan_futile
            if scan_result.stall_permanent or rescan_futile:
                next_offset = None
            else:
                next_offset = offset + len(page_entries)
    return _BrowsePage(
        entries=page_entries,
        has_more=has_more,
        next_offset=next_offset,
        total_count=total_count,
        rescan_futile=rescan_futile,
    )


def _continue_tail_texts(
    page: _BrowsePage,
    scan_result: ScanResult | None,
) -> tuple[str | None, str | None]:
    """派生文本通道尾部的续扫导引与未完成标记，成功与空结果两条装配通道共用。

    has_more 页改走翻页引导不取导引；空页续扫偏移等于原偏移，文本同样导引
    原地重扫。

    Returns:
        (续扫导引, 未完成标记) 二元组，缺省分量为 None，标记已含括号包裹。
    """
    hint: str | None = None
    if not page.has_more and page.next_offset is not None:
        hint = f"{_CONTINUE_SCAN_HINT_TEXT}{page.next_offset}"
    marker = _incomplete_marker_text(scan_result)
    return hint, None if marker is None else f"（{marker}）"


async def _build_empty_browse_result(
    *,
    ctx: Context[Any, Any] | None,
    state: _BrowseRequestState,
    page: _BrowsePage,
    format_filter_exhausted: bool,
    unreadable_dirs: list[Path],
    scan_result: ScanResult | None,
) -> CallToolResult:
    """空页装配阶段：按错误可归因性分流为参数错误结果或空结果。"""
    # 空页按错误可归因性分流：format_filter_exhausted 与 offset 越界是模型可自纠的
    # 参数错误，返回 is_error=True 与结构化错误标记；目录不可读与无图片非模型可修复，
    # 维持空结果语义，文案区分「目录不可读」与「无图片」；存在不完整成因时
    # 空结果不声称完备，追加可见标记。
    if format_filter_exhausted:
        supported_list = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
        if state.format_filter:
            # 用户 filter 字符串经净化后拼入消息；支持列表为静态服务端数据，
            # 不参与净化。
            user_formats = sanitize_error_text(", ".join(state.format_filter))
            message = f"指定的图片格式 {user_formats} 均不在支持列表内，支持: {supported_list}。"
        else:
            # 空列表无格式可回显，改用不含空位的文案，避免残缺语义。
            message = f"未指定任何受支持的图片格式，支持: {supported_list}。"
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return await _build_browse_error(
            state=state, message=message, error_type="validation_error"
        )
    if page.total_count:
        # 消息携带总数与有效区间，模型修正 offset 后即可重试。
        message = (
            f"offset={state.offset} 超出范围，目录共有 {page.total_count} 张图片，"
            f"请使用 0 <= offset < {page.total_count}。"
        )
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return await _build_browse_error(
            state=state, message=message, error_type="validation_error"
        )
    if unreadable_dirs:
        unique_unreadable = list(dict.fromkeys(unreadable_dirs))
        logger.info("不可读目录明细: {}", [str(item) for item in unique_unreadable])
        listed = unique_unreadable[:_MAX_UNREADABLE_DIR_LISTING]
        parts = [", ".join(sanitize_data_text(item.as_posix()) for item in listed)]
        hidden = len(unique_unreadable) - len(listed)
        if hidden:
            parts.append(f"另有 {hidden} 个目录")
        dirs_text = "，".join(parts)
        message = f"目录不可读或无图片文件：{dirs_text}"
    else:
        message = "未找到图片文件，请确认目录或过滤条件。"
    hint_text, marker_text = _continue_tail_texts(page, scan_result)
    if hint_text is not None:
        message = f"{message}；{hint_text}"
    if marker_text is not None:
        message = f"{message}{marker_text}"
    structured = _build_browse_structured_result(
        state,
        status="empty",
        total_count=page.total_count,
        has_more=page.has_more,
        next_offset=page.next_offset,
        rescan_futile=page.rescan_futile,
        scan_result=scan_result,
    )
    result = await build_structured_tool_result(message, structured, is_error=False)
    await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")
    return result


async def _build_browse_success_result(
    *,
    ctx: Context[Any, Any] | None,
    state: _BrowseRequestState,
    page: _BrowsePage,
    scan_result: ScanResult | None,
) -> CallToolResult:
    """成功装配阶段：生成展示条目与翻页引导并组装 completed 结果。"""
    display_lines, structured_images = await asyncio.to_thread(
        _build_display_entries,
        image_entries=page.entries,
        show_details=state.show_details,
    )
    lines = ["图片列表:"] + display_lines
    if page.has_more:
        # has_more 时 total_count 恒为 None，翻页引导仅给出当前页区间。
        page_last = state.offset + len(page.entries)
        range_text = f"第 {state.offset + 1}-{page_last} 张"
        lines.append(f"{range_text}，仍有更多，继续翻页请传 offset={page.next_offset}")
    hint_text, marker_text = _continue_tail_texts(page, scan_result)
    if hint_text is not None:
        lines.append(hint_text)
    if marker_text is not None:
        lines.append(marker_text)

    structured = _build_browse_structured_result(
        state,
        status="completed",
        images=structured_images,
        total_count=page.total_count,
        has_more=page.has_more,
        next_offset=page.next_offset,
        rescan_futile=page.rescan_futile,
        scan_result=scan_result,
    )
    result = await build_structured_tool_result("\n".join(lines), structured, is_error=False)
    await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")
    return result


async def execute_browse_request(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None,
    *,
    resolved_directories: list[Path],
    bounds_scope: list[Path] | None = None,
    resolved_directory: Path | None = None,
) -> CallToolResult:
    """执行图片浏览主逻辑：求值读权限、解析目录、扫描分页并装配工具结果。

    目录解析以图片目录为基准，判定面向读权限（工作区 ∪ 图片目录）；扫描结果经扫描
    缓存加速翻页，切片多取一张以判定 has_more。未预期异常向上抛出，由薄壳入口外层
    ``handle_browse_images`` 兜底降级。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        ctx: MCP 上下文，用于进度上报，可为 None。
        resolved_directories: 外层创建的共享列表，解析结果逐步填充，供成功与兜底
            分支读取。
        bounds_scope: 条目过滤的替代界，与读权限求交后生效，空列表拒绝；None 时按
            读权限（工作区 ∪ 图片目录）过滤；Web 图库传入图片目录使越界条目在
            扫描源头剔除。
        resolved_directory: 调用方经 resolve_against_base 同口径预解析的请求目录，
            提供时免重复解析；None 时按 directory 现解析。

    Returns:
        浏览工具结果，目录无效、越界与模型可自纠的参数错误为 is_error=True，目录
        不可读与无图片维持空结果语义。

    Raises:
        ValueError: bounds_scope 为空列表时。
    """
    if bounds_scope is not None and not bounds_scope:
        raise ValueError("bounds_scope 不能为空列表")
    effective_format_filter, format_filter_exhausted = _normalize_format_filter(
        params.format_filter
    )
    directory = params.effective_directory

    workspace_roots, read_scope, resolved_dir, dir_error = await _resolve_browse_directories(
        directory, resolved_directory
    )

    state = _BrowseRequestState.from_params(
        params,
        workspace_roots=workspace_roots,
        resolved_directories=resolved_directories,
        format_filter=effective_format_filter,
    )

    if dir_error is not None:
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        # 目录形态非法（UNC、空字节等）为调用方可自纠的参数错误。
        return await _build_browse_error(
            state=state, message=dir_error, error_type="validation_error"
        )
    if resolved_dir is None:
        message = read_scope_denial_message("目录")
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return await _build_browse_error(state=state, message=message)
    resolved_directories.append(resolved_dir)

    logger.info(
        "浏览图片: dir={}, recursive={}, max_depth={}, limit={}",
        resolved_dir,
        state.recursive,
        state.max_depth,
        state.limit,
    )

    scan_scope = (
        _narrow_scope_to_bounds(read_scope, bounds_scope)
        if bounds_scope is not None
        else read_scope
    )
    image_entries, unreadable_dirs, scan_result = await _scan_browse_entries(
        ctx=ctx,
        state=state,
        resolved_dir=resolved_dir,
        read_scope=scan_scope,
        format_filter_exhausted=format_filter_exhausted,
    )

    page = _paginate_browse_images(
        image_entries=image_entries,
        offset=state.offset,
        limit=state.limit,
        scan_result=scan_result,
    )
    if not page.entries:
        return await _build_empty_browse_result(
            ctx=ctx,
            state=state,
            page=page,
            format_filter_exhausted=format_filter_exhausted,
            unreadable_dirs=unreadable_dirs,
            scan_result=scan_result,
        )

    return await _build_browse_success_result(
        ctx=ctx,
        state=state,
        page=page,
        scan_result=scan_result,
    )


async def handle_browse_images(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None = None,
    *,
    bounds_scope: list[Path] | None = None,
    resolved_directory: Path | None = None,
) -> CallToolResult:
    """处理图片浏览请求，扫描读权限（工作区 ∪ 图片目录）内指定目录的图片并分页返回。

    无会话 Roots 声明时工作根目录经声明链回退；未预期异常降级为结构化错误返回，
    不向调用方抛出。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        ctx: MCP 上下文，用于进度上报，可为 None。
        bounds_scope: 条目过滤的替代界，与读权限求交后生效，None 时按读权限过滤；
            透传给 execute_browse_request。
        resolved_directory: 调用方经 resolve_against_base 同口径预解析的请求目录，
            提供时免重复解析；透传给 execute_browse_request。

    Returns:
        浏览工具结果，失败时 isError 为 True。
    """
    # 已解析目录列表在外层创建、浏览编排填充：异常兜底分支经同一引用回显已解析目录。
    resolved_directories: list[Path] = []
    try:
        return await execute_browse_request(
            params,
            ctx,
            resolved_directories=resolved_directories,
            bounds_scope=bounds_scope,
            resolved_directory=resolved_directory,
        )
    except Exception as exc:
        log_tiered_failure(logger, exc, "浏览图片处理失败")
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return await build_browse_fallback_result(
            params, resolved_directories, format_error_for_user(exc)
        )
