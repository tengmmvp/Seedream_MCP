"""文件管理模块：生成图片保存路径、写入字节内容并清理旧文件。

负责按日期与工具名组织保存路径、净化文件名、用内容哈希做去重、基于字节签名
嗅探真实扩展名，以及按保留天数清理旧文件。落盘写入与旧文件遍历均通过 io_file
防符号链接，避免经由符号链接逃逸出基础目录。
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.errors import SeedreamMCPError
from ..core.formats import (
    DEFAULT_IMAGE_EXTENSION,
    SUPPORTED_IMAGE_EXTENSIONS,
    infer_extension_from_bytes,
)
from ..core.logs import get_logger
from .io_file import (
    _is_reparse_point,
    atomic_replace_from_fd_sync,
)
from .io_path import _is_within_resolved
from .io_url import get_file_extension_from_url

logger = get_logger(__name__)

# 文件名长度上限，避免超出常见文件系统目录项长度限制
_MAX_FILENAME_LENGTH = 200

# Windows 保留设备名，命中时在词干后追加下划线避免被解释为设备而非文件
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)


class FileManagerError(SeedreamMCPError):
    """文件管理相关操作失败时抛出的异常。"""

    pass


class FileManager:
    """图片保存路径生成、字节写入与旧文件清理的统一入口。"""

    def __init__(self, base_dir: Path | None = None):
        """初始化文件管理器并确保基础目录存在。

        Args:
            base_dir: 图片保存基础目录。默认为当前工作目录下的 images 文件夹。
        """
        raw_base = Path.cwd() / "images" if base_dir is None else Path(base_dir)
        try:
            resolved = raw_base.resolve()
        except (OSError, ValueError) as e:
            raise FileManagerError(f"解析保存路径时出错: {e}") from e
        # 仅拒绝指向已存在文件的路径。目录越界防护不在本类职责内，
        # 由调用方 tools/core/common._resolve_base_dir 经 is_path_within_base 校验。
        if resolved.exists() and not resolved.is_dir():
            raise FileManagerError(f"保存路径不是目录: {resolved}")
        base_dir = resolved

        self.base_dir = base_dir
        self.ensure_directory(self.base_dir)
        # 缓存 resolved 形式供 validate_path 等热路径复用；base_dir 已 resolve，无需重复解析。
        self._base_abs = self.base_dir

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
        except OSError as e:
            raise FileManagerError(f"创建目录失败: {path} -> {e}") from e

    def validate_path(self, path: Path) -> bool:
        """验证路径是否在基础目录范围内。

        复用 io_path._is_within_resolved 做 resolve 后的包含判定，可拦截包含 ``..``
        或经由符号链接指向基础目录之外的路径。

        Args:
            path: 要验证的路径。

        Returns:
            路径在基础目录范围内返回 True，否则返回 False。
        """
        try:
            abs_path = path.resolve()
            if _is_within_resolved(abs_path, self._base_abs):
                return True
            logger.warning("路径不在基础目录内: {}", abs_path)
            return False
        except Exception as e:
            logger.warning("路径验证失败: {} -> {}", path, e)
            return False

    def _resolved_within_base(self, resolved_path: Path) -> bool:
        """判断已 resolve 的路径是否位于基础目录内，直接比较，不再 resolve。

        复用 io_path._is_within_resolved 的单一实现，避免两处包含判定逻辑分叉漂移。
        供 cleanup_old_files 等热路径复用，避免对已 resolve 路径重复解析。
        """
        return _is_within_resolved(resolved_path, self._base_abs)

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除文件系统不安全字符并规避 Windows 保留设备名。

        Args:
            filename: 原始文件名。

        Returns:
            清理后仅含安全字符的文件名。
        """
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename)

        # 限制文件名长度避免超出文件系统上限。
        # max 兜底：扩展名本身超长时差值为负，负索引会从尾部误截，故下限取 0。
        if len(filename) > _MAX_FILENAME_LENGTH:
            name, ext = os.path.splitext(filename)
            filename = name[: max(0, _MAX_FILENAME_LENGTH - len(ext))] + ext

        # Windows 保留设备名处理：CON.txt、NUL 等在 Windows 上会被解释为设备而非文件，
        # 命中时在首个点前追加下划线使词干不再匹配保留名
        # Windows 解析设备名前会剥离前导点与首尾空格，按同样规则归一化词干再判断。
        # 直接 split(".",1) 对前导点输入（如 .CON）会使首段为空而漏检，故先 lstrip 再取词干。
        normalized_stem = filename.lstrip(". ").split(".", 1)[0].strip(". ")
        if normalized_stem.upper() in _WINDOWS_RESERVED_NAMES:
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
            base_name: 基础名称。
            extension: 文件扩展名，包含点号。
            content_hash: 内容哈希值，提供则取其前 8 位嵌入文件名。
            timestamp: 时间戳，默认取当前时间。

        Returns:
            唯一文件名。
        """
        if timestamp is None:
            timestamp = datetime.now()

        clean_base = self.sanitize_filename(base_name)

        # [:-3] 截掉微秒末三位，得到毫秒精度时间戳
        time_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]

        unique_suffix = uuid.uuid4().hex[:8]

        # 拼接文件名：优先嵌入内容哈希，否则用随机后缀确保不冲突。
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

    def infer_extension_from_bytes(
        self, content: bytes, default: str = DEFAULT_IMAGE_EXTENSION
    ) -> str:
        """基于文件头魔法字节嗅探真实图片类型并返回扩展名。

        读取字节头部 magic bytes 判断真实格式，不信任 URL 或路径声明的扩展名，
        避免伪造后缀的文件落盘。委托 formats 模块的统一实现。

        Args:
            content: 图片字节内容。
            default: 无法识别时返回的默认扩展名，含点号。

        Returns:
            推断出的扩展名，含点号。
        """
        return infer_extension_from_bytes(content, default)

    def get_organized_path(
        self, filename: str, subfolder: str | None = None, date_folder: bool = True
    ) -> Path:
        """在基础目录下按日期与子目录组织文件路径。

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

        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)

        if not self.validate_path(save_path):
            raise FileManagerError(f"路径不安全: {save_path}")

        return save_path

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
            extension: 文件扩展名，包含点号。
            tool_name: 工具名称，用作保存子目录。
            custom_name: 自定义文件名基础部分，覆盖提示词派生。
            content_hash: 内容哈希，嵌入文件名用于去重与标识。
            date_folder: 是否按日期创建一级子目录。

        Returns:
            保存路径。
        """
        base_name = custom_name or self.generate_name_from_prompt(prompt)
        filename = self.generate_unique_filename(base_name, extension, content_hash=content_hash)
        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)
        if not self.validate_path(save_path):
            raise FileManagerError(f"路径不安全: {save_path}")
        return save_path

    def save_bytes(
        self, file_path: Path, data: bytes, overwrite: bool = False, ensure_parent: bool = True
    ) -> dict[str, Any]:
        """将字节数据写入文件，返回保存结果元数据。

        Args:
            file_path: 目标路径。
            data: 字节数据。
            overwrite: 是否覆盖已有文件。
            ensure_parent: 是否确保父目录存在；调用方已建目录时可传 False 跳过重复 mkdir。

        Returns:
            保存结果元数据，包含最终路径、大小与保存时间。
        """
        try:
            # 默认确保父目录存在；批量保存入口或上游已建目录时可由调用方关闭。
            if ensure_parent:
                self.ensure_directory(file_path.parent)
            # 不允许覆盖时，若文件已存在则追加内容短哈希生成不冲突的新文件名。
            final_path = file_path
            if final_path.exists() and not overwrite:
                base = final_path.stem
                ext = final_path.suffix
                short_hash = self.get_content_hash(data)[:8]
                final_path = final_path.with_name(f"{base}_{short_hash}{ext}")

            # 原子落盘协议由 io_file.atomic_replace_from_fd_sync 同步提供，与 io_download
            # 下载路径的异步骨架对应同一协议：随机名临时文件规避符号链接 TOCTOU，写入后
            # os.replace 原子替换，失败清理临时文件。writer 以 closefd=False 包装 fd，骨架
            # 独占 fd 关闭。save_bytes 为同步公共接口且数据已在内存，走同步落盘路径避免在
            # 事件循环内 asyncio.run 驱动异步骨架的分层倒置与 RuntimeError 风险。
            def _writer(fd: int) -> None:
                with os.fdopen(fd, "wb", closefd=False) as f:
                    f.write(data)

            atomic_replace_from_fd_sync(final_path, _writer, suffix=".part")
            return {
                "file_path": str(final_path),
                "file_size": len(data),
                "save_time": datetime.now(timezone.utc).isoformat(),
            }
        except OSError as e:
            raise FileManagerError(f"写入文件失败: {file_path} -> {e}") from e

    def relative_to_base(self, file_path: Path) -> str:
        """获取文件相对于基础目录的路径。

        Args:
            file_path: 文件路径。

        Returns:
            相对路径字符串；不在基础目录内则返回绝对路径。
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
        # 以基础目录为基准生成相对路径：保存文件恒位于 base_dir 之下，相对化必然成功，
        # 且不受进程 CWD 变化影响。统一为正斜杠以兼容 Markdown 引用。
        relative_path = self.relative_to_base(file_path)
        markdown_path = relative_path.replace("\\", "/")

        if not markdown_path.startswith("./"):
            markdown_path = "./" + markdown_path

        if alt_text:
            return f"![{alt_text}]({markdown_path})"
        else:
            return f"![]({markdown_path})"

    def run_cleanup_policies(self, days: int, max_total_bytes: int | None) -> dict[str, Any]:
        """单次目录扫描依次执行按天清理与总量配额驱逐，供节流清理入口复用。

        共享一次遍历结果执行两策略，避免重复全目录 os.walk。按天策略经 _apply_age_policy
        实现并删除空目录；配额驱逐基于按天清理后的
        剩余文件计算，剔除已删条目避免对已删路径重复 unlink。days 小于 1 跳过按天清理，
        max_total_bytes 为 None 跳过配额驱逐。

        Returns:
            合并的清理结果，包含两策略累计的删除文件数、释放字节数与错误列表。
        """
        errors: list[str] = []
        deleted_files = 0
        deleted_size = 0
        try:
            all_files, directories = self._collect_all_files(errors)
            remaining_files = all_files
            if days >= 1:
                deleted_names, age_deleted_size = self._apply_age_policy(all_files, days, errors)
                self._prune_empty_dirs(directories)
                deleted_files += len(deleted_names)
                deleted_size += age_deleted_size
                if deleted_names:
                    deleted_set = set(deleted_names)
                    remaining_files = [
                        item for item in all_files if str(item[0]) not in deleted_set
                    ]
            if max_total_bytes is not None:
                quota_deleted, quota_deleted_size = self._enforce_quota_from_scan(
                    remaining_files, max_total_bytes, errors
                )
                deleted_files += quota_deleted
                deleted_size += quota_deleted_size
        except Exception as e:
            errors.append(f"清理过程出错: {e}")
            logger.error("清理过程出错: {}", e)
        return {"deleted_files": deleted_files, "deleted_size": deleted_size, "errors": errors}

    def _apply_age_policy(
        self,
        all_files: list[tuple[Path, int, float]],
        days: int,
        errors: list[str],
    ) -> tuple[list[str], int]:
        """对已扫描文件按保留天数删除过期项，返回已删路径名列表与释放字节数。

        cutoff 以 epoch 秒比较 st_mtime，规避本地时区与夏令时跳变导致的清理边界漂移。
        供 cleanup_old_files 与 run_cleanup_policies 共用，消除两处 cutoff 计算与过期
        过滤的重复实现；调用方负责 days 小于 1 的跳过判定。
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

        heapq.nsmallest 仅取可能被删的最旧候选：非零字节文件每删一个至少减少 1 字节，
        覆盖超额量至多需 excess 个；0 字节文件不减少总量但占据最旧位置也可能被删，计入
        候选上界，最终封顶为文件总数，避免对全量文件排序。nsmallest 与 sorted 同为稳定
        排序，逐候选删除至总量达标的语义在删除全成功时与原 sorted 实现等价；个别 unlink
        失败时固定候选窗口可能提前耗尽而总量仍超限，失败记入 errors 由下次节流清理重试，
        驱逐力在极端错误路径下弱于原全量遍历。
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
    ) -> tuple[list[tuple[Path, int, float]], list[Path]]:
        """遍历基础目录，收集全部普通文件与待评估的空目录候选。

        os.walk(followlinks=False) 不下降进入符号链接目录。Windows NTFS junction 属
        reparse point 但 is_symlink 返回 False，followlinks 无法拦截，仍会被下降进入
        junction 目标，下降后 root 解析到 base_dir 之外。每层先经 _resolved_within_base
        复核 root 真实位置，越界则跳过该层的目录与文件处理，防止经 junction 误删 base_dir
        之外的条目造成数据破坏。os.walk 下降 junction 时已发生的 OS 级 listdir 无法在此
        拦截，涉及 NTLM/SMB 出站认证风险，部署方应确保 base_dir 不接受不可信写入。

        一次扫描产出全部 (path, size, mtime) 供按天清理与总量配额两策略共用，按天策略
        在调用方按 cutoff 过滤，避免两策略各自全目录遍历。

        Args:
            errors: 收集 stat 失败的错误描述列表，与删除阶段共享同一列表。

        Returns:
            (all_files, directories)：all_files 为 (path, size, mtime) 元组列表，
            directories 为待评估空目录清理的目录列表，不含 base_dir 自身。
        """
        all_files: list[tuple[Path, int, float]] = []
        directories: list[Path] = []
        for root, dirs, files in os.walk(self.base_dir, followlinks=False):
            root_path = Path(root)
            # root 字符串经 os.walk 从已 resolve 的 base_dir 拼接而来，仍可能因 NTFS
            # junction 被下降到 base_dir 之外；resolve 一次复核真实位置。
            try:
                root_resolved = root_path.resolve()
            except Exception as e:
                logger.warning("路径验证失败: {} -> {}", root_path, e)
                dirs[:] = []
                continue
            if not self._resolved_within_base(root_resolved):
                # 越界：清空 dirs 阻止 os.walk 继续下降到越界子目录，含 NTFS junction
                # 目标，避免无谓的越界遍历与潜在 SMB 出站认证暴露
                logger.warning("路径不在基础目录内: {}", root_resolved)
                dirs[:] = []
                continue
            # 下降前剔除符号链接、NTFS junction 与越界目录。os.walk(followlinks=False)
            # 不拦截 junction，因其属 reparse point 而 is_symlink 返回 False；若不在此
            # 剔除会下降进入 junction 或越界目录目标执行 OS 级 listdir，涉及潜在 SMB
            # 出站认证暴露。dirs[:] 原地赋值阻止 os.walk 继续下降到被剔除的目录。
            pruned_dirs: list[str] = []
            for name in dirs:
                dir_path = root_path / name
                if dir_path.is_symlink():
                    continue
                if _is_reparse_point(dir_path):
                    logger.warning("跳过 reparse point 目录: {}", dir_path)
                    continue
                try:
                    dir_resolved = dir_path.resolve()
                except Exception as e:
                    logger.warning("路径验证失败: {} -> {}", dir_path, e)
                    continue
                if not self._resolved_within_base(dir_resolved):
                    logger.warning("路径不在基础目录内: {}", dir_resolved)
                    continue
                directories.append(dir_path)
                pruned_dirs.append(name)
            dirs[:] = pruned_dirs
            for name in files:
                file_path = root_path / name
                # 仅收集本服务支持的图片文件，跳过 base_dir 内其他类型文件，避免误删用户数据
                if file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                    continue
                # 跳过符号链接文件，其目标可能在 base_dir 之外。
                if file_path.is_symlink():
                    continue
                # 与目录分支对称：reparse point 文件会被 stat 跟随，命中则跳过。
                if _is_reparse_point(file_path):
                    logger.warning("跳过 reparse point 文件: {}", file_path)
                    continue
                try:
                    stat_result = file_path.stat()
                    if not stat.S_ISREG(stat_result.st_mode):
                        continue
                    all_files.append((file_path, stat_result.st_size, stat_result.st_mtime))
                except Exception as e:
                    errors.append(f"获取文件信息失败 {file_path}: {e}")
                    logger.warning("获取文件信息失败: {} -> {}", file_path, e)
        return all_files, directories

    @staticmethod
    def _delete_expired_files(
        expired_files: list[tuple[Path, int, float]], errors: list[str]
    ) -> tuple[list[str], int]:
        """删除收集到的过期文件，返回已删路径列表与累计释放字节数。

        stat 与 unlink 拆分到收集与删除两阶段：stat 失败已在收集阶段记录，此处仅
        处理 unlink 失败，错误累积到共享的 errors 列表以保持错误消息一致。
        """
        deleted_files: list[str] = []
        deleted_size = 0
        for file_path, size, _mtime in expired_files:
            try:
                file_path.unlink()
                deleted_files.append(str(file_path))
                deleted_size += size
                logger.info("删除旧文件: {}", file_path)
            except Exception as e:
                errors.append(f"删除文件失败 {file_path}: {e}")
                logger.warning("删除文件失败: {} -> {}", file_path, e)
        return deleted_files, deleted_size

    @staticmethod
    def _prune_empty_dirs(directories: list[Path]) -> None:
        """按深度逆序删除已变空的子目录，先删深层再删浅层。

        目录删除失败仅记录警告不计入 errors 列表，与历史行为一致。
        """
        # 按目录深度逆序排序，先删深层空目录再删浅层，使父目录在子目录删除后变空而被清理。
        for dir_path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            try:
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    logger.info("删除空目录: {}", dir_path)
            except Exception as e:
                logger.warning("删除目录失败: {} -> {}", dir_path, e)
