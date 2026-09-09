"""图片浏览工具的核心执行流水线。

读权限求值（工作区 ∪ 图片目录）、请求目录解析、越界过滤扫描、分页配额与
structuredContent 装配；不经 ``execute_generation_handler`` 生成流水线，由 impl
处理器薄壳委托调用，未预期异常不在本模块捕获，统一由外层兜底降级。
"""

from __future__ import annotations

import asyncio
import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

from ...utils.core.errors import (
    CONTROL_CHARS_PATTERN,
    sanitize_data_text,
    sanitize_error_text,
)
from ...utils.core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ...utils.core.logs import get_logger
from ...utils.io.io_path import (
    find_images_in_directory,
    get_read_context,
    get_workspace_roots,
    is_within_resolved,
    normalize_path,
    read_scope_denial_message,
)
from ...utils.io.io_scan import cached_find_images_in_directory
from ._helpers import (
    PROGRESS_COMPLETE,
    PROGRESS_SCAN_START,
    safe_report_progress,
)
from .outputs import BrowseImagesStructuredOutput, build_error_dict
from .schemas import BrowseImagesInput

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = get_logger()

# 空结果文案中不可读目录的列举上限，超出部分按计数提示，全部明细保留在日志。
_MAX_UNREADABLE_DIR_LISTING = 5

