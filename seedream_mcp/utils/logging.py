"""
Seedream MCP工具 - 日志配置模块

配置日志记录功能，支持控制台和文件输出。
"""

import sys
import logging
from typing import Any, Callable, Optional
from pathlib import Path

from loguru import logger


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    enable_console: bool = True,
    enable_file: bool = True,
) -> None:
    """设置日志配置

    Args:
        log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径，如果为None则使用默认路径
        enable_console: 是否启用控制台输出
        enable_file: 是否启用文件输出
    """
    # 移除默认的loguru处理器
    logger.remove()

    # 设置日志级别
    level = log_level.upper()

    # 控制台输出配置
    if enable_console:
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>",
            colorize=True,
            backtrace=True,
            diagnose=True,
        )

    # 文件输出配置
    if enable_file:
        if log_file is None:
            # 使用默认日志文件路径
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / "seedream_mcp.log"
        else:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_path),
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="10 MB",  # 日志文件大小超过10MB时轮转
            retention="30 days",  # 保留30天的日志文件
            compression="zip",  # 压缩旧的日志文件
            backtrace=True,
            diagnose=True,
            enqueue=True,  # 异步写入，提高性能
        )

    # 配置标准库logging以重定向到loguru
    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # 获取对应的loguru级别
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            # 查找调用者
            frame, depth = logging.currentframe(), 2
            while frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    # 设置标准库logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # 设置第三方库的日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger.info(f"日志系统初始化完成，级别: {level}")
    if enable_file and log_file:
        logger.info(f"日志文件: {log_file}")


def get_logger(name: Optional[str] = None):
    """获取logger实例

    Args:
        name: logger名称，如果为None则使用调用模块名

    Returns:
        logger实例
    """
    if name is None:
        # 自动获取调用模块名
        import inspect

        frame = inspect.currentframe().f_back
        name = frame.f_globals.get("__name__", "unknown")

    return logger.bind(name=name)


def log_function_call(func: Callable[..., Any]):
    """函数调用日志装饰器

    Args:
        func: 要装饰的函数

    Returns:
        装饰后的函数
    """
    import functools
    import asyncio

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        func_name = f"{func.__qualname__}"
        logger.info(f"函数调用: {func_name}({{}})")

        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"函数调用失败: {func_name} - {e}")
            raise

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        func_name = f"{func.__qualname__}"
        logger.info(f"函数调用: {func_name}({{}})")

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"函数调用失败: {func_name} - {e}")
            raise

    # 检查是否是异步函数
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


def log_function_call_manual(
    func_name: str,
    args: Optional[dict[str, Any]] = None,
    result: Optional[Any] = None,
    error: Optional[Exception] = None,
) -> None:
    """手动记录函数调用日志

    Args:
        func_name: 函数名称
        args: 函数参数（敏感信息会被过滤）
        result: 函数返回结果
        error: 异常信息
    """
    # 过滤敏感信息
    safe_args = _filter_sensitive_data(args) if args else {}

    if error:
        logger.error(f"函数调用失败: {func_name}({safe_args}) - {error}")
    else:
        logger.info(f"函数调用: {func_name}({safe_args})")
        if result is not None:
            logger.debug(f"函数返回: {_filter_sensitive_data(result)}")


def _filter_sensitive_data(data):
    """过滤敏感数据

    Args:
        data: 要过滤的数据

    Returns:
        过滤后的数据
    """
    if isinstance(data, dict):
        filtered = {}
        for key, value in data.items():
            if any(
                sensitive in key.lower() for sensitive in ["key", "token", "password", "secret"]
            ):
                filtered[key] = "***"
            else:
                filtered[key] = _filter_sensitive_data(value)
        return filtered
    elif isinstance(data, list):
        return [_filter_sensitive_data(item) for item in data]
    elif isinstance(data, str) and len(data) > 100:
        # 截断过长的字符串
        return data[:100] + "..."
    else:
        return data
