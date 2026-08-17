"""图片浏览工具的 impl 处理器。

直接实现工作区图片扫描与分页，不经 ``execute_generation_handler`` 生成流水线；字段规则
由 schemas.BrowseImagesInput 单一定义。
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

# 回退边界占位回显：工作区根来自 SEEDREAM_WORKSPACE_ROOT 或进程 CWD 回退而非客户端
# 会话 Roots 声明时，回显绝对路径会向调用方暴露服务器环境结构，字段与消息统一以
# 本占位符替代真实路径。
_FALLBACK_BOUNDARY_PLACEHOLDER = "<工作区根（服务器配置）>"


@dataclass(frozen=True)
class _BrowseRequestState:
    """单次浏览请求的状态快照，错误分支与兜底分支共享取值。

    Attributes:
        workspace_roots: 客户端授权的工作区根目录列表，保留原始形态供错误消息与
            structuredContent 回显。
        directory: 用户请求的目录字符串，未提供时归一为当前目录 "."。
        resolved_directories: 由 handle_browse_images 创建并传入 impl 的共享活动列表，
            解析流程逐步填充；成功分支在填充完成后读取同一引用，外层兜底分支在 impl
            抛出未预期异常时仍能取到已解析的目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归扫描的最大深度。
        limit: 单页返回的图片条数上限。
        offset: 分页起始偏移，与 limit 共同决定当前页切片。
        show_details: 是否在文本与结构化条目中附带文件大小与修改时间。
        format_filter: 经支持列表过滤后的扩展名白名单，None 表示不限制格式。
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
        """从类型化输入模型与既有状态构建请求快照，impl 与兜底分支共享同一取值逻辑。"""
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
    """格式化文件信息，返回展示文本与结构化详情字段。

    show_details 为真时读取文件大小与修改时间，文本格式为 “路径 | 大小 | 修改时间”，
    结构化详情含 size_mb 与 modified 两键；stat 失败时文本追加 “文件信息不可用”，两键置 None。
    show_details 为假时仅返回路径文本与空详情字典。

    Args:
        display_path: 展示给用户的文件路径字符串。
        stat_path: 用于读取文件属性的实际路径对象。
        show_details: 是否显示文件详细信息（大小、修改时间）。

    Returns:
        展示文本与结构化详情字段字典组成的二元组。
    """
    if not show_details:
        return display_path, {}
    try:
        stat_result = stat_path.stat()
    except OSError:
        return f"{display_path} | 文件信息不可用", {"size_mb": None, "modified": None}
    size_mb = stat_result.st_size / (1024 * 1024)
    # astimezone 将 naive 本地时间标注为本地时区，输出携带 UTC 偏移以消除时区歧义。
    # 畸形时间戳（负值或超范围）会使 fromtimestamp 抛 ValueError/OSError，与 stat 失败
    # 同样降级为“文件信息不可用”，不使整次浏览落到兜底错误分支。
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
    """集中构建 browse_images 工具的 structuredContent。

    经 BrowseImagesStructuredOutput 构造后 model_dump，使 runtime 输出与声明的
    outputSchema 绑定，字段漂移在构造时即暴露。成功、空结果与失败三分支共用此构建；
    失败分支以默认值填充非关键字段，符合 BrowseImagesStructuredOutput 全部字段可选的
    声明。成功与空结果路径不输出 error 键，与既有契约一致。

    边界来自 env/CWD 回退而非会话 Roots 声明时，workspace_roots 与
    resolved_directories 的路径值以占位符替代，不向调用方回显服务器本地目录结构；
    会话 Roots 场景保持回显客户端自己声明的路径。
    """
    if is_boundary_from_session_roots():
        workspace_root_values = [str(root) for root in workspace_roots]
        resolved_directory_values = [str(item) for item in resolved_directories]
    else:
        workspace_root_values = [_FALLBACK_BOUNDARY_PLACEHOLDER] if workspace_roots else []
        resolved_directory_values = [_FALLBACK_BOUNDARY_PLACEHOLDER] if resolved_directories else []
    payload: dict[str, Any] = {
        "tool": "seedream_browse_images",
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
    """集中构造 browse_images 工具的错误 CallToolResult。

    各错误分支共享同一请求状态快照与 is_error=True 语义，仅 message 不同；通过此辅助函数
    统一构造，避免重复展开相同状态字段。structuredContent.error.type 恒为 browse_failed，
    message 同时作为可见文本与结构化错误原因，二者保持一致。
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
    """扫描单个目录并完成越界判定与去重，返回新增的 (原始路径, resolved 路径) 列表。

    越界判定与去重集中在本函数同步执行，由调用方通过 ``asyncio.to_thread`` 在线程内调用；
    每张图片的 resolve 由扫描缓存层在扫描完成时执行一次并随缓存条目共享，深翻页命中缓存时
    本函数直接取已 resolve 对，不再逐文件重复 resolve。越界复核与去重不随缓存固化，每次
    请求按当前工作区根重新执行，保留安全语义。``seen_images`` 跨目录共享以去重重叠根目录的
    重复图片；调用方按目录串行 await，无并发写竞争。

    剔除项不占用分页配额：扫描层按 scan_limit 早停，早停窗口内的越界符号链接与跨目录
    重复项在扫描之后才被剔除，若不补偿，剔除项占满配额会使 has_more 假阴性、total_count
    低报、尾部图片翻页不可达。本函数在扫描命中上限、未填满配额且存在剔除时，按剔除计数
    扩大 scan_limit 补扫，直至填满配额、扫到目录末尾或无剔除项。补扫经已处理位置的续扫
    游标跳过此前已消费的扫描前缀，同目录的扫描结果为字典序稳定前缀，续扫不重复消费；
    scan_limit 随剔除数严格递增，目录文件数有限，循环必然终止。

    Args:
        resolved_dir: 已 resolve 的待扫描目录。
        recursive: 是否递归扫描子目录。
        max_depth: 递归最大深度。
        format_filter: 图片扩展名白名单，None 表示全部支持的后缀。
        remaining: 距 scan_limit 上限的剩余配额，新增条目不超过此值。
        resolved_roots: 已 resolve 的工作区根列表，用于越界判定。
        seen_images: 跨目录共享的已见原始路径集合，函数内就地更新。
        unreadable_dirs: 跨目录共享的不可读目录收集列表，函数内就地更新，供空结果
            分支区分目录不可读与目录内无图片。

    Returns:
        新增 (原始路径, resolved 路径) 元组列表，长度不超过 remaining。
    """
    new_entries: list[tuple[Path, Path]] = []
    scan_limit = remaining
    # 续扫游标：当前轮扫描前缀中已消费的条目数，补扫轮从该位置继续，不重复消费。
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
        # 返回量达到 scan_limit 说明目录可能仍有后续条目，未达到即已扫到末尾。
        scan_hit_limit = len(matched_image_pairs) >= scan_limit
        dropped = 0
        while consumed < len(matched_image_pairs):
            image_path, image_resolved = matched_image_pairs[consumed]
            consumed += 1
            # resolve 结果来自扫描缓存层；root 已 resolve，直接做 relative_to 比较。
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
        # 早停窗口内存在剔除且配额未填满：按剔除计数扩大 scan_limit 补扫，使剔除项
        # 不占掉本页可见配额。consumed 游标假设同目录补扫返回稳定前缀，扫描层按
        # normcase 排序且缓存按 mtime 失效，单次请求内目录内容变化的竞态窗口极窄，
        # 错位条目由 seen_images 去重兜底。
        scan_limit = scan_limit + dropped


def _build_display_entries(
    *,
    images: list[Path],
    image_resolved_map: dict[Path, Path],
    resolved_roots: list[Path],
    show_details: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    """组装面向用户的展示文本与结构化图片条目。

    将 is_within_resolved 命中查找、get_relative_path 与 _format_file_info 的 stat 等文件
    系统相关计算集中在本函数同步执行，由调用方通过 ``asyncio.to_thread`` 在线程内调用，避免
    show_details 下网络挂载目录的 stat 阻塞事件循环。索引编号沿用 enumerate 语义：被
    忽略条目仍占位、保留原序号不重排。

    Args:
        images: 当前页图片原始路径列表。
        image_resolved_map: 原始路径到 resolved 路径的缓存，由扫描阶段填充。
        resolved_roots: 已 resolve 的工作区根列表，用于定位展示基准根。
        show_details: 是否在文本与结构化条目中附加文件大小与修改时间。

    Returns:
        (展示文本行列表，结构化图片条目列表)。文本行不含 “图片列表:” 标题头。
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
        # 文件名来自服务器文件系统，含控制字符的文件名经净化后才进入文本与结构化
        # 两条通道，与生成通道对 local_path/markdown_ref 的净化口径一致。
        display_path = sanitize_data_text(get_relative_path(img_resolved, str(display_base)))
        detail_text, details = _format_file_info(display_path, img_resolved, show_details)
        lines.append(f"{idx}. {detail_text}")
        entry: dict[str, Any] = {"index": idx, "path": display_path}
        entry.update(details)
        structured_images.append(entry)
    return lines, structured_images


def _normalize_format_filter(raw: list[str] | None) -> tuple[list[str] | None, bool]:
    """过滤出受支持的图片扩展名，返回 (过滤值, 是否无有效后缀)。

    仅保留受支持的扩展名，避免以非图片后缀探测文件。空列表与全部不受支持的输入
    语义一致：均为“无有效后缀”，标记 exhausted 并保留原始输入供 structuredContent
    回显；不能把空列表传给 find_images_in_directory，其把空列表视为未限制而扫描
    全部。未提供（None）不限制、不标记。
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
    """处理图片浏览请求，扫描工作区内指定目录的图片文件并分页返回。

    仅允许访问 MCP Roots 授权的工作区目录；扫描结果按目录 mtime 缓存以加速翻页，切片
    offset+limit+1 张以判定 has_more。完整字段规则与默认值见 ``BrowseImagesInput``，
    本函数读取类型化入参模型。未预期异常被外层捕获并降级为结构化错误，与生成类
    ``execute_generation_handler`` 的错误结构对齐，不向调用方抛出。

    Args:
        params: 经 pydantic 校验的图片浏览入参模型。
        ctx: MCP 上下文，用于进度上报，无会话时可为 None。

    Returns:
        MCP 标准工具结果，含面向模型的图片列表文本与 structuredContent。
    """
    # 已解析目录列表在进入 impl 前创建并由两函数共享引用：impl 内部逐步填充，抛出
    # 未预期异常时兜底分支经同一引用回显已解析目录，不再恒为空列表。
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
        # 过滤值回显与内层错误分支保持一致：经同一过滤规则而非置 None，避免丢失
        # 用户原始过滤输入。
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

    resolved_directories 为外层创建的共享列表，目录解析结果在填充该列表后供成功
    分支与外层兜底分支共同读取。
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

    # 预解析工作区根与请求目录：resolve/normalize 均为可能阻塞网络挂载目录的同步
    # 文件系统调用，整体下沉线程执行；去重与展示阶段直接用 is_within_resolved 与这些
    # 已 resolve 的 root 比较，root 无需重复 resolve。图片路径由扫描缓存层 resolve，
    # 结果经 image_resolved_map 供展示阶段复用。展示层与 structuredContent 仍回显原始
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
                # 异常消息内含完整用户输入路径，经净化截断后才进入文本与结构化
                # 错误两条通道，与生成侧 format_error_for_user 的出口防护对齐。
                return resolved_root_list, [], sanitize_error_text(f"目录路径无效: {exc}")
            if not any(is_within_resolved(absolute_dir, base) for base in resolved_root_list):
                return resolved_root_list, [], None
            resolved_dir_list.append(absolute_dir)
        else:
            for root in resolved_root_list:
                try:
                    candidate = normalize_path(state.directory, str(root))
                except ValueError as exc:
                    # 相对路径的规范化失败由路径自身缺陷决定（UNC、驱动器相对、非法
                    # 字符等），与拼接的根无关，首个根即失败时与绝对分支同口径返回
                    # 「目录路径无效」；路径合法但全部越界才落到下方的超出范围分支。
                    # 异常消息内含完整用户输入路径，经净化截断后才进入错误通道。
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
        # 越界拒绝消息回显允许根清单仅在边界来自会话 Roots 声明时进行；env/CWD
        # 回退边界下回显绝对路径会暴露服务器环境结构，改述为服务器配置边界。
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

    # 搜索图片文件：_scan_and_filter_directory 经 cached_find_images_in_directory 扫描目录，
    # 翻页共享有序列表缓存，非递归按目录 mtime 失效、递归按 TTL 失效；scan_limit 用于早停
    # 与切片判定 has_more。越界项即 resolve 后落在工作区外的符号链接，与跨目录重复项在
    # 扫描之后才被剔除；_scan_and_filter_directory 对被剔除项按剔除计数补扫，补齐其占用
    # 的早停配额，分页语义不受剔除项影响。format_filter_exhausted 时跳过扫描，all_images
    # 保持为空，由下方空结果分支统一返回。
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
            # 多目录扫描时按已扫描目录占比上报中间进度，区间为 20% 至 90%；单目录跳过。
            # 进度上报必须留在事件循环，不能在线程内调用 ctx。
            if total_dirs > 1:
                await _safe_report_progress(
                    ctx,
                    progress=PROGRESS_SCAN_START + PROGRESS_SCAN_SPAN * dir_index / total_dirs,
                    message=f"已扫描 {dir_index}/{total_dirs} 个目录，找到 {len(all_images)} 张图片",
                )

    # 分页切片：has_more 时仅扫到 scan_limit、无法精确总数，total_count 置 None。
    page_end = state.offset + state.limit
    images = all_images[state.offset : page_end]
    has_more = len(all_images) > page_end
    next_offset = page_end if has_more else None
    total_count = None if has_more else len(all_images)

    # 处理当前页为空的情况，按错误可归因性分流：
    # - format_filter_exhausted 与 offset 越界为模型可自纠的参数错误，走错误结果
    #   路径返回 is_error=True 与结构化错误标记，客户端 UI 与模型据错误信号修正
    #   参数后重试，符合官方错误信号约定；
    # - 目录不可读与目录内无图片非模型可修复，维持空结果语义；扫描中存在不可读
    #   目录时文案区分「目录不可读」，避免权限问题被静默归并为「无图片」。
    if not images:
        if format_filter_exhausted:
            supported_list = ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            if state.format_filter:
                # 用户提交的 filter 字符串经错误文本净化出口收敛后拼入消息，凭据样式
                # 片段被脱敏、超长输入被截断；支持列表为静态服务端数据，不参与净化，
                # 保留完整可读。
                user_formats = sanitize_error_text(", ".join(state.format_filter))
                message = (
                    f"指定的图片格式 {user_formats} 均不在支持列表内，支持: {supported_list}。"
                )
            else:
                # 空列表为文档明示的合法输入，无用户格式可回显时改用不含量词空位的
                # 文案，避免双空格与残缺语义。
                message = f"未指定任何受支持的图片格式，支持: {supported_list}。"
            await _safe_report_progress(ctx, progress=PROGRESS_COMPLETE, message="浏览图片处理失败")
            return _build_browse_error(state=state, message=message)
        if total_count:
            # offset 越界消息携带实际总数与有效区间，模型修正 offset 后即可重试成功。
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
                # 回退边界场景回显已 resolve 的目录绝对路径会泄露服务器环境结构，
                # 仅按数量提示，明细进日志。
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
        # 满页且仍有更多时在文本尾部追加翻页引导；has_more 意味着未扫完全量，
        # total_count 此时恒为 None，省略总数表述仅给出当前页区间。
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