# 扫描因条目预算截断时文本通道的可见标记，提示模型返回结果可能不完整。
_SCAN_TRUNCATION_MARKER = "目录条目过多，结果可能不完整"


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
    display_path: str, stat_path: Path, show_details: bool
) -> tuple[str, dict[str, Any]]:
    """格式化单个文件的展示文本与结构化详情字段。

    show_details 为真时附带大小与修改时间，文本格式为「路径 | 大小 | 修改时间」，结构化
    详情含 size_mb 与 modified 两键；stat 或时间戳解析失败时降级为「文件信息不可用」。
    show_details 为假时仅返回路径文本与空详情字典。

    Args:
        display_path: 展示给用户的路径字符串。
        stat_path: 读取文件属性的实际路径对象。
        show_details: 是否附带大小与修改时间详情。

    Returns:
        (展示文本, 结构化详情字典) 二元组，无详情时字典为空。
    """
    if not show_details:
        return display_path, {}
    try:
        stat_result = stat_path.stat()
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
        next_offset: 下一页起始偏移，无更多图片时为 None。
        error: 结构化错误载荷，无错误时为 None。

    Returns:
        structuredContent 字典，无错误时排除 error 键。
    """
    payload: dict[str, Any] = {
        "tool": "browse_images",
        "success": success,
        "status": status,
        "directory": state.directory,
        "resolved_directories": [
            str(item).replace("\\", "/") for item in state.resolved_directories
        ],
        "workspace_roots": [str(root).replace("\\", "/") for root in state.workspace_roots],
        "images": images if images is not None else [],
        "count": len(images) if images is not None else 0,
        "total_count": total_count,
        "offset": state.offset,
        "has_more": has_more,
        "next_offset": next_offset,
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


def _build_browse_error(
    *,
    state: _BrowseRequestState,
    message: str,
    error_type: str = "browse_failed",
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
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structured_content=_build_browse_structured_result(
            state,
            status="failed",
            success=False,
            error=build_error_dict(error_type, message),
        ),
        is_error=True,
    )


def _scan_and_filter_directory(
    *,
    resolved_dir: Path,
    recursive: bool,
    max_depth: int,
    format_filter: list[str] | None,
    remaining: int,
    read_scope: list[Path],
    seen_images: set[Path],
    unreadable_dirs: list[Path],
    truncated_dirs: list[Path],
) -> list[tuple[Path, Path]]:
    """扫描单个目录并做越界判定与去重，返回新增的 (原始路径, resolved 路径) 列表。

    同步执行，由调用方经 ``asyncio.to_thread`` 在线程内调用。图片的 resolve 结果由扫描
    缓存共享；越界复核与去重不随缓存固化，每次按当前读权限重新执行。剔除项不占分页
    配额：扫描命中上限且配额未填满时，按剔除计数扩大 scan_limit 补扫，直至填满配额、
    扫到目录末尾或无剔除项。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归扫描的最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        remaining: 本目录新增条数的配额上限。
        read_scope: 已 resolve 的读权限目录列表（工作区 ∪ 图片目录），越界判定基准。
        seen_images: 已见原始路径集合，就地更新，兜底扫描缓存前缀扩展轮次间的
            竞态错位重复。
        unreadable_dirs: 不可读目录收集列表，就地更新，供空结果分支区分目录
            不可读与目录内无图片。
        truncated_dirs: 截断目录收集列表，就地更新，供装配分支标记结果不完整。

    Returns:
        新增 (原始路径, resolved 路径) 元组列表，长度不超过 remaining。
    """
    new_entries: list[tuple[Path, Path]] = []
    scan_limit = remaining
    # 续扫游标：已消费的条目数，补扫轮从该位置继续。
    consumed = 0
    while True:
        # 底层扫描经本模块作用域的 find_images_in_directory 注入，外部替换本模块同名属性即可生效。
        matched_image_pairs = cached_find_images_in_directory(
            resolved_dir=resolved_dir,
            recursive=recursive,
            max_depth=max_depth,
            format_filter=format_filter,
            scan_limit=scan_limit,
            scanner=find_images_in_directory,
            unreadable_dirs=unreadable_dirs,
            truncated_dirs=truncated_dirs,
        )
        # 返回量达到 scan_limit 说明可能仍有后续条目，否则已扫到末尾。
        scan_hit_limit = len(matched_image_pairs) >= scan_limit
        dropped = 0
        out_of_scope: list[Path] = []
        while consumed < len(matched_image_pairs):
            image_path, image_resolved = matched_image_pairs[consumed]
            consumed += 1
            # resolve 结果来自扫描缓存；权限目录已 resolve，直接比较。
            if not any(is_within_resolved(image_resolved, scope) for scope in read_scope):
                out_of_scope.append(image_path)
                dropped += 1
                continue
            if image_path in seen_images:
                dropped += 1
                continue
            seen_images.add(image_path)
            new_entries.append((image_path, image_resolved))
            if len(new_entries) >= remaining:
                break
        if out_of_scope:
            # 聚合单条告警，防大目录翻页时越界项逐条刷屏
            logger.warning("检测到越界图片路径 {} 条，已忽略", len(out_of_scope))
        if not scan_hit_limit or len(new_entries) >= remaining or dropped == 0:
            return new_entries
        # 按剔除计数扩大 scan_limit 补扫，使剔除项不占本页配额；同目录扫描返回稳定
        # 前缀，consumed 游标不重复消费，竞态错位由 seen_images 去重兜底。
        scan_limit = scan_limit + dropped


def _build_display_entries(
    *,
    images: list[Path],
    image_resolved_map: dict[Path, Path],
    show_details: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """组装展示文本与结构化图片条目，条目为可直接使用的绝对路径。

    文件系统相关计算集中在本函数同步执行，由调用方经 ``asyncio.to_thread`` 在线程内
    调用，避免网络挂载目录的 stat 阻塞事件循环。条目恒为 resolved 绝对路径并归一
    正斜杠，可直接填入参考图参数；文件名来自服务器自己的文件系统扫描结果，原样
    回显不做脱敏，保证模型按条目回流时路径可命中。

    Args:
        images: 当前页的图片原始路径列表，已经扫描层越界过滤与去重。
        image_resolved_map: 原始路径到 resolved 路径的映射，由扫描阶段填充。
        show_details: 是否附带大小与修改时间详情。

    Returns:
        (展示文本行列表，结构化图片条目列表)，文本行不含「图片列表:」标题头。
    """
    lines: list[str] = []
    structured_images: list[dict[str, Any]] = []
    for idx, img in enumerate(images, 1):
        img_resolved = image_resolved_map[img]
        # 归一正斜杠，跨平台口径一致。
        display_path = str(img_resolved).replace("\\", "/")
        # 文本通道压平控制字符，防止含控制字符文件名伪造清单行；口径与 errors 的
        # 净化共用 CONTROL_CHARS_PATTERN；结构化路径保持原样供回流。
        text_path = CONTROL_CHARS_PATTERN.sub(" ", display_path)
        detail_text, details = _format_file_info(text_path, img_resolved, show_details)
        lines.append(f"{idx}. {detail_text}")
        entry: dict[str, Any] = {"index": idx, "path": display_path}
        entry.update(details)
        structured_images.append(entry)
    return lines, structured_images


def _normalize_format_filter(raw: list[str] | None) -> tuple[list[str] | None, bool]:
    """过滤出受支持的图片扩展名，返回 (过滤值, 是否无有效后缀)。

    仅保留受支持的扩展名；空列表与全部不受支持的输入均标记为无有效后缀，并保留原始
    输入供 structuredContent 回显。空列表不能透传给 ``find_images_in_directory``，其将
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
        # 兜底分支的回显字段降级原因可追溯
        logger.warning("浏览兜底分支重读工作区根失败，按无工作区处理: {}", exc)
        fallback_roots = []
    fallback_filter, _ = _normalize_format_filter(params.format_filter)
    return _build_browse_error(
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
) -> tuple[list[Path], list[Path], Path | None, str | None]:
    """目录解析阶段：求值读权限并解析请求目录，供越界判定与扫描取用。

    绝对目录判读权限，相对目录仅限图片目录内。解析成功返回单个已 resolve 目录；
    路径无效与相对越界携带错误消息，绝对越界返回 None。
    """

    # 读权限求值与请求目录的 resolve/normalize 可能阻塞网络挂载目录，整体下沉
    # 线程；会话 Roots 时工作区为 ContextVar 直读，下沉无额外开销。后续以已
    # resolve 的目录直接比较；structuredContent 仍回显原始 workspace_roots。
    def _read_scope_and_resolve_dir() -> tuple[list[Path], list[Path], Path | None, str | None]:
        """求值工作区、图片目录与读权限并解析请求目录，返回四元组。

        三类位置经 get_read_context 单点求值共享，消除本函数内对图片目录与工作区
        的重复解析。
        """
        workspace_roots, images_root, read_scope = get_read_context()
        try:
            # 相对路径以图片目录为基准；默认目录 "." 即图片目录本身。
            resolved_dir = normalize_path(directory, str(images_root))
        except ValueError as exc:
            # 异常消息内含用户输入路径，经净化后才进入错误通道。
            return workspace_roots, read_scope, None, sanitize_error_text(f"目录路径无效: {exc}")
        if not os.path.isabs(directory) and not is_within_resolved(resolved_dir, images_root):
            return (
                workspace_roots,
                read_scope,
                None,
                "相对路径仅限图片保存目录内，其他位置请使用绝对路径",
            )
        if not any(is_within_resolved(resolved_dir, scope) for scope in read_scope):
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
) -> tuple[list[Path], dict[Path, Path], list[Path], list[Path]]:
    """扫描阶段：扫描请求目录并合并越界过滤与去重后的图片条目，上报扫描进度。"""
    # scan_limit 多取一张用于判定 has_more；越界与重复项的剔除及补扫见
    # _scan_and_filter_directory。format_filter_exhausted 时跳过扫描与进度上报，
    # 由空结果分支统一返回。
    scan_limit = state.offset + state.limit + 1
    all_images: list[Path] = []
    image_resolved_map: dict[Path, Path] = {}
    unreadable_dirs: list[Path] = []
    truncated_dirs: list[Path] = []
    if not format_filter_exhausted:
        await safe_report_progress(ctx, progress=PROGRESS_SCAN_START, message="开始扫描图片目录")
        # seen_images 兜底单目录内缓存前缀扩展的竞态错位重复。
        seen_images: set[Path] = set()
        new_entries = await asyncio.to_thread(
            _scan_and_filter_directory,
            resolved_dir=resolved_dir,
            recursive=state.recursive,
            max_depth=state.max_depth,
            format_filter=state.format_filter,
            remaining=scan_limit,
            read_scope=read_scope,
            seen_images=seen_images,
            unreadable_dirs=unreadable_dirs,
            truncated_dirs=truncated_dirs,
        )
        for image_path, image_resolved in new_entries:
            all_images.append(image_path)
            image_resolved_map[image_path] = image_resolved
    return all_images, image_resolved_map, unreadable_dirs, truncated_dirs


