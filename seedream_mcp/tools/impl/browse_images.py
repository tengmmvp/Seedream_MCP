"""图片浏览工具的 impl 处理器。

直接实现工作区图片扫描与分页，不经 ``execute_generation_handler`` 生成流水线；字段规则
与校验由 ``BrowseImagesInput`` 单一定义。
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolResult, TextContent

from ..core._helpers import (
    PROGRESS_COMPLETE,
    PROGRESS_SCAN_SPAN,
    PROGRESS_SCAN_START,
    _safe_report_progress,
)
from ..core.outputs import BrowseImagesStructuredOutput, build_error_dict
from ..core.schemas import BrowseImagesInput
from ...utils.core.errors import format_error_for_user, sanitize_data_text, sanitize_error_text
from ...utils.core.formats import SUPPORTED_IMAGE_EXTENSIONS
from ...utils.core.logs import get_logger
from ...utils.io.io_scan import cached_find_images_in_directory
from ...utils.io.io_path import (
    find_images_in_directory,
    get_relative_path,
    get_workspace_roots,
    is_boundary_from_session_roots,
    is_within_resolved,
    normalize_path,
    resolve_workspace_roots,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = get_logger(__name__)

# 回退边界占位回显：边界来自 env/CWD 回退而非会话 Roots 声明时，不向调用方回显
# 服务器本地路径，字段与消息统一以本占位符替代。
_FALLBACK_BOUNDARY_PLACEHOLDER = "<工作区根（服务器配置）>"


@dataclass(frozen=True)
class _BrowseRequestState:
    """单次浏览请求的状态快照，供成功、空结果与错误分支共享取值。

    Attributes:
        workspace_roots: 客户端授权的工作区根列表，保留原始形态供回显。
        directory: 请求目录字符串，未提供时归一为 "."。
        resolved_directories: 外层创建的共享列表，解析结果逐步填充，异常兜底分支
            经同一引用读取。
        format_filter: 经支持列表过滤后的扩展名白名单，None 表示不限制。
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
        format_filter: list[str] | None = None,
    ) -> _BrowseRequestState:
        """从类型化入参模型与既有状态构建请求快照。"""
        return cls(
            workspace_roots=workspace_roots,
            directory=params.directory if params.directory is not None else ".",
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
        display_path: 展示给用户的路径字符串，已完成净化。
        stat_path: 读取文件属性的实际路径对象。
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
    *,
    status: str,
    workspace_roots: list[Path],
    directory: str,
    resolved_directories: list[Path],
    recursive: bool,
    max_depth: int,
    limit: int,
    offset: int,
    show_details: bool,
    format_filter: list[str] | None,
    success: bool = True,
    images: list[dict[str, Any]] | None = None,
    total_count: int | None = None,
    has_more: bool | None = None,
    next_offset: int | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """集中构建 browse_images 的 structuredContent，成功、空结果与失败三分支共用。

    经 ``BrowseImagesStructuredOutput`` 构造后 model_dump，使输出与声明的 outputSchema
    绑定，字段漂移在构造时暴露；无错误时不输出 error 键。边界来自 env/CWD 回退而非
    会话 Roots 声明时，workspace_roots 与 resolved_directories 以占位符替代，不回显
    服务器本地目录。
    """
    if is_boundary_from_session_roots():
        workspace_root_values = [str(root) for root in workspace_roots]
        resolved_directory_values = [str(item) for item in resolved_directories]
    else:
        workspace_root_values = [_FALLBACK_BOUNDARY_PLACEHOLDER] if workspace_roots else []
        resolved_directory_values = [_FALLBACK_BOUNDARY_PLACEHOLDER] if resolved_directories else []
    payload: dict[str, Any] = {
        "tool": "browse_images",
        "success": success,
        "status": status,
        "directory": directory,
        "resolved_directories": resolved_directory_values,
        "workspace_roots": workspace_root_values,
        "images": images if images is not None else [],
        "count": len(images) if images is not None else 0,
        "total_count": total_count,
        "offset": offset,
        "has_more": has_more,
        "next_offset": next_offset,
        "recursive": recursive,
        "max_depth": max_depth,
        "limit": limit,
        "show_details": show_details,
        "format_filter": format_filter,
    }
    output = BrowseImagesStructuredOutput(**payload, error=error)
    if error is None:
        return output.model_dump(exclude={"error"})
    return output.model_dump()


def _build_browse_error(
    *,
    state: _BrowseRequestState,
    message: str,
    status: str = "failed",
) -> CallToolResult:
    """集中构造错误 CallToolResult，各错误分支仅 message 不同。

    统一 is_error=True 语义与请求状态回显；structuredContent.error.type 恒为
    browse_failed，message 同时作为可见文本与结构化错误原因。
    """
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structured_content=_build_browse_structured_result(
            status=status,
            workspace_roots=state.workspace_roots,
            directory=state.directory,
            resolved_directories=state.resolved_directories,
            recursive=state.recursive,
            max_depth=state.max_depth,
            limit=state.limit,
            offset=state.offset,
            show_details=state.show_details,
            format_filter=state.format_filter,
            success=False,
            error=build_error_dict("browse_failed", message),
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
    resolved_roots: list[Path],
    seen_images: set[Path],
    unreadable_dirs: list[Path],
) -> list[tuple[Path, Path]]:
    """扫描单个目录并做越界判定与去重，返回新增的 (原始路径, resolved 路径) 列表。

    同步执行，由调用方经 ``asyncio.to_thread`` 在线程内调用。图片的 resolve 结果由扫描
    缓存共享；越界复核与去重不随缓存固化，每次按当前工作区根重新执行。剔除项不占分页
    配额：扫描命中上限且配额未填满时，按剔除计数扩大 scan_limit 补扫，直至填满配额、
    扫到目录末尾或无剔除项。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        remaining: 本目录新增条数的配额上限。
        seen_images: 跨目录共享的已见原始路径集合，就地更新。
        unreadable_dirs: 跨目录共享的不可读目录收集列表，就地更新，供空结果分支区分
            目录不可读与目录内无图片。

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
        )
        # 返回量达到 scan_limit 说明可能仍有后续条目，否则已扫到末尾。
        scan_hit_limit = len(matched_image_pairs) >= scan_limit
        dropped = 0
        while consumed < len(matched_image_pairs):
            image_path, image_resolved = matched_image_pairs[consumed]
            consumed += 1
            # resolve 结果来自扫描缓存；根已 resolve，直接比较。
            if not any(is_within_resolved(image_resolved, root) for root in resolved_roots):
                logger.warning("检测到越界图片路径，已忽略: {}", image_path)
                dropped += 1
                continue
            if image_path in seen_images:
                dropped += 1
                continue
            seen_images.add(image_path)
            new_entries.append((image_path, image_resolved))
            if len(new_entries) >= remaining:
                break
        if not scan_hit_limit or len(new_entries) >= remaining or dropped == 0:
            return new_entries
        # 按剔除计数扩大 scan_limit 补扫，使剔除项不占本页配额；同目录扫描返回稳定
        # 前缀，consumed 游标不重复消费，竞态错位由 seen_images 去重兜底。
        scan_limit = scan_limit + dropped


def _build_display_entries(
    *,
    images: list[Path],
    image_resolved_map: dict[Path, Path],
    resolved_roots: list[Path],
    show_details: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """组装展示文本与结构化图片条目。

    文件系统相关计算集中在本函数同步执行，由调用方经 ``asyncio.to_thread`` 在线程内
    调用，避免网络挂载目录的 stat 阻塞事件循环。被忽略条目仍占位，索引序号不重排。

    Args:
        image_resolved_map: 原始路径到 resolved 路径的映射，由扫描阶段填充。

    Returns:
        (展示文本行列表，结构化图片条目列表)，文本行不含「图片列表:」标题头。
    """
    lines: list[str] = []
    structured_images: list[dict[str, Any]] = []
    for idx, img in enumerate(images, 1):
        img_resolved = image_resolved_map[img]
        display_base = next(
            (root for root in resolved_roots if is_within_resolved(img_resolved, root)),
            None,
        )
        if display_base is None:
            logger.warning("图片路径未命中任何工作区根目录，已忽略: {}", img)
            continue
        # 文件名来自服务器文件系统，经净化后才进入文本与结构化两条通道，与生成
        # 通道的净化口径一致。
        display_path = sanitize_data_text(get_relative_path(img_resolved, str(display_base)))
        detail_text, details = _format_file_info(display_path, img_resolved, show_details)
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
    """
    if raw is None:
        return None, False
    if not raw:
        return raw, True
    supported_only = [ext for ext in raw if ext in SUPPORTED_IMAGE_EXTENSIONS]
    if supported_only:
        return supported_only, False
    return raw, True


async def handle_browse_images(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None = None,
) -> CallToolResult:
    """处理图片浏览请求，扫描工作区内指定目录的图片并分页返回。

    仅允许访问 MCP Roots 授权的工作区目录；扫描结果经目录级缓存加速翻页，切片多取
    一张以判定 has_more。未预期异常降级为结构化错误返回，不向调用方抛出。
    """
    # 已解析目录列表在外层创建、impl 填充：异常兜底分支经同一引用回显已解析目录。
    resolved_directories: list[Path] = []
    try:
        return await _handle_browse_images_impl(
            params, ctx, resolved_directories=resolved_directories
        )
    except Exception as exc:
        logger.error("浏览图片处理失败", exc_info=True)
        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
        user_message = format_error_for_user(exc)
        try:
            fallback_roots = get_workspace_roots()
        except Exception:
            fallback_roots = []
        # 过滤值经同一过滤规则回显，与内层错误分支一致，保留用户原始输入。
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


async def _handle_browse_images_impl(
    params: BrowseImagesInput,
    ctx: Context[Any, Any] | None = None,
    *,
    resolved_directories: list[Path],
) -> CallToolResult:
    """执行图片浏览主逻辑，未预期异常由外层 ``handle_browse_images`` 兜底降级。

    resolved_directories 为外层创建的共享列表，解析结果供成功与兜底分支读取。
    """
    raw_format_filter, format_filter_exhausted = _normalize_format_filter(params.format_filter)

    workspace_roots = get_workspace_roots()
    resolved_dirs = resolved_directories
    state = _BrowseRequestState.from_params(
        params,
        workspace_roots=workspace_roots,
        resolved_directories=resolved_dirs,
        format_filter=raw_format_filter,
    )

    if not workspace_roots:
        message = "当前 MCP 会话未授权任何工作区目录，无法浏览本地文件。"
        return _build_browse_error(state=state, message=message)

    # resolve/normalize 可能阻塞网络挂载目录，工作区根与请求目录的预解析整体下沉
    # 线程，后续以已 resolve 的根直接比较；展示层与 structuredContent 仍回显原始
    # workspace_roots。
    def _resolve_roots_and_dirs() -> tuple[list[Path], list[Path], str | None]:
        """解析工作区根与请求目录，返回根列表、目录列表与错误消息三元组。"""
        resolved_root_list = resolve_workspace_roots(workspace_roots)
        resolved_dir_list: list[Path] = []
        error_message: str | None = None
        if Path(state.directory).is_absolute():
            try:
                absolute_dir = normalize_path(state.directory)
            except ValueError as exc:
                # 异常消息内含用户输入路径，经净化后才进入错误通道。
                return resolved_root_list, [], sanitize_error_text(f"目录路径无效: {exc}")
            if not any(is_within_resolved(absolute_dir, base) for base in resolved_root_list):
                return resolved_root_list, [], None
            resolved_dir_list.append(absolute_dir)
        else:
            for root in resolved_root_list:
                try:
                    candidate = normalize_path(state.directory, str(root))
                except ValueError as exc:
                    # 规范化失败由 UNC、驱动器相对、非法字符等路径自身缺陷决定，与
                    # 拼接的根无关，与绝对分支同口径返回「目录路径无效」；路径合法但
                    # 全部越界落到下方的超出范围分支。异常消息内含用户输入路径，
                    # 经净化后进入错误通道。
                    return resolved_root_list, [], sanitize_error_text(f"目录路径无效: {exc}")
                if not is_within_resolved(candidate, root):
                    continue
                if candidate not in resolved_dir_list:
                    resolved_dir_list.append(candidate)
        return resolved_root_list, resolved_dir_list, error_message

    resolved_roots, resolved_dirs_from_thread, dir_error = await asyncio.to_thread(
        _resolve_roots_and_dirs
    )

    if dir_error is not None:
        return _build_browse_error(state=state, message=dir_error)
    resolved_dirs.extend(resolved_dirs_from_thread)

    if not resolved_dirs:
        # 回退边界下不回显允许根清单，避免暴露服务器环境结构。
        if is_boundary_from_session_roots():
            allowed_roots = ", ".join(str(root) for root in workspace_roots)
            message = f"目录超出允许范围。仅允许浏览工作区目录: {allowed_roots}"
        else:
            message = "目录超出允许范围。仅允许浏览服务器配置的工作区目录。"
        return _build_browse_error(state=state, message=message)

    await _safe_report_progress(ctx, progress=PROGRESS_SCAN_START, message="开始扫描图片目录")

    logger.info(
        "浏览图片: dirs={}, recursive={}, max_depth={}, limit={}",
        resolved_dirs,
        state.recursive,
        state.max_depth,
        state.limit,
    )

    # 逐目录扫描并合并结果，scan_limit 取 offset+limit+1，多取一张用于判定 has_more；
    # 越界项与重复项的剔除及补扫见 _scan_and_filter_directory。format_filter_exhausted
    # 时跳过扫描，由空结果分支统一返回。
    scan_limit = state.offset + state.limit + 1
    all_images: list[Path] = []
    image_resolved_map: dict[Path, Path] = {}
    unreadable_dirs: list[Path] = []
    if not format_filter_exhausted:
        seen_images: set[Path] = set()
        total_dirs = len(resolved_dirs)
        for dir_index, resolved_dir in enumerate(resolved_dirs, start=1):
            if len(all_images) >= scan_limit:
                break
            remaining = scan_limit - len(all_images)
            new_entries = await asyncio.to_thread(
                _scan_and_filter_directory,
                resolved_dir=resolved_dir,
                recursive=state.recursive,
                max_depth=state.max_depth,
                format_filter=state.format_filter,
                remaining=remaining,
                resolved_roots=resolved_roots,
                seen_images=seen_images,
                unreadable_dirs=unreadable_dirs,
            )
            for image_path, image_resolved in new_entries:
                all_images.append(image_path)
                image_resolved_map[image_path] = image_resolved
            # 多目录时按目录占比上报中间进度，区间为 20% 至 90%；上报须留在事件
            # 循环，不能在线程内调用 ctx。
            if total_dirs > 1:
                await _safe_report_progress(
                    ctx,
                    progress=PROGRESS_SCAN_START + PROGRESS_SCAN_SPAN * dir_index / total_dirs,
                    message=f"已扫描 {dir_index}/{total_dirs} 个目录，找到 {len(all_images)} 张图片",
                )

    # 分页切片：has_more 时未扫完全量、总数未知，total_count 置 None。
    page_end = state.offset + state.limit
    images = all_images[state.offset : page_end]
    has_more = len(all_images) > page_end
    next_offset = page_end if has_more else None
    total_count = None if has_more else len(all_images)

    # 空页按错误可归因性分流：format_filter_exhausted 与 offset 越界是模型可自纠的
    # 参数错误，返回 is_error=True 与结构化错误标记；目录不可读与无图片非模型可修复，
    # 维持空结果语义，文案区分「目录不可读」与「无图片」。
    if not images:
        if format_filter_exhausted:
            supported_list = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            if state.format_filter:
                # 用户 filter 字符串经净化后拼入消息；支持列表为静态服务端数据，
                # 不参与净化。
                user_formats = sanitize_error_text(", ".join(state.format_filter))
                message = (
                    f"指定的图片格式 {user_formats} 均不在支持列表内，支持: {supported_list}。"
                )
            else:
                # 空列表无格式可回显，改用不含空位的文案，避免残缺语义。
                message = f"未指定任何受支持的图片格式，支持: {supported_list}。"
            await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
            return _build_browse_error(state=state, message=message)
        if total_count:
            # 消息携带总数与有效区间，模型修正 offset 后即可重试。
            message = (
                f"offset={state.offset} 超出范围，目录共有 {total_count} 张图片，"
                f"请使用 0 <= offset < {total_count}。"
            )
            await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
            return _build_browse_error(state=state, message=message)
        if unreadable_dirs:
            unique_unreadable = list(dict.fromkeys(unreadable_dirs))
            if is_boundary_from_session_roots():
                dirs_text = ", ".join(str(item) for item in unique_unreadable)
            else:
                # 回退边界不回显路径，仅按数量提示，明细进日志。
                dirs_text = f"{len(unique_unreadable)} 个目录（回退边界场景不回显路径）"
            message = f"目录不可读或无图片文件：{dirs_text}"
        else:
            message = "未找到图片文件，请确认目录或过滤条件。"
        await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structured_content=_build_browse_structured_result(
                status="empty",
                workspace_roots=state.workspace_roots,
                directory=state.directory,
                resolved_directories=state.resolved_directories,
                recursive=state.recursive,
                max_depth=state.max_depth,
                limit=state.limit,
                offset=state.offset,
                show_details=state.show_details,
                format_filter=state.format_filter,
                success=True,
                images=[],
                total_count=total_count,
                has_more=has_more,
                next_offset=next_offset,
            ),
            is_error=False,
        )

    display_lines, structured_images = await asyncio.to_thread(
        _build_display_entries,
        images=images,
        image_resolved_map=image_resolved_map,
        resolved_roots=resolved_roots,
        show_details=state.show_details,
    )
    lines = ["图片列表:"] + display_lines
    if has_more:
        # has_more 时 total_count 恒为 None，翻页引导仅给出当前页区间。
        page_last = state.offset + len(images)
        range_text = f"第 {state.offset + 1}-{page_last} 张"
        lines.append(f"{range_text}，仍有更多，继续翻页请传 offset={next_offset}")

    await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="扫描完成")

    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(lines))],
        structured_content=_build_browse_structured_result(
            status="completed",
            workspace_roots=state.workspace_roots,
            directory=state.directory,
            resolved_directories=state.resolved_directories,
            recursive=state.recursive,
            max_depth=state.max_depth,
            limit=state.limit,
            offset=state.offset,
            show_details=state.show_details,
            format_filter=state.format_filter,
            success=True,
            images=structured_images,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
        ),
        is_error=False,
    )
