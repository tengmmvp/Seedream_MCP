"""文件管理模块：生成图片保存路径、写入字节内容并清理旧文件。

负责按日期与工具名组织保存路径、净化文件名、用内容哈希做去重、基于字节签名
嗅探真实扩展名，以及按保留天数清理旧文件。落盘写入与旧文件遍历均通过 os_utils
防符号链接，避免经由符号链接逃逸出基础目录。
"""

import hashlib
import os
import re
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from .errors import SeedreamMCPError
from .formats import SUPPORTED_IMAGE_EXTENSIONS
from .logging import get_logger
from .os_utils import open_no_follow_write

logger = get_logger(__name__)


class FileManagerError(SeedreamMCPError):
    """文件管理相关操作失败时抛出的异常。"""

    pass


class FileManager:
    """图片保存路径生成、字节写入与旧文件清理的统一入口。"""

    def __init__(self, base_dir: Optional[Path] = None):
        """初始化文件管理器并确保基础目录存在。

        Args:
            base_dir: 图片保存基础目录。默认为当前工作目录下的 images 文件夹。
        """
        if base_dir is None:
            base_dir = Path.cwd() / "images"
        else:
            try:
                resolved = Path(base_dir).resolve()
            except (OSError, ValueError) as e:
                raise FileManagerError(f"解析保存路径时出错: {e}") from e
            # 仅拒绝指向已存在文件的路径。目录越界防护不在本类职责内，
            # 由调用方 tools/core/common._resolve_base_dir 经 is_path_within_base 校验。
            if resolved.exists() and not resolved.is_dir():
                raise FileManagerError(f"保存路径不是目录: {resolved}")
            base_dir = resolved

        self.base_dir = base_dir
        self.ensure_directory(self.base_dir)
        # 缓存创建时的工作目录，供 generate_markdown_reference 批量复用。
        self._cwd = Path.cwd()
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

        通过 resolve 后做 relative_to 比较判定，可拦截包含 ``..`` 或经由符号链接
        指向基础目录之外的路径。

        Args:
            path: 要验证的路径。

        Returns:
            路径在基础目录范围内返回 True，否则返回 False。
        """
        try:
            abs_path = path.resolve()
            base_abs = self._base_abs

            try:
                abs_path.relative_to(base_abs)
                return True
            except ValueError:
                logger.warning("路径不在基础目录内: {}", abs_path)
                return False

        except Exception as e:
            logger.warning("路径验证失败: {} -> {}", path, e)
            return False

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除文件系统不安全字符。

        Args:
            filename: 原始文件名。

        Returns:
            清理后仅含安全字符的文件名。
        """
        # 替换文件系统保留字符为下划线。
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

        # 剔除控制字符。
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename)

        # 限制文件名长度避免超出文件系统上限。
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[: 200 - len(ext)] + ext

        # 净化后为空则回退默认名。
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

        # 移除特殊字符，仅保留字母、数字、空格与连字符。
        clean_prompt = re.sub(r"[^\w\s-]", "", prompt)

        # 空白字符折叠为下划线。
        clean_prompt = re.sub(r"\s+", "_", clean_prompt)

        # 截断到最大长度。
        if len(clean_prompt) > max_length:
            clean_prompt = clean_prompt[:max_length]

        # 去除首尾下划线。
        clean_prompt = clean_prompt.strip("_")

        # 结果为空则回退默认名。
        if not clean_prompt:
            clean_prompt = "image"

        return clean_prompt.lower()

    def generate_unique_filename(
        self,
        base_name: str,
        extension: str,
        content_hash: Optional[str] = None,
        timestamp: Optional[datetime] = None,
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

        # 清理基础名称。
        clean_base = self.sanitize_filename(base_name)

        # 生成含毫秒的时间戳字符串。
        time_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]

        # 生成 4 位随机唯一性后缀。
        unique_suffix = uuid.uuid4().hex[:4]

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

    def infer_extension_from_bytes(self, content: bytes, default: str = ".jpeg") -> str:
        """基于文件头魔法字节嗅探真实图片类型并返回扩展名。

        读取字节头部 magic bytes 判断真实格式，不信任 URL 或路径声明的扩展名，
        避免伪造后缀的文件落盘。委托 formats 模块的统一实现。

        Args:
            content: 图片字节内容。
            default: 无法识别时返回的默认扩展名，含点号。

        Returns:
            推断出的扩展名，含点号。
        """
        from .formats import infer_extension_from_bytes

        return infer_extension_from_bytes(content, default)

    def get_organized_path(
        self, filename: str, subfolder: Optional[str] = None, date_folder: bool = True
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

        # 按当前日期创建一级子目录。
        if date_folder:
            today = datetime.now().strftime("%Y-%m-%d")
            path = path / today

        # 追加工具名等子目录。
        if subfolder:
            clean_subfolder = self.sanitize_filename(subfolder)
            path = path / clean_subfolder

        # 确保目录存在。
        self.ensure_directory(path)

        return path / filename

    def create_save_path(
        self,
        prompt: str,
        url: str,
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
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
        # 确定文件名基础部分：优先自定义名，否则由提示词派生。
        if custom_name:
            base_name = custom_name
        else:
            base_name = self.generate_name_from_prompt(prompt)

        # 从 URL 路径推断扩展名；调用独立工具函数以避免实例化 DownloadManager。
        from .url_utils import get_file_extension_from_url

        extension = get_file_extension_from_url(url)
        # 收敛到受支持图片扩展名白名单，防止 URL 派生的 .html/.aspx 等任意后缀落盘。
        if extension not in SUPPORTED_IMAGE_EXTENSIONS:
            extension = ".jpeg"

        # 生成唯一文件名。
        filename = self.generate_unique_filename(base_name, extension)

        # 组织到日期与工具名子目录下。
        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)

        # 校验路径未越出基础目录。
        if not self.validate_path(save_path):
            raise FileManagerError(f"路径不安全: {save_path}")

        return save_path

    def create_save_path_from_extension(
        self,
        prompt: str,
        extension: str,
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
        content_hash: Optional[str] = None,
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
    ) -> Dict[str, Any]:
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
            # O_NOFOLLOW 防护最终路径分量、拒绝符号链接，由 os_utils 统一实现；
            # 符号链接或打开失败抛 OSError，由下方 except 转 FileManagerError。
            with open_no_follow_write(final_path) as f:
                f.write(data)
            return {
                "file_path": str(final_path),
                "file_size": len(data),
                "save_time": datetime.now().isoformat(),
            }
        except OSError as e:
            raise FileManagerError(f"写入文件失败: {file_path} -> {e}") from e

    def get_relative_path(self, file_path: Path) -> str:
        """获取文件相对于基础目录的路径。

        Args:
            file_path: 文件路径。

        Returns:
            相对路径字符串；不在基础目录内则返回绝对路径。
        """
        try:
            return str(file_path.relative_to(self.base_dir))
        except ValueError:
            # 不在基础目录内时回退绝对路径。
            return str(file_path)

    def generate_markdown_reference(self, file_path: Path, alt_text: str = "") -> str:
        """生成 Markdown 图片引用。

        Args:
            file_path: 文件路径。
            alt_text: 替代文本。

        Returns:
            Markdown 引用字符串。
        """
        # 优先取相对当前工作目录的路径，便于在日志或文档中直接引用。
        try:
            cwd = self._cwd
            relative_path = str(file_path.relative_to(cwd))
        except ValueError:
            # 文件不在当前工作目录下时，回退到相对基础目录的路径并前缀基础目录名。
            relative_path = self.get_relative_path(file_path)
            base_dir_name = self.base_dir.name
            relative_path = f"{base_dir_name}/{relative_path}"

        # 统一为正斜杠以兼容 Markdown 引用。
        markdown_path = relative_path.replace("\\", "/")

        # 补齐相对路径前缀。
        if not markdown_path.startswith("./"):
            markdown_path = "./" + markdown_path

        if alt_text:
            return f"![{alt_text}]({markdown_path})"
        else:
            return f"![]({markdown_path})"

    def cleanup_old_files(self, days: int = 30) -> Dict[str, Any]:
        """清理超过保留天数的旧文件并删除随之变空的子目录。

        Args:
            days: 文件保留天数，默认 30。

        Returns:
            清理结果，包含删除文件数、释放字节数与错误列表。
        """
        # 以 epoch 秒比较 st_mtime，规避 datetime.fromtimestamp 受本地时区与夏令时
        # 跳变影响导致的清理边界漂移。
        cutoff_epoch = datetime.now().timestamp() - days * 86400
        deleted_files = []
        deleted_size = 0
        errors = []

        try:
            directories: List[Path] = []
            # os.walk(followlinks=False) 不下降进入符号链接目录。Windows NTFS junction 属
            # reparse point 但 is_symlink 返回 False，followlinks 无法拦截，仍会被下降进入
            # junction 目标，下降后 root 解析到 base_dir 之外。每层先经 validate_path 复核
            # root 真实位置，越界则跳过该层的目录与文件处理，防止经 junction 误删 base_dir
            # 之外的条目造成数据破坏。os.walk 下降 junction 时已发生的 OS 级 listdir 无法在此
            # 拦截，涉及 NTLM/SMB 出站认证风险，部署方应确保 base_dir 不接受不可信写入。
            for root, dirs, files in os.walk(self.base_dir, followlinks=False):
                root_path = Path(root)
                if not self.validate_path(root_path):
                    continue
                for name in dirs:
                    dir_path = root_path / name
                    # 跳过符号链接目录，不纳入空目录清理，避免对其目标操作。
                    if dir_path.is_symlink():
                        continue
                    # junction 目录 resolve 后落在 base_dir 之外，不纳入空目录清理，
                    # 避免 rmdir 误伤 junction 目标。
                    if not self.validate_path(dir_path):
                        continue
                    if dir_path != self.base_dir:
                        directories.append(dir_path)
                for name in files:
                    file_path = root_path / name
                    # 跳过符号链接文件，其目标可能在 base_dir 之外。
                    if file_path.is_symlink():
                        continue
                    try:
                        stat_result = file_path.stat()
                        if not stat.S_ISREG(stat_result.st_mode):
                            continue
                        if stat_result.st_mtime < cutoff_epoch:
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                            deleted_size += stat_result.st_size
                            logger.info("删除旧文件: {}", file_path)
                    except Exception as e:
                        errors.append(f"删除文件失败 {file_path}: {e}")
                        logger.warning("删除文件失败: {} -> {}", file_path, e)

            # 按深度逆序排序，先删深层空目录再删浅层。
            for dir_path in sorted(directories, reverse=True):
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                        logger.info("删除空目录: {}", dir_path)
                except Exception as e:
                    logger.warning("删除目录失败: {} -> {}", dir_path, e)

        except Exception as e:
            errors.append(f"清理过程出错: {e}")
            logger.error("清理过程出错: {}", e)

        return {"deleted_files": len(deleted_files), "deleted_size": deleted_size, "errors": errors}