def _paginate_browse_images(
    *,
    all_images: list[Path],
    offset: int,
    limit: int,
    truncated: bool,
) -> tuple[list[Path], bool | None, int | None, int | None]:
    """分页切片阶段：切出当前页图片并派生 has_more、next_offset 与 total_count。

    截断时总数未知：total_count 置 None，未翻满页也不以 has_more=False 声称无
    更多，改置 None 表示未知。
    """
    # has_more 时未扫完全量、总数未知，total_count 置 None。
    page_end = offset + limit
    images = all_images[offset:page_end]
    has_more: bool | None = len(all_images) > page_end
    next_offset: int | None = page_end if has_more else None
    if has_more:
        total_count = None
    elif truncated:
        total_count = None
        has_more = None
        next_offset = None
    else:
        total_count = len(all_images)
    return images, has_more, next_offset, total_count


async def _build_empty_browse_result(
    *,
    ctx: Context[Any, Any] | None,
    state: _BrowseRequestState,
    format_filter_exhausted: bool,
    total_count: int | None,
    has_more: bool | None,
    next_offset: int | None,
    unreadable_dirs: list[Path],
    truncated: bool,
) -> CallToolResult:
    """空页装配阶段：按错误可归因性分流为参数错误结果或空结果。"""
    # 空页按错误可归因性分流：format_filter_exhausted 与 offset 越界是模型可自纠的
    # 参数错误，返回 is_error=True 与结构化错误标记；目录不可读与无图片非模型可修复，
    # 维持空结果语义，文案区分「目录不可读」与「无图片」；截断时空结果不声称完备，
    # 追加可见标记。
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
        return _build_browse_error(state=state, message=message, error_type="validation_error")
    if total_count:
        # 消息携带总数与有效区间，模型修正 offset 后即可重试。
        message = (
            f"offset={state.offset} 超出范围，目录共有 {total_count} 张图片，"
            f"请使用 0 <= offset < {total_count}。"
        )
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return _build_browse_error(state=state, message=message, error_type="validation_error")
    if unreadable_dirs:
        unique_unreadable = list(dict.fromkeys(unreadable_dirs))
        logger.info("不可读目录明细: {}", [str(item) for item in unique_unreadable])
        listed = unique_unreadable[:_MAX_UNREADABLE_DIR_LISTING]
        parts = [", ".join(sanitize_data_text(str(item).replace("\\", "/")) for item in listed)]
        hidden = len(unique_unreadable) - len(listed)
        if hidden:
            parts.append(f"另有 {hidden} 个目录")
        dirs_text = "，".join(parts)
        message = f"目录不可读或无图片文件：{dirs_text}"
    else:
        message = "未找到图片文件，请确认目录或过滤条件。"
    if truncated:
        message = f"{message}（{_SCAN_TRUNCATION_MARKER}）"
    result = CallToolResult(
        content=[TextContent(type="text", text=message)],
        structured_content=_build_browse_structured_result(
            state,
            status="empty",
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
        ),
        is_error=False,
    )
    await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")
    return result


