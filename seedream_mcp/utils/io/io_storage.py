"""文件管理模块：生成图片保存路径、写入字节内容并清理旧文件。

负责按日期与工具名组织保存路径、净化文件名、用内容哈希做去重，以及按保留天数
清理旧文件。落盘写入与旧文件遍历均通过 io_file 防符号链接，避免经由符号链接逃逸
出保存目录。字节签名嗅探扩展名由 core.formats 统一提供，调用方直接使用模块函数。
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
import stat
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import SeedreamMCPError
from ..core.formats import (
    DEFAULT_IMAGE_EXTENSION,
    SUPPORTED_IMAGE_EXTENSIONS,
)
from ..core.logs import get_logger
from .io_file import (
    atomic_replace_from_fd_sync,
    has_reparse_attribute,
)
from .io_path import is_unc_path, is_windows_reserved_name, is_within_resolved, resolve_images_root
from .io_url import get_file_extension_from_url

logger = get_logger()


def current_save_time() -> str:
    """保存记录的统一时间基准：本地时间带时区偏移，与文件名、日期子目录一致。"""
    return datetime.now().astimezone().isoformat()


# 文件名长度上限，避免超出常见文件系统目录项长度限制。
_MAX_FILENAME_LENGTH = 200

# 文件名扩展名长度上限：超过该值的扩展名不可能是合法图片后缀，按纯词干截断，
# 防止超长扩展名使文件名长度截断失效。
_MAX_EXTENSION_LENGTH = 16

# 唯一文件名词干基础的长度预算：Windows 默认 MAX_PATH 260，custom_name 合法输入
# 上限 255 字符，不加预算地拼接完整路径必然超限导致自动保存失败，按预算截断后
# 合法输入可正常落盘。
_MAX_UNIQUE_BASE_LENGTH = 120

# 遗留临时文件清扫的 mtime 宽限秒数：仅删除早于该时限的 .part 条目，在途下载与
# 写入的临时文件恒新于宽限值不被并发清理击杀，合法下载总预算为小时级，低于宽限。
_PART_SWEEP_GRACE_SECONDS = 24 * 3600

# 清理遍历的条目告警阈值：按天与配额策略需要全量收集，不设预算截断，超阈记一条
# warning 暴露异常规模的保存目录，量级对齐 io_scan 的扫描条目预算。
_CLEANUP_ENTRY_WARN_THRESHOLD = 20000


class FileManagerError(SeedreamMCPError):
    """文件管理相关操作失败。"""

    pass


class FileManager:
    """图片保存路径生成、字节写入与旧文件清理的统一入口。

    Attributes:
        base_dir: 图片保存目录，已 resolve 的绝对路径。
    """

    def __init__(self, base_dir: Path | None = None):
        """初始化文件管理器并确保保存目录存在。

        Args:
            base_dir: 图片保存目录。默认为 io_path.resolve_images_root 求值的
                图片目录，与目录体系的单一求值权威一致。

        Raises:
            FileManagerError: 保存目录为 UNC 形式、解析失败或指向已存在文件。
            SeedreamConfigError: 默认图片目录求值失败。
        """
        raw_base = resolve_images_root() if base_dir is None else Path(base_dir)
        # UNC 的 resolve 会触发 SMB 认证，直连构造入口与调用方同口径在 resolve 前拒绝。
        if is_unc_path(str(raw_base)):
            raise FileManagerError(f"拒绝 UNC 路径以避免触发 SMB 连接: {raw_base}")
        try:
            resolved = raw_base.resolve()
        except (OSError, ValueError) as e:
            raise FileManagerError(f"解析保存路径时出错: {e}") from e
        # 仅拒绝指向已存在文件的路径；save_path 为调用级保存声明，位置不受限，
        # 空字节等其余路径形态由调用方 tools/core/_shared 在 resolve 前拒绝。
        if resolved.exists() and not resolved.is_dir():
            raise FileManagerError(f"保存路径不是目录: {resolved}")
        base_dir = resolved

        self.base_dir = base_dir
        self.ensure_directory(self.base_dir)

    def ensure_directory(self, path: Path) -> None:
        """确保目录存在，不存在则递归创建。

        Args:
            path: 目录路径。

        Raises:
            FileManagerError: 创建目录失败时抛出。
        """
        try:
            path.mkdir(parents=True, exist_ok=True)
            logger.debug("确保目录存在: {}", path)
        except (OSError, ValueError) as e:
            # Python 3.13 起 pathlib 对嵌入 null 字符等非法路径参数抛 ValueError
            # 而非 OSError，与解析失败一并归一为 FileManagerError。
            raise FileManagerError(f"创建目录失败: {path} -> {e}") from e

    def validate_path(self, path: Path) -> bool:
        """验证路径是否在保存目录范围内。

        复用 io_path.is_within_resolved 做 resolve 后的包含判定，可拦截包含 ``..``
        或经由符号链接指向保存目录之外的路径。

        Args:
            path: 要验证的路径。

        Returns:
            路径在保存目录范围内返回 True，否则返回 False。
        """
        try:
            abs_path = path.resolve()
            if is_within_resolved(abs_path, self.base_dir):
                return True
            logger.warning("路径不在保存目录内: {}", abs_path)
            return False
        except Exception as e:
            logger.warning("路径验证失败: {} -> {}", path, e)
            return False

    def _resolved_within_base(self, resolved_path: Path) -> bool:
        """判断已 resolve 的路径是否位于保存目录内，直接比较，不再 resolve。

        委托 io_path.is_within_resolved 保持包含判定的单一实现，供
        run_cleanup_policies 等热路径复用，避免对已 resolve 路径重复解析。
        """
        return is_within_resolved(resolved_path, self.base_dir)

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除文件系统不安全字符并规避 Windows 保留设备名。

        Args:
            filename: 原始文件名。

        Returns:
            清理后仅含安全字符的文件名。
        """
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename)

        # 超长文件名截断词干并保留扩展名；超长扩展名不可能是合法后缀，按纯词干
        # 整体截断，防止 name[:0] + ext 使截断失效。
        if len(filename) > _MAX_FILENAME_LENGTH:
            name, ext = os.path.splitext(filename)
            if len(ext) > _MAX_EXTENSION_LENGTH:
                filename = filename[:_MAX_FILENAME_LENGTH]
            else:
                filename = name[: _MAX_FILENAME_LENGTH - len(ext)] + ext

        # Windows 保留设备名处理：CON.txt、NUL 等会被解释为设备而非文件，命中时在
        # 首个点前追加下划线；词干归一判定经 is_windows_reserved_name 与
        # normalize_path 单一来源。
        if is_windows_reserved_name(filename):
            parts = filename.split(".", 1)
            parts[0] += "_"
            filename = ".".join(parts)

        if not filename.strip():
            filename = "unnamed"

        return filename.strip()

    def generate_name_from_prompt(self, prompt: str, max_length: int = 50) -> str:
        """从提示词生成可读的文件名基础部分。

        Args:
            prompt: 生成提示词。
            max_length: 文件名基础部分最大长度。

        Returns:
            由提示词派生的小写文件名基础部分。
        """
        if not prompt:
            return "image"

        clean_prompt = re.sub(r"[^\w\s-]", "", prompt)
        clean_prompt = re.sub(r"\s+", "_", clean_prompt)

        if len(clean_prompt) > max_length:
            clean_prompt = clean_prompt[:max_length]

        clean_prompt = clean_prompt.strip("_")

        if not clean_prompt:
            clean_prompt = "image"

        return clean_prompt.lower()

    def generate_unique_filename(
        self,
        base_name: str,
        extension: str,
        content_hash: str | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        """生成包含时间戳与唯一性后缀的文件名。

        Args:
            base_name: 基础名称，清理后的词干超过 _MAX_UNIQUE_BASE_LENGTH 时截断
                至该长度，为时间戳、哈希/随机后缀与目录前缀预留长度预算。
            extension: 文件扩展名，包含点号。
            content_hash: 内容哈希值，提供则取其前 8 位嵌入文件名。
            timestamp: 时间戳，默认取当前时间。

        Returns:
            唯一文件名，词干已按预算截断，完整路径长度可控。
        """
        if timestamp is None:
            timestamp = datetime.now()

        clean_base = self.sanitize_filename(base_name)
        if len(clean_base) > _MAX_UNIQUE_BASE_LENGTH:
            clean_base = clean_base[:_MAX_UNIQUE_BASE_LENGTH]

        # [:-3] 截掉微秒末三位，得到毫秒精度时间戳。
        time_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]

        unique_suffix = uuid.uuid4().hex[:8]

        if content_hash:
            hash_part = content_hash[:8]
            filename = f"{clean_base}_{time_str}_{hash_part}{extension}"
        else:
            filename = f"{clean_base}_{time_str}_{unique_suffix}{extension}"

        return filename

    def get_content_hash(self, content: bytes) -> str:
        """计算内容的 SHA256 哈希值。

        Args:
            content: 文件内容。

        Returns:
            SHA256 十六进制哈希值。
        """
        return hashlib.sha256(content).hexdigest()

    def get_organized_path(
        self, filename: str, subfolder: str | None = None, date_folder: bool = True
    ) -> Path:
        """在保存目录下按日期与子目录组织文件路径。

        Args:
            filename: 文件名。
            subfolder: 子文件夹名称，通常为工具名。
            date_folder: 是否按日期创建一级子目录。

        Returns:
            组织后的完整文件路径。
        """
        path = self.base_dir

        if date_folder:
            today = datetime.now().strftime("%Y-%m-%d")
            path = path / today

        if subfolder:
            clean_subfolder = self.sanitize_filename(subfolder)
            path = path / clean_subfolder

        self.ensure_directory(path)

        return path / filename

    def create_save_path(
        self,
        prompt: str,
        url: str,
        tool_name: str = "seedream",
        custom_name: str | None = None,
        date_folder: bool = True,
    ) -> Path:
        """根据提示词与 URL 生成图片保存路径。

        Args:
            prompt: 生成提示词，用于派生文件名基础部分。
            url: 图片 URL，用于推断扩展名。
            tool_name: 工具名称，用作保存子目录。
            custom_name: 自定义文件名基础部分，覆盖提示词派生。
            date_folder: 是否按日期创建一级子目录。

        Returns:
            保存路径。

        Raises:
            FileManagerError: 生成的保存路径越出保存目录。
        """
        if custom_name:
            base_name = custom_name
        else:
            base_name = self.generate_name_from_prompt(prompt)

        extension = get_file_extension_from_url(url)
        # 收敛到受支持图片扩展名白名单，防止 URL 派生的 .html/.aspx 等任意后缀落盘。
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            extension = DEFAULT_IMAGE_EXTENSION

        filename = self.generate_unique_filename(base_name, extension)

        return self._finalize_save_path(filename, tool_name, date_folder)

    def create_save_path_from_extension(
        self,
        prompt: str,
        extension: str,
        tool_name: str = "seedream",
        custom_name: str | None = None,
        content_hash: str | None = None,
        date_folder: bool = True,
    ) -> Path:
        """基于已知扩展名生成保存路径，供字节签名嗅探出真实类型后使用。

        Args:
            prompt: 生成提示词，用于派生文件名基础部分。
            extension: 文件扩展名，包含点号；不在受支持图片扩展名白名单时回退默认
                图片扩展名，防止任意后缀经本入口落盘。
            tool_name: 工具名称，用作保存子目录。
            custom_name: 自定义文件名基础部分，覆盖提示词派生。
            content_hash: 内容哈希，嵌入文件名用于去重与标识。
            date_folder: 是否按日期创建一级子目录。

        Returns:
            保存路径。

        Raises:
            FileManagerError: 生成的保存路径越出保存目录。
        """
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            extension = DEFAULT_IMAGE_EXTENSION
        base_name = custom_name or self.generate_name_from_prompt(prompt)
        filename = self.generate_unique_filename(base_name, extension, content_hash=content_hash)
        return self._finalize_save_path(filename, tool_name, date_folder)

    def _finalize_save_path(self, filename: str, tool_name: str, date_folder: bool) -> Path:
        """按日期与工具子目录组织保存路径并校验越界，返回最终保存路径。

        Args:
            filename: 已含扩展名的唯一文件名。
            tool_name: 工具名称，用作保存子目录。
            date_folder: 是否按日期创建一级子目录。

        Raises:
            FileManagerError: 生成的保存路径越出保存目录。
        """
        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)
        if not self.validate_path(save_path):
            raise FileManagerError(f"路径不安全: {save_path}")
        return save_path

    def save_bytes(
        self,
        file_path: Path,
        data: bytes,
        overwrite: bool = False,
        ensure_parent: bool = True,
        fsync: bool = False,
    ) -> dict[str, Any]:
        """将字节数据写入文件，返回保存结果元数据。

        overwrite=False 时的已存在检查与改名属 best-effort：检查与原子替换之间存在
        竞态窗口，极小概率下并发写入方会落到同一目标文件。文件名含时间戳与随机或
        内容哈希后缀，碰撞概率可忽略，不为该窗口加锁兜底。

        Args:
            file_path: 目标路径。
            data: 字节数据。
            overwrite: 是否覆盖已有文件。
            ensure_parent: 是否确保父目录存在；调用方已建目录时可传 False 跳过重复 mkdir。
            fsync: 写入后、原子替换前是否对文件执行 os.fsync 刷入稳定存储。

        Returns:
            保存结果元数据，包含最终路径、大小与保存时间。

        Raises:
            FileManagerError: 目录创建或文件写入失败。
        """
        try:
            if ensure_parent:
                self.ensure_directory(file_path.parent)
            final_path = file_path
            if final_path.exists() and not overwrite:
                base = final_path.stem
                ext = final_path.suffix
                short_hash = self.get_content_hash(data)[:8]
                final_path = final_path.with_name(f"{base}_{short_hash}{ext}")

            # 原子落盘由 io_file 的同步骨架提供，与 io_download 的异步骨架同一协议。
            def _writer(fd: int) -> None:
                with os.fdopen(fd, "wb", closefd=False) as f:
                    f.write(data)

            atomic_replace_from_fd_sync(final_path, _writer, suffix=".part", fsync=fsync)
            return {
                "file_path": str(final_path),
                "file_size": len(data),
                "save_time": current_save_time(),
            }
        except OSError as e:
            raise FileManagerError(f"写入文件失败: {file_path} -> {e}") from e

    def relative_to_base(self, file_path: Path) -> str:
        """获取文件相对于保存目录的路径。

        Args:
            file_path: 文件路径。

        Returns:
            相对路径字符串；不在保存目录内则返回绝对路径。
        """
        try:
            return str(file_path.relative_to(self.base_dir))
        except ValueError:
            return str(file_path)

    def generate_markdown_reference(self, file_path: Path, alt_text: str = "") -> str:
        """生成 Markdown 图片引用。

        Args:
            file_path: 文件路径。
            alt_text: 替代文本。

        Returns:
            Markdown 引用字符串。
        """
        # 相对 base_dir 生成路径且统一正斜杠；空格、圆括号、# 与 % 百分号编码：# 会
        # 被视为 fragment 起点，% 会被误解码。百分号必须最先编码，后编码会使其余
        # 编码产物中的百分号被二次编码。
        relative_path = self.relative_to_base(file_path)
        markdown_path = relative_path.replace("\\", "/")
        markdown_path = (
            markdown_path.replace("%", "%25")
            .replace("#", "%23")
            .replace(" ", "%20")
            .replace("(", "%28")
            .replace(")", "%29")
        )

        if not markdown_path.startswith("./"):
            markdown_path = "./" + markdown_path

        if alt_text:
            return f"![{alt_text}]({markdown_path})"
        else:
            return f"![]({markdown_path})"

    def run_cleanup_policies(self, days: int, max_total_bytes: int | None) -> dict[str, Any]:
        """单次目录扫描依次执行按天清理、总量配额驱逐与遗留临时文件清扫。

        共享一次遍历结果执行三项处理，避免重复全目录 os.walk；配额驱逐基于按天
        清理后的剩余文件计算。days 小于 1 跳过按天清理，max_total_bytes 为 None
        跳过配额驱逐，.part 清扫与空目录回收不受两项开关门控。空目录清理不区分
        目录来源，用户自建的空目录也会被移除，需保留目录结构请放置占位文件；超龄
        .part 清扫的宽限语义见 _sweep_orphan_part_files。

        Args:
            days: 按天清理的保留天数，小于 1 跳过按天清理。
            max_total_bytes: 保存目录总字节上限；None 跳过配额驱逐。

        Returns:
            合并的清理结果，包含各策略累计的删除文件数、释放字节数与错误列表。
        """
        errors: list[str] = []
        deleted_files = 0
        deleted_size = 0
        try:
            all_files, part_files, directories = self._collect_all_files(errors)
            remaining_files = all_files
            if days >= 1:
                deleted_names, age_deleted_size = self._apply_age_policy(all_files, days, errors)
                deleted_files += len(deleted_names)
                deleted_size += age_deleted_size
                if deleted_names:
                    deleted_set = set(deleted_names)
                    remaining_files = [item for item in all_files if item[0] not in deleted_set]
            if max_total_bytes is not None:
                quota_deleted, quota_deleted_size = self._enforce_quota_from_scan(
                    remaining_files, max_total_bytes, errors
                )
                deleted_files += quota_deleted
                deleted_size += quota_deleted_size
            # 临时文件清扫先于空目录回收：仅含遗留 .part 的目录在清扫后变空，本轮
            # prune 即可回收，不留待下一次节流间隔。
            swept_files, swept_size = self._sweep_orphan_part_files(part_files, errors)
            deleted_files += swept_files
            deleted_size += swept_size
            # 空目录回收独立于按天门控，配额驱逐清空的目录当轮即回收，不慢性累积
            # 目录项。
            self._prune_empty_dirs(directories)
        except Exception as e:
            errors.append(f"清理过程失败: {e}")
            logger.error("清理过程失败: {}", e)
        return {"deleted_files": deleted_files, "deleted_size": deleted_size, "errors": errors}

    @staticmethod
    def _sweep_orphan_part_files(
        part_files: list[tuple[Path, int, float]], errors: list[str]
    ) -> tuple[int, int]:
        """删除超龄遗留的 .part 临时文件，返回删除数量与释放字节数。

        常规清理仅收集图片扩展名，进程崩溃或清理失败遗留的 .part 不在其列，不经
        清扫将永久累积。仅删 mtime 早于宽限值的条目，在途下载与写入的临时文件恒
        新于宽限值，并发清理不击杀；.part 亦为常见下载工具的半成品命名，其他工具
        的在途文件同样受宽限保护。候选由 _collect_all_files 在同一遍历中顺带收集，
        遍历防护口径见该函数，防删除动作经 junction 越出 base_dir。
        """
        now = datetime.now().timestamp()
        deleted = 0
        deleted_size = 0
        for file_path, size, mtime in part_files:
            if now - mtime < _PART_SWEEP_GRACE_SECONDS:
                continue
            try:
                file_path.unlink()
                deleted += 1
                deleted_size += size
            except OSError as e:
                errors.append(f"临时文件清理失败: {file_path} -> {e}")
        if deleted:
            logger.info("清扫超龄遗留临时文件 {} 个", deleted)
        return deleted, deleted_size

    def _apply_age_policy(
        self,
        all_files: list[tuple[Path, int, float]],
        days: int,
        errors: list[str],
    ) -> tuple[list[Path], int]:
        """对已扫描文件按保留天数删除过期项，返回已删路径列表与释放字节数。

        cutoff 以 epoch 秒比较 st_mtime，规避本地时区与夏令时跳变导致的清理边界漂移。
        """
        cutoff_epoch = datetime.now().timestamp() - days * 86400
        expired_files = [(p, s, m) for (p, s, m) in all_files if m < cutoff_epoch]
        return self._delete_expired_files(expired_files, errors)

    def _enforce_quota_from_scan(
        self,
        files: list[tuple[Path, int, float]],
        max_total_bytes: int,
        errors: list[str],
    ) -> tuple[int, int]:
        """按总量配额从已扫描文件中驱逐最旧文件，返回删除文件数与累计释放字节数。

        heapq.nsmallest 仅取可能被删的最旧候选：非零字节文件每删一个至少减 1 字节，
        覆盖超额量至多需 excess 个；0 字节文件不减少总量但占最旧位置也可能被删，
        计入上界并封顶为文件总数，避免全量排序。个别 unlink 失败时固定候选窗口可能
        提前耗尽而总量仍超限，记入 errors 由下次节流清理重试。
        """
        total = sum(size for _path, size, _mtime in files)
        if total <= max_total_bytes:
            return 0, 0
        excess = total - max_total_bytes
        zero_byte_files = sum(1 for _p, size, _m in files if size == 0)
        candidate_limit = min(len(files), excess + zero_byte_files)
        deleted_files = 0
        deleted_size = 0
        for file_path, size, _mtime in heapq.nsmallest(
            candidate_limit, files, key=lambda item: item[2]
        ):
            if total <= max_total_bytes:
                break
            try:
                file_path.unlink()
                total -= size
                deleted_files += 1
                deleted_size += size
                logger.info("总量配额驱逐旧文件: {}", file_path)
            except Exception as e:
                errors.append(f"删除文件失败 {file_path}: {e}")
                logger.warning("删除文件失败: {} -> {}", file_path, e)
        return deleted_files, deleted_size

    def _collect_all_files(
        self, errors: list[str]
    ) -> tuple[list[tuple[Path, int, float]], list[tuple[Path, int, float]], list[Path]]:
        """递归遍历保存目录，收集图片文件、.part 遗留候选与待评估的空目录候选。

        以 os.scandir 递归下降，目录与文件条目各经一次 ``entry.stat(follow_symlinks=
        False)`` 同时取得符号链接与 reparse 判定、常规文件校验及 size/mtime，替代
        lstat 后再 stat 跟随的双系统调用。NTFS junction 属 reparse point 且 is_symlink
        返回 False，is_dir(follow_symlinks=False) 对其仍返回 True，下降前剔除 reparse
        并经 resolve 复核真实位置在 base_dir 之内，防止经 junction 误删 base_dir 之外
        的条目；scandir 已下降 junction 的 OS 级 listdir 无法拦截，涉及 SMB 出站认证
        风险，部署方应确保 base_dir 不接受不可信写入。一次扫描产出全部
        (path, size, mtime) 供三项清理共用；.part 候选按条目名后缀收集，不依赖图片
        扩展名过滤。

        残余风险：快照与删除两阶段之间，同名替换的新文件沿用旧 mtime 时会按快照的
        过期判定误删新内容；base_dir 内指向界外文件的硬链接 unlink 只删除界内链接
        自身，同时使界外同一文件的链接计数减一。

        Args:
            errors: 收集 stat 失败的错误描述列表，与删除阶段共享同一列表。

        Returns:
            (all_files, part_files, directories)：all_files 为受支持图片扩展名文件的
            (path, size, mtime) 元组列表，part_files 为 .part 结尾条目的同形元组列表，
            directories 为待评估空目录清理的目录列表，不含 base_dir 自身。
        """
        all_files: list[tuple[Path, int, float]] = []
        part_files: list[tuple[Path, int, float]] = []
        directories: list[Path] = []

        def _scan_directory(root: Path) -> None:
            # 显式栈迭代下降，任意深度不触发递归上限，深层文件不漏出清理视野
            pending: list[Path] = [root]
            while pending:
                directory = pending.pop()
                try:
                    with os.scandir(directory) as iterator:
                        entries = list(iterator)
                except OSError as e:
                    # 目录不可读仅记录警告：计入 errors 会使清理持续判失败而反复退避重试。
                    logger.warning("扫描目录失败: {} -> {}", directory, e)
                    continue
                for entry in entries:
                    entry_path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        # 单次 no-follow stat 同时判定符号链接与 reparse 属性；条目
                        # 消失属正常轮替，跳过不下降。
                        try:
                            dir_stat = entry.stat(follow_symlinks=False)
                        except OSError as e:
                            logger.warning("获取目录信息失败: {} -> {}", entry_path, e)
                            continue
                        if stat.S_ISLNK(dir_stat.st_mode):
                            continue
                        if has_reparse_attribute(dir_stat):
                            logger.warning("跳过 reparse point 目录: {}", entry_path)
                            continue
                        try:
                            dir_resolved = entry_path.resolve()
                        except Exception as e:
                            logger.warning("路径验证失败: {} -> {}", entry_path, e)
                            continue
                        if not self._resolved_within_base(dir_resolved):
                            logger.warning("路径不在保存目录内: {}", dir_resolved)
                            continue
                        directories.append(entry_path)
                        pending.append(entry_path)
                        continue
                    # 仅收集受支持图片与 .part 临时文件，跳过其他类型避免误删用户数据。
                    is_image = entry_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                    if not is_image and not entry.name.endswith(".part"):
                        continue
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except OSError as e:
                        if not is_image:
                            # .part 条目被原子重命名消失属正常轮替，不计错误以免回滚节流。
                            continue
                        errors.append(f"获取文件信息失败 {entry_path}: {e}")
                        logger.warning("获取文件信息失败: {} -> {}", entry_path, e)
                        continue
                    # 符号链接与 reparse 文件目标可能在 base_dir 之外，no-follow stat 不
                    # 跟随，命中即跳过；FIFO、套接字等非常规文件同样不纳入清理。
                    if stat.S_ISLNK(entry_stat.st_mode):
                        continue
                    if has_reparse_attribute(entry_stat):
                        logger.warning("跳过 reparse point 文件: {}", entry_path)
                        continue
                    if not stat.S_ISREG(entry_stat.st_mode):
                        continue
                    collected = (entry_path, entry_stat.st_size, entry_stat.st_mtime)
                    if is_image:
                        all_files.append(collected)
                    else:
                        part_files.append(collected)

        # 扫描根复核真实位置后下降，防止 base_dir 自身被替换为指向外部的符号链接。
        try:
            root_resolved = self.base_dir.resolve()
        except Exception as e:
            logger.warning("路径验证失败: {} -> {}", self.base_dir, e)
            return all_files, part_files, directories
        if not self._resolved_within_base(root_resolved):
            logger.warning("路径不在保存目录内: {}", root_resolved)
            return all_files, part_files, directories
        _scan_directory(self.base_dir)
        if len(all_files) > _CLEANUP_ENTRY_WARN_THRESHOLD:
            logger.warning(
                "清理扫描图片条目数 {} 超过阈值 {}，保存目录规模异常增长",
                len(all_files),
                _CLEANUP_ENTRY_WARN_THRESHOLD,
            )
        return all_files, part_files, directories

    @staticmethod
    def _delete_expired_files(
        expired_files: list[tuple[Path, int, float]], errors: list[str]
    ) -> tuple[list[Path], int]:
        """删除收集到的过期文件，返回已删路径列表与累计释放字节数。

        stat 与 unlink 拆分到收集与删除两阶段：stat 失败已在收集阶段记录，此处仅
        处理 unlink 失败，错误累积到共享的 errors 列表以保持错误消息一致。
        """
        deleted_files: list[Path] = []
        deleted_size = 0
        for file_path, size, _mtime in expired_files:
            try:
                file_path.unlink()
                deleted_files.append(file_path)
                deleted_size += size
                logger.info("删除旧文件: {}", file_path)
            except Exception as e:
                errors.append(f"删除文件失败 {file_path}: {e}")
                logger.warning("删除文件失败: {} -> {}", file_path, e)
        return deleted_files, deleted_size

    @staticmethod
    def _prune_empty_dirs(directories: list[Path]) -> None:
        """按深度逆序删除已变空的子目录，使父目录在子目录删除后变空而级联得到清理。

        目录删除失败仅记录警告，不计入 errors 列表。
        """
        for dir_path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    logger.info("删除空目录: {}", dir_path)
            except Exception as e:
                logger.warning("删除目录失败: {} -> {}", dir_path, e)


# FileManager 进程级缓存：构造含 resolve/exist/mkdir 文件系统调用，按 base_dir
# 原始值（调用方均传已解析的绝对路径）复用避免每次保存重复探测；LRU 上限封顶
# 多会话 roots 下的条目增长，竞态下最坏重复构造一次。
_FILE_MANAGER_CACHE_MAX_ENTRIES = 16
_file_manager_cache: "OrderedDict[Path, FileManager]" = OrderedDict()


def get_file_manager(base_dir: Path | None = None) -> "FileManager":
    """按 base_dir 取进程级缓存的 FileManager，未命中时构造并缓存，LRU 驱逐最旧。"""
    cache_key = resolve_images_root() if base_dir is None else Path(base_dir)
    cached = _file_manager_cache.get(cache_key)
    if cached is not None:
        _file_manager_cache.move_to_end(cache_key)
        return cached
    manager = FileManager(base_dir)
    _file_manager_cache[cache_key] = manager
    while len(_file_manager_cache) > _FILE_MANAGER_CACHE_MAX_ENTRIES:
        _file_manager_cache.popitem(last=False)
    return manager


def reset_file_manager_cache() -> None:
    """清空 FileManager 进程级缓存，仅供测试隔离调用。"""
    _file_manager_cache.clear()
