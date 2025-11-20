"""
Seedream 4.0 MCP工具配置管理模块

负责从环境变量读取配置信息，并提供配置验证功能。
"""

import os
from typing import Optional
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

from .utils.errors import SeedreamConfigError


@dataclass
class SeedreamConfig:
    """Seedream 4.0 MCP工具配置类"""
    
    # 必需配置
    api_key: str
    
    # 可选配置（带默认值）
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_id: str = "doubao-seedream-4-0-250828"
    default_size: str = "2K"
    default_watermark: bool = False
    timeout: int = 60
    api_timeout: int = 60
    max_retries: int = 3
    
    # 日志配置
    log_level: str = "INFO"
    log_file: Optional[str] = None
    
    # 自动保存配置
    auto_save_enabled: bool = True
    auto_save_base_dir: Optional[str] = None
    auto_save_download_timeout: int = 30
    auto_save_max_retries: int = 3
    auto_save_max_file_size: int = 50 * 1024 * 1024  # 50MB
    auto_save_max_concurrent: int = 5
    auto_save_date_folder: bool = True
    auto_save_cleanup_days: int = 30
    
    def __post_init__(self):
        """配置验证"""
        self.validate()
    
    def validate(self):
        """验证配置参数"""
        # 验证API密钥
        if not self.api_key or self.api_key.strip() == "":
            raise SeedreamConfigError("API密钥不能为空")
        
        if self.api_key == "your_api_key_here":
            raise SeedreamConfigError("请设置有效的API密钥，不能使用默认占位符")
        
        # 验证base_url
        if not self.base_url or not self.base_url.startswith(("http://", "https://")):
            raise SeedreamConfigError("base_url必须是有效的HTTP/HTTPS URL")
        
        # 验证model_id
        if not self.model_id or self.model_id.strip() == "":
            raise SeedreamConfigError("model_id不能为空")
        
        # 验证default_size
        valid_sizes = ["1K", "2K", "4K"]
        if self.default_size not in valid_sizes:
            raise SeedreamConfigError(f"default_size必须是以下值之一: {valid_sizes}")
        
        # 验证timeout
        if self.timeout <= 0:
            raise SeedreamConfigError("timeout必须大于0")
        
        # 验证api_timeout
        if self.api_timeout <= 0:
            raise SeedreamConfigError("api_timeout必须大于0")
        
        # 验证max_retries
        if self.max_retries < 0:
            raise SeedreamConfigError("max_retries不能小于0")
        
        # 验证log_level
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_log_levels:
            raise SeedreamConfigError(f"log_level必须是以下值之一: {valid_log_levels}")
        
        # 标准化log_level
        self.log_level = self.log_level.upper()
        
        # 验证自动保存配置
        if self.auto_save_download_timeout <= 0:
            raise SeedreamConfigError("auto_save_download_timeout必须大于0")
        
        if self.auto_save_max_retries < 0:
            raise SeedreamConfigError("auto_save_max_retries不能小于0")
        
        if self.auto_save_max_file_size <= 0:
            raise SeedreamConfigError("auto_save_max_file_size必须大于0")
        
        if self.auto_save_max_concurrent <= 0:
            raise SeedreamConfigError("auto_save_max_concurrent必须大于0")
        
        if self.auto_save_cleanup_days < 0:
            raise SeedreamConfigError("auto_save_cleanup_days不能小于0")
        
        # 验证自动保存目录
        if self.auto_save_base_dir:
            try:
                base_dir = Path(self.auto_save_base_dir)
                if base_dir.exists() and not base_dir.is_dir():
                    raise SeedreamConfigError(f"auto_save_base_dir不是有效目录: {self.auto_save_base_dir}")
            except Exception as e:
                raise SeedreamConfigError(f"auto_save_base_dir路径无效: {self.auto_save_base_dir} -> {e}")
    
    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "SeedreamConfig":
        """从环境变量创建配置实例
        
        Args:
            env_file: .env文件路径，如果不指定则使用默认路径
            
        Returns:
            SeedreamConfig实例
            
        Raises:
            SeedreamConfigError: 配置错误时抛出
        """
        # 加载.env文件
        if env_file:
            load_dotenv(env_file)
        else:
            # 尝试加载当前目录和上级目录的.env文件
            load_dotenv()
            load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
        
        # 读取环境变量
        api_key = os.getenv("ARK_API_KEY", "").strip()
        if not api_key:
            raise SeedreamConfigError(
                "未找到ARK_API_KEY环境变量。请设置您的火山引擎API密钥。\n"
                "您可以通过以下方式设置：\n"
                "1. 创建.env文件并添加: ARK_API_KEY=your_actual_api_key\n"
                "2. 设置系统环境变量: export ARK_API_KEY=your_actual_api_key"
            )
        
        # 创建配置实例
        config = cls(
            api_key=api_key,
            base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            model_id=os.getenv("SEEDREAM_MODEL_ID", "doubao-seedream-4-0-250828"),
            default_size=os.getenv("SEEDREAM_DEFAULT_SIZE", "2K"),
            default_watermark=_parse_bool(os.getenv("SEEDREAM_DEFAULT_WATERMARK", "false")),
            timeout=_parse_int(os.getenv("SEEDREAM_TIMEOUT", "60")),
            api_timeout=_parse_int(os.getenv("SEEDREAM_API_TIMEOUT", "60")),
            max_retries=_parse_int(os.getenv("SEEDREAM_MAX_RETRIES", "3")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=os.getenv("LOG_FILE"),
            # 自动保存配置
            auto_save_enabled=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_ENABLED", "true")),
            auto_save_base_dir=os.getenv("SEEDREAM_AUTO_SAVE_BASE_DIR"),
            auto_save_download_timeout=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT", "30")),
            auto_save_max_retries=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_RETRIES", "3")),
            auto_save_max_file_size=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE", str(50 * 1024 * 1024))),
            auto_save_max_concurrent=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_MAX_CONCURRENT", "5")),
            auto_save_date_folder=_parse_bool(os.getenv("SEEDREAM_AUTO_SAVE_DATE_FOLDER", "true")),
            auto_save_cleanup_days=_parse_int(os.getenv("SEEDREAM_AUTO_SAVE_CLEANUP_DAYS", "30")),
        )
        
        return config
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "api_key": "***" if self.api_key else None,  # 隐藏敏感信息
            "base_url": self.base_url,
            "model_id": self.model_id,
            "default_size": self.default_size,
            "default_watermark": self.default_watermark,
            "timeout": self.timeout,
            "api_timeout": self.api_timeout,
            "max_retries": self.max_retries,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "auto_save_enabled": self.auto_save_enabled,
            "auto_save_base_dir": self.auto_save_base_dir,
            "auto_save_download_timeout": self.auto_save_download_timeout,
            "auto_save_max_retries": self.auto_save_max_retries,
            "auto_save_max_file_size": self.auto_save_max_file_size,
            "auto_save_max_concurrent": self.auto_save_max_concurrent,
            "auto_save_date_folder": self.auto_save_date_folder,
            "auto_save_cleanup_days": self.auto_save_cleanup_days,
        }
    
    def __repr__(self) -> str:
        """字符串表示（隐藏敏感信息）"""
        return f"SeedreamConfig(api_key='***', base_url='{self.base_url}', model_id='{self.model_id}')"
    
def _parse_bool(value: str) -> bool:
    """解析布尔值字符串"""
    if isinstance(value, bool):
        return value
    return value.lower() in ("true", "1", "yes", "on")


def _parse_int(value: str) -> int:
    """解析整数字符串"""
    try:
        return int(value)
    except (ValueError, TypeError):
        raise SeedreamConfigError(f"无法解析整数值: {value}")


# 全局配置实例（延迟初始化）
_global_config: Optional[SeedreamConfig] = None


def get_global_config() -> SeedreamConfig:
    """获取全局配置实例"""
    global _global_config
    if _global_config is None:
        _global_config = SeedreamConfig.from_env()
    return _global_config


def set_config(config: SeedreamConfig):
    """设置全局配置实例"""
    global _global_config
    _global_config = config


def reload_config(env_file: Optional[str] = None):
    """重新加载配置"""
    global _global_config
    _global_config = SeedreamConfig.from_env(env_file)