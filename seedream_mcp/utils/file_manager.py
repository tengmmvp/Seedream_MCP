"""
文件管理模块
实现智能文件命名、目录管理、路径安全检查
"""

import hashlib
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class FileManagerError(Exception):
    """文件管理错误异常"""

    pass


class FileManager:
    """文件管理器"""

    def __init__(self, base_dir: Optional[Path] = None):
        """
        初始化文件管理器

        Args:
            base_dir: 基础目录,默认为当前工作目录下的images文件夹
        """
        if base_dir is None:
            base_dir = Path.cwd() / "images"
        else:
            # 验证用户提供的路径
            try:
                base_dir = Path(base_dir).resolve()
                # 基本安全检查
                if self._is_unsafe_path(base_dir):
                    logger.warning(f"提供的保存路径不安全: {base_dir}，使用默认路径")
                    base_dir = Path.cwd() / "images"
            except (OSError, ValueError) as e:
                logger.warning(f"解析保存路径时出错: {e}，使用默认路径")
                base_dir = Path.cwd() / "images"

        self.base_dir = base_dir
        self.ensure_directory(self.base_dir)

    def ensure_directory(self, path: Path) -> None:
        """
        确保目录存在

        Args:
            path: 目录路径

        Raises:
            FileManagerError: 创建目录失败时抛出
        """
        try:
            path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"确保目录存在: {path}")
        except OSError as e:
            raise FileManagerError(f"创建目录失败: {path} -> {e}")

    def _is_unsafe_path(self, path: Path) -> bool:
        """
        检查路径是否不安全

        Args:
            path: 要检查的路径

        Returns:
            路径是否不安全
        """
        try:
            # 检查是否包含危险的路径遍历
            path_str = str(path)
            if ".." in path.parts or path_str.startswith("\\\\") or ":" in path.name:
                return True

            # 检查路径长度
            if len(str(path)) > 260:  # Windows路径长度限制
                return True

            return False
        except Exception:
            return True

    def validate_path(self, path: Path) -> bool:
        """
        验证路径是否在基础目录范围内

        Args:
            path: 要验证的路径

        Returns:
            路径是否在基础目录范围内
        """
        try:
            # 解析绝对路径
            abs_path = path.resolve()
            base_abs = self.base_dir.resolve()

            # 检查路径是否在基础目录内（沙盒验证）
            try:
                abs_path.relative_to(base_abs)
                return True
            except ValueError:
                logger.warning(f"路径不在基础目录内: {abs_path}")
                return False

        except Exception as e:
            logger.warning(f"路径验证失败: {path} -> {e}")
            return False

    def sanitize_filename(self, filename: str) -> str:
        """
        清理文件名，移除不安全字符

        Args:
            filename: 原始文件名

        Returns:
            清理后的文件名
        """
        # 移除或替换不安全字符
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

        # 移除控制字符
        filename = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename)

        # 限制长度
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[: 200 - len(ext)] + ext

        # 确保不为空
        if not filename.strip():
            filename = "unnamed"

        return filename.strip()

    def generate_name_from_prompt(self, prompt: str, max_length: int = 50) -> str:
        """
        从提示词生成文件名

        Args:
            prompt: 提示词
            max_length: 最大长度

        Returns:
            生成的文件名
        """
        if not prompt:
            return "image"

        # 移除特殊字符，保留字母数字和空格
        clean_prompt = re.sub(r"[^\w\s-]", "", prompt)

        # 替换空格为下划线
        clean_prompt = re.sub(r"\s+", "_", clean_prompt)

        # 限制长度
        if len(clean_prompt) > max_length:
            clean_prompt = clean_prompt[:max_length]

        # 移除首尾下划线
        clean_prompt = clean_prompt.strip("_")

        # 确保不为空
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
        """
        生成唯一文件名

        Args:
            base_name: 基础名称
            extension: 文件扩展名（包含点号）
            content_hash: 内容哈希值
            timestamp: 时间戳

        Returns:
            唯一文件名
        """
        if timestamp is None:
            timestamp = datetime.now()

        # 清理基础名称
        clean_base = self.sanitize_filename(base_name)

        # 生成时间戳字符串（包含毫秒）
        time_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]

        # 生成随机数
        random_suffix = f"{random.randint(1000, 9999)}"

        # 构建文件名
        if content_hash:
            # 使用内容哈希的前8位
            hash_part = content_hash[:8]
            filename = f"{clean_base}_{time_str}_{hash_part}{extension}"
        else:
            # 添加随机数后缀确保唯一性
            filename = f"{clean_base}_{time_str}_{random_suffix}{extension}"

        return filename

    def get_content_hash(self, content: bytes) -> str:
        """
        计算内容哈希值

        Args:
            content: 文件内容

        Returns:
            SHA256哈希值
        """
        return hashlib.sha256(content).hexdigest()

    def infer_extension_from_bytes(self, content: bytes, default: str = ".jpg") -> str:
        """
        基于文件头推断图片扩展名

        Args:
            content: 图片字节内容
            default: 默认扩展名

        Returns:
            扩展名（包含点号）
        """
        try:
            # PNG
            if content.startswith(b"\x89PNG\r\n\x1a\n"):
                return ".png"
            # JPEG
            if content.startswith(b"\xff\xd8\xff"):
                return ".jpeg"
            # GIF
            if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
                return ".gif"
            # BMP
            if content.startswith(b"BM"):
                return ".bmp"
            # WEBP (RIFF....WEBP)
            if content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
                return ".webp"
            # TIFF
            if content.startswith(b"II*\x00") or content.startswith(b"MM\x00*"):
                return ".tiff"
        except Exception:
            pass
        return default

    def get_organized_path(
        self, filename: str, subfolder: Optional[str] = None, date_folder: bool = True
    ) -> Path:
        """
        获取组织化的文件路径

        Args:
            filename: 文件名
            subfolder: 子文件夹名称
            date_folder: 是否按日期创建文件夹

        Returns:
            完整文件路径
        """
        path = self.base_dir

        # 添加日期文件夹
        if date_folder:
            today = datetime.now().strftime("%Y-%m-%d")
            path = path / today

        # 添加子文件夹
        if subfolder:
            clean_subfolder = self.sanitize_filename(subfolder)
            path = path / clean_subfolder

        # 确保目录存在
        self.ensure_directory(path)

        # 返回完整文件路径
        return path / filename

    def create_save_path(
        self,
        prompt: str,
        url: str,
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
        date_folder: bool = True,
    ) -> Path:
        """
        创建保存路径

        Args:
            prompt: 生成提示词
            url: 图片URL
            tool_name: 工具名称
            custom_name: 自定义名称
            date_folder: 是否按日期创建文件夹

        Returns:
            保存路径
        """
        # 确定基础名称
        if custom_name:
            base_name = custom_name
        else:
            # 从提示词生成基础名称
            base_name = self.generate_name_from_prompt(prompt)

        # 获取文件扩展名
        from .download_manager import DownloadManager

        dm = DownloadManager()
        extension = dm.get_file_extension_from_url(url)

        # 生成唯一文件名
        filename = self.generate_unique_filename(base_name, extension)

        # 获取组织化路径
        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)

        # 验证路径安全性
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
        """
        基于扩展名创建保存路径

        Args:
            prompt: 生成提示词
            extension: 文件扩展名
            tool_name: 工具名称
            custom_name: 自定义名称
            content_hash: 内容哈希（用于去重/标识）
            date_folder: 是否按日期创建文件夹

        Returns:
            保存路径
        """
        base_name = custom_name or self.generate_name_from_prompt(prompt)
        filename = self.generate_unique_filename(base_name, extension, content_hash=content_hash)
        save_path = self.get_organized_path(filename, tool_name, date_folder=date_folder)
        if not self.validate_path(save_path):
            raise FileManagerError(f"路径不安全: {save_path}")
        return save_path

    def create_save_path_from_content(
        self,
        prompt: str,
        content_bytes: bytes,
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
        default_extension: str = ".jpeg",
        date_folder: bool = True,
    ) -> Path:
        """
        基于字节内容创建保存路径（推断扩展名并使用内容哈希）
        """
        extension = self.infer_extension_from_bytes(content_bytes, default=default_extension)
        content_hash = self.get_content_hash(content_bytes)
        return self.create_save_path_from_extension(
            prompt=prompt,
            extension=extension,
            tool_name=tool_name,
            custom_name=custom_name,
            content_hash=content_hash,
            date_folder=date_folder,
        )

    def save_bytes(self, file_path: Path, data: bytes, overwrite: bool = False) -> Dict[str, Any]:
        """
        将字节数据写入文件

        Args:
            file_path: 目标路径
            data: 字节数据
            overwrite: 是否覆盖已有文件

        Returns:
            保存结果元数据
        """
        try:
            # 目录保证存在
            self.ensure_directory(file_path.parent)
            # 如果文件存在并且不允许覆盖，生成新的唯一文件名
            final_path = file_path
            if final_path.exists() and not overwrite:
                base = final_path.stem
                ext = final_path.suffix
                # 添加一个短哈希避免冲突
                short_hash = self.get_content_hash(data)[:8]
                final_path = final_path.with_name(f"{base}_{short_hash}{ext}")
            # 写入数据
            with open(final_path, "wb") as f:
                f.write(data)
            return {
                "file_path": str(final_path),
                "file_size": len(data),
                "save_time": datetime.now().isoformat(),
            }
        except OSError as e:
            raise FileManagerError(f"写入文件失败: {file_path} -> {e}")

    def get_relative_path(self, file_path: Path) -> str:
        """
        获取相对于基础目录的路径

        Args:
            file_path: 文件路径

        Returns:
            相对路径字符串
        """
        try:
            return str(file_path.relative_to(self.base_dir))
        except ValueError:
            # 如果不在基础目录内，返回绝对路径
            return str(file_path)

    def generate_markdown_reference(self, file_path: Path, alt_text: str = "") -> str:
        """
        生成Markdown图片引用

        Args:
            file_path: 文件路径
            alt_text: 替代文本

        Returns:
            Markdown引用字符串
        """
        # 获取相对于当前工作目录的路径
        try:
            # 尝试获取相对于当前工作目录的路径
            cwd = Path.cwd()
            relative_path = str(file_path.relative_to(cwd))
        except ValueError:
            # 如果文件不在当前工作目录下，使用相对于基础目录的路径
            relative_path = self.get_relative_path(file_path)
            # 添加基础目录名称
            base_dir_name = self.base_dir.name
            relative_path = f"{base_dir_name}/{relative_path}"

        # 转换为正斜杠
        markdown_path = relative_path.replace("\\", "/")

        # 添加相对路径前缀
        if not markdown_path.startswith("./"):
            markdown_path = "./" + markdown_path

        # 生成Markdown引用
        if alt_text:
            return f"![{alt_text}]({markdown_path})"
        else:
            return f"![]({markdown_path})"

    def cleanup_old_files(self, days: int = 30) -> Dict[str, Any]:
        """
        清理旧文件

        Args:
            days: 保留天数

        Returns:
            清理结果
        """
        from datetime import timedelta

        cutoff_time = datetime.now() - timedelta(days=days)
        deleted_files = []
        deleted_size = 0
        errors = []

        try:
            for file_path in self.base_dir.rglob("*"):
                if file_path.is_file():
                    try:
                        # 检查文件修改时间
                        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                        if mtime < cutoff_time:
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            deleted_files.append(str(file_path))
                            deleted_size += file_size
                            logger.info(f"删除旧文件: {file_path}")
                    except Exception as e:
                        errors.append(f"删除文件失败 {file_path}: {e}")
                        logger.warning(f"删除文件失败: {file_path} -> {e}")

            # 删除空目录
            for dir_path in sorted(self.base_dir.rglob("*"), reverse=True):
                if dir_path.is_dir() and dir_path != self.base_dir:
                    try:
                        if not any(dir_path.iterdir()):
                            dir_path.rmdir()
                            logger.info(f"删除空目录: {dir_path}")
                    except Exception as e:
                        logger.warning(f"删除目录失败: {dir_path} -> {e}")

        except Exception as e:
            errors.append(f"清理过程出错: {e}")
            logger.error(f"清理过程出错: {e}")

        return {"deleted_files": len(deleted_files), "deleted_size": deleted_size, "errors": errors}