async def _build_browse_success_result(
    *,
    ctx: Context[Any, Any] | None,
    state: _BrowseRequestState,
    images: list[Path],
    image_resolved_map: dict[Path, Path],
    has_more: bool | None,
    next_offset: int | None,
    total_count: int | None,
    truncated: bool,
) -> CallToolResult:
    """成功装配阶段：生成展示条目与翻页引导并组装 completed 结果。"""
    display_lines, structured_images = await asyncio.to_thread(
        _build_display_entries,
        images=images,
        image_resolved_map=image_resolved_map,
        show_details=state.show_details,
    )
    lines = ["图片列表:"] + display_lines
    if has_more:
        # has_more 时 total_count 恒为 None，翻页引导仅给出当前页区间。
        page_last = state.offset + len(images)
        range_text = f"第 {state.offset + 1}-{page_last} 张"
        lines.append(f"{range_text}，仍有更多，继续翻页请传 offset={next_offset}")
    if truncated:
        lines.append(f"（{_SCAN_TRUNCATION_MARKER}）")

    result = CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content=_build_browse_structured_result(
            state,
            status="completed",
            images=structured_images,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
        ),
        is_error=False,
    )
    await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")
    return result


async def execute_browse_request(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None,
    *,
    resolved_directories: list[Path],
) -> CallToolResult:
    """执行图片浏览主逻辑：求值读权限、解析目录、扫描分页并装配工具结果。

    目录解析以图片目录为基准，判定面向读权限（工作区 ∪ 图片目录）；扫描结果经扫描
    缓存加速翻页，切片多取一张以判定 has_more。未预期异常向上抛出，由 impl 外层
    ``handle_browse_images`` 兜底降级。

    Args:
        params: 经 pydantic 校验的工具输入模型。
        ctx: MCP 上下文，用于进度上报，可为 None。
        resolved_directories: 外层创建的共享列表，解析结果逐步填充，供成功与兜底
            分支读取。

    Returns:
        浏览工具结果，目录无效、越界与模型可自纠的参数错误为 is_error=True，目录
        不可读与无图片维持空结果语义。
    """
    raw_format_filter, format_filter_exhausted = _normalize_format_filter(params.format_filter)
    directory = params.effective_directory

    workspace_roots, read_scope, resolved_dir, dir_error = await _resolve_browse_directories(
        directory
    )

    state = _BrowseRequestState.from_params(
        params,
        workspace_roots=workspace_roots,
        resolved_directories=resolved_directories,
        format_filter=raw_format_filter,
    )

    if dir_error is not None:
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        # 目录形态非法（UNC、空字节等）为调用方可自纠的参数错误。
        return _build_browse_error(state=state, message=dir_error, error_type="validation_error")
    if resolved_dir is None:
        message = read_scope_denial_message("目录")
        await safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        return _build_browse_error(state=state, message=message)
    resolved_directories.append(resolved_dir)

    logger.info(
        "浏览图片: dir={}, recursive={}, max_depth={}, limit={}",
        resolved_dir,
        state.recursive,
        state.max_depth,
        state.limit,
    )

    all_images, image_resolved_map, unreadable_dirs, truncated_dirs = await _scan_browse_entries(
        ctx=ctx,
        state=state,
        resolved_dir=resolved_dir,
        read_scope=read_scope,
        format_filter_exhausted=format_filter_exhausted,
    )

    images, has_more, next_offset, total_count = _paginate_browse_images(
        all_images=all_images,
        offset=state.offset,
        limit=state.limit,
        truncated=bool(truncated_dirs),
    )

    if not images:
        return await _build_empty_browse_result(
            ctx=ctx,
            state=state,
            format_filter_exhausted=format_filter_exhausted,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
            unreadable_dirs=unreadable_dirs,
            truncated=bool(truncated_dirs),
        )

    return await _build_browse_success_result(
        ctx=ctx,
        state=state,
        images=images,
        image_resolved_map=image_resolved_map,
        has_more=has_more,
        next_offset=next_offset,
        total_count=total_count,
        truncated=bool(truncated_dirs),
    )
