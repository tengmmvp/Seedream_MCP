"""
自动保存核心模块
集成下载和文件管理,实现完整的自动保存逻辑
"""

import asyncio
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .download_manager import DownloadManager, DownloadError
from .file_manager import FileManager, FileManagerError

logger = logging.getLogger(__name__)


class AutoSaveError(Exception):
    """自动保存错误异常"""
    pass


class AutoSaveResult:
    """自动保存结果"""
    
    def __init__(
        self,
        success: bool,
        original_url: str,
        local_path: Optional[str] = None,
        markdown_ref: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.original_url = original_url
        self.local_path = local_path
        self.markdown_ref = markdown_ref
        self.error = error
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            'success': self.success,
            'original_url': self.original_url
        }
        
        if self.local_path:
            result['local_path'] = self.local_path
        
        if self.markdown_ref:
            result['markdown_ref'] = self.markdown_ref
        
        if self.error:
            result['error'] = self.error
        
        if self.metadata:
            result['metadata'] = self.metadata
        
        return result


class AutoSaveManager:
    """自动保存管理器"""
    
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        download_timeout: int = 30,
        max_retries: int = 3,
        max_file_size: int = 50 * 1024 * 1024,  # 50MB
        max_concurrent: int = 5
    ):
        """
        初始化自动保存管理器
        
        Args:
            base_dir: 基础保存目录
            download_timeout: 下载超时时间
            max_retries: 最大重试次数
            max_file_size: 最大文件大小
            max_concurrent: 最大并发下载数
        """
        self.file_manager = FileManager(base_dir)
        self.download_manager = DownloadManager(
            timeout=download_timeout,
            max_retries=max_retries,
            max_file_size=max_file_size
        )
        self.max_concurrent = max_concurrent

    def _parse_data_uri(self, data: str) -> Tuple[Optional[str], str]:
        """
        解析 data URI,返回 (mime_type, base64_payload)
        如果不是 data URI,则返回 (None, 原始字符串)
        """
        try:
            if data.startswith("data:"):
                header, payload = data.split(",", 1)
                # header 形式如: data:image/png;base64
                header = header[5:]  # 去掉 'data:'
                mime = None
                if ";" in header:
                    mime = header.split(";")[0] or None
                else:
                    mime = header or None
                return mime, payload
            return None, data
        except Exception:
            return None, data

    def _extension_from_mime(self, mime: Optional[str]) -> str:
        mapping = {
            'image/png': '.png',
            'image/jpeg': '.jpeg',
            'image/webp': '.webp',
            'image/gif': '.gif',
            'image/bmp': '.bmp',
            'image/tiff': '.tiff'
        }
        if not mime:
            return '.jpeg'
        return mapping.get(mime.lower(), '.jpeg')
    
    async def save_image(
        self,
        url: str,
        prompt: str = "",
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
        alt_text: Optional[str] = None
    ) -> AutoSaveResult:
        """
        保存单个图片
        
        Args:
            url: 图片URL
            prompt: 生成提示词
            tool_name: 工具名称
            custom_name: 自定义文件名
            alt_text: Markdown替代文本
            
        Returns:
            保存结果
        """
        try:
            logger.info(f"开始自动保存图片: {url}")
            
            # 验证URL
            if not self.download_manager.validate_url(url):
                raise AutoSaveError(f"无效的URL: {url}")
            
            # 创建保存路径
            save_path = self.file_manager.create_save_path(
                prompt=prompt,
                url=url,
                tool_name=tool_name,
                custom_name=custom_name
            )
            
            # 下载图片
            download_result = await self.download_manager.download_image(url, save_path)
            
            # 生成Markdown引用
            markdown_alt = alt_text or prompt or "Generated Image"
            markdown_ref = self.file_manager.generate_markdown_reference(
                save_path, markdown_alt
            )
            
            # 构建元数据
            metadata = {
                'prompt': prompt,
                'tool_name': tool_name,
                'save_time': datetime.now().isoformat(),
                'file_size': download_result.get('file_size', 0),
                'download_time': download_result.get('download_time', 0),
                'content_type': download_result.get('content_type', ''),
                'attempts': download_result.get('attempts', 1)
            }
            
            result = AutoSaveResult(
                success=True,
                original_url=url,
                local_path=str(save_path),
                markdown_ref=markdown_ref,
                metadata=metadata
            )
            
            logger.info(f"图片保存成功: {save_path}")
            return result
            
        except (DownloadError, FileManagerError, AutoSaveError) as e:
            logger.error(f"图片保存失败: {url} -> {e}")
            return AutoSaveResult(
                success=False,
                original_url=url,
                error=str(e)
            )
        except Exception as e:
            logger.error(f"图片保存出现未知错误: {url} -> {e}")
            return AutoSaveResult(
                success=False,
                original_url=url,
                error=f"未知错误: {e}"
            )

    async def save_base64_image(
        self,
        b64_data: str,
        prompt: str = "",
        tool_name: str = "seedream",
        custom_name: Optional[str] = None,
        alt_text: Optional[str] = None
    ) -> AutoSaveResult:
        """
        保存单个 Base64 图片(支持 data URI 或纯 base64 字符串)
        """
        try:
            logger.info("开始自动保存 Base64 图片")

            mime, payload = self._parse_data_uri(b64_data)
            payload = (payload or "").strip()
            if not payload:
                raise AutoSaveError("空的Base64数据")

            try:
                content_bytes = base64.b64decode(payload, validate=False)
            except Exception as e:
                raise AutoSaveError(f"Base64解码失败: {e}")

            # 推断扩展名
            extension = self._extension_from_mime(mime) if mime else self.file_manager.infer_extension_from_bytes(content_bytes, default=".jpeg")

            # 创建保存路径
            content_hash = self.file_manager.get_content_hash(content_bytes)
            save_path = self.file_manager.create_save_path_from_extension(
                prompt=prompt,
                extension=extension,
                tool_name=tool_name,
                custom_name=custom_name,
                content_hash=content_hash
            )

            # 写入文件
            write_result = self.file_manager.save_bytes(save_path, content_bytes)

            # 生成 Markdown 引用
            markdown_alt = alt_text or prompt or "Generated Image"
            markdown_ref = self.file_manager.generate_markdown_reference(Path(write_result['file_path']), markdown_alt)

            metadata = {
                'prompt': prompt,
                'tool_name': tool_name,
                'save_time': write_result.get('save_time'),
                'file_size': write_result.get('file_size', 0),
                'content_type': mime or '',
                'attempts': 1
            }

            original_desc = f"base64:{len(payload)}"
            logger.info(f"Base64 图片保存成功: {write_result['file_path']}")
            return AutoSaveResult(
                success=True,
                original_url=original_desc,
                local_path=write_result['file_path'],
                markdown_ref=markdown_ref,
                metadata=metadata
            )

        except (FileManagerError, AutoSaveError) as e:
            logger.error(f"Base64 图片保存失败: {e}")
            return AutoSaveResult(
                success=False,
                original_url='base64',
                error=str(e)
            )
        except Exception as e:
            logger.error(f"Base64 图片保存出现未知错误: {e}")
            return AutoSaveResult(
                success=False,
                original_url='base64',
                error=f"未知错误: {e}"
            )
    
    async def save_multiple_images(
        self,
        image_data: List[Dict[str, Any]],
        tool_name: str = "seedream"
    ) -> List[AutoSaveResult]:
        """
        批量保存多个图片
        
        Args:
            image_data: 图片数据列表,每个元素包含url、prompt等信息
            tool_name: 工具名称
            
        Returns:
            保存结果列表
        """
        logger.info(f"开始批量保存 {len(image_data)} 个图片")
        
        # 创建保存任务
        tasks = []
        for data in image_data:
            url = data.get('url', '')
            prompt = data.get('prompt', '')
            custom_name = data.get('custom_name')
            alt_text = data.get('alt_text')
            
            task = self.save_image(
                url=url,
                prompt=prompt,
                tool_name=tool_name,
                custom_name=custom_name,
                alt_text=alt_text
            )
            tasks.append(task)
        
        # 限制并发数量
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def save_with_semaphore(task):
            async with semaphore:
                return await task
        
        # 执行所有任务
        results = await asyncio.gather(
            *[save_with_semaphore(task) for task in tasks],
            return_exceptions=True
        )
        
        # 处理异常结果
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                url = image_data[i].get('url', 'unknown')
                processed_results.append(AutoSaveResult(
                    success=False,
                    original_url=url,
                    error=str(result)
                ))
            else:
                processed_results.append(result)
        
        # 统计结果
        success_count = sum(1 for r in processed_results if r.success)
        logger.info(f"批量保存完成: {success_count}/{len(image_data)} 成功")
        
        return processed_results

    async def save_multiple_base64_images(
        self,
        image_data: List[Dict[str, Any]],
        tool_name: str = "seedream"
    ) -> List[AutoSaveResult]:
        """
        并发保存多个 Base64 图片
        """
        logger.info(f"开始批量保存 {len(image_data)} 个 Base64 图片")

        tasks = []
        for data in image_data:
            b64 = data.get('b64_json', '')
            prompt = data.get('prompt', '')
            custom_name = data.get('custom_name')
            alt_text = data.get('alt_text')
            tasks.append(self.save_base64_image(
                b64_data=b64,
                prompt=prompt,
                tool_name=tool_name,
                custom_name=custom_name,
                alt_text=alt_text
            ))

        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def save_with_semaphore(task):
            async with semaphore:
                return await task

        results = await asyncio.gather(
            *[save_with_semaphore(task) for task in tasks],
            return_exceptions=True
        )

        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(AutoSaveResult(
                    success=False,
                    original_url='base64',
                    error=str(result)
                ))
            else:
                processed_results.append(result)

        success_count = sum(1 for r in processed_results if r.success)
        logger.info(f"批量 Base64 保存完成: {success_count}/{len(image_data)} 成功")
        return processed_results
    
    def format_response_with_auto_save(
        self,
        original_response: Dict[str, Any],
        auto_save_results: List[AutoSaveResult],
        include_original_urls: bool = True
    ) -> Dict[str, Any]:
        """
        格式化包含自动保存信息的响应
        
        Args:
            original_response: 原始API响应
            auto_save_results: 自动保存结果列表
            include_original_urls: 是否包含原始URL
            
        Returns:
            格式化后的响应
        """
        response = original_response.copy()
        
        # 添加自动保存信息
        auto_save_info = {
            'enabled': True,
            'total_images': len(auto_save_results),
            'successful_saves': sum(1 for r in auto_save_results if r.success),
            'failed_saves': sum(1 for r in auto_save_results if not r.success),
            'results': [r.to_dict() for r in auto_save_results]
        }
        
        response['auto_save'] = auto_save_info
        
        # 添加本地路径和Markdown引用到图片信息中
        images = response.get('images', [])
        for i, (image, result) in enumerate(zip(images, auto_save_results)):
            if result.success:
                image['local_path'] = result.local_path
                image['markdown_ref'] = result.markdown_ref
                
                # 如果不包含原始URL,移除URL字段
                if not include_original_urls and 'url' in image:
                    image['original_url'] = image.pop('url')
            else:
                image['auto_save_error'] = result.error
        
        return response
    
    def generate_markdown_summary(
        self,
        auto_save_results: List[AutoSaveResult],
        title: str = "Generated Images"
    ) -> str:
        """
        生成Markdown格式的图片摘要
        
        Args:
            auto_save_results: 自动保存结果列表
            title: 摘要标题
            
        Returns:
            Markdown格式的摘要
        """
        lines = [f"# {title}", ""]
        
        successful_results = [r for r in auto_save_results if r.success]
        failed_results = [r for r in auto_save_results if not r.success]
        
        if successful_results:
            lines.append("## Successfully Saved Images")
            lines.append("")
            
            for i, result in enumerate(successful_results, 1):
                lines.append(f"### Image {i}")
                if result.markdown_ref:
                    lines.append(result.markdown_ref)
                if result.metadata and result.metadata.get('prompt'):
                    lines.append(f"**Prompt:** {result.metadata['prompt']}")
                if result.local_path:
                    lines.append(f"**Local Path:** `{result.local_path}`")
                lines.append("")
        
        if failed_results:
            lines.append("## Failed to Save")
            lines.append("")
            
            for i, result in enumerate(failed_results, 1):
                lines.append(f"### Failed Image {i}")
                lines.append(f"**URL:** {result.original_url}")
                lines.append(f"**Error:** {result.error}")
                lines.append("")
        
        # 添加统计信息
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- Total images: {len(auto_save_results)}")
        lines.append(f"- Successfully saved: {len(successful_results)}")
        lines.append(f"- Failed to save: {len(failed_results)}")
        
        return "\n".join(lines)
    
    def get_storage_info(self) -> Dict[str, Any]:
        """
        获取存储信息
        
        Returns:
            存储信息
        """
        base_dir = self.file_manager.base_dir
        
        try:
            # 计算目录大小和文件数量
            total_size = 0
            file_count = 0
            
            for file_path in base_dir.rglob("*"):
                if file_path.is_file():
                    file_count += 1
                    total_size += file_path.stat().st_size
            
            return {
                'base_directory': str(base_dir),
                'total_files': file_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'directory_exists': base_dir.exists()
            }
            
        except Exception as e:
            logger.error(f"获取存储信息失败: {e}")
            return {
                'base_directory': str(base_dir),
                'error': str(e)
            }
    
    async def cleanup_old_files(self, days: int = 30) -> Dict[str, Any]:
        """
        清理旧文件
        
        Args:
            days: 保留天数
            
        Returns:
            清理结果
        """
        return self.file_manager.cleanup_old_files(days)
