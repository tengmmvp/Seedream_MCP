"""Seedream MCP 日志配置模块。

基于 loguru 初始化日志系统，配置控制台与文件双通道输出。文件日志按 10 MB 轮换、
保留 30 天并压缩归档。通过 InterceptHandler 将标准库 logging 调用重定向至 loguru，
统一第三方库与项目内部的日志通道。
"""

import functools
import inspect
import logging
import sys
from pathlib import Path
from types import FrameType
from typing import Any, Callable, Optional, Union

from loguru import logger


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    enable_console: bool = True,
    enable_file: bool = True,
    force_standard_logging: bool = False,
) -> None:
    """
    设置日志配置

    Args:
        log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径，如果为None则使用默认路径
        enable_console: 是否启用控制台输出
        enable_file: 是否启用文件输出
        force_standard_logging: 是否强制接管标准库 logging 配置
    """
    # 移除默认的loguru处理器
    logger.remove()

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
            diagnose=False,
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
            format=(
                "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | " "{name}:{function}:{line} - {message}"
            ),
            rotation="10 MB",
            retention="30 days",
            compression="zip",
            backtrace=True,
            diagnose=False,
            enqueue=True,
        )

    # 配置标准库logging以重定向到loguru
    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_level: Union[str, int]
            try:
                log_level = logger.level(record.levelname).name
            except ValueError:
                log_level = record.levelno

            # 向上跳过 logging 模块自身的帧，定位真实调用者以计算正确的日志深度
            frame: Optional[FrameType] = logging.currentframe()
            depth = 2
            while frame is not None and frame.f_code.co_filename == logging.__file__:
                frame = frame.f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(log_level, record.getMessage())

    # 安装 InterceptHandler，将标准库 logging 的全部调用重定向至 loguru
    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=0,
        force=force_standard_logging,
    )

    # 设置第三方库的日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger.info("日志系统初始化完成，级别: {}", level)
    if enable_file and log_file:
        logger.info("日志文件: {}", log_file)


def get_logger(name: Optional[str] = None) -> Any:
    """
    获取logger实例

    Args:
        name: logger名称，如果为None则使用调用模块名

    Returns:
        logger实例
    """
    if name is None:
        # 自动获取调用模块名
        current = inspect.currentframe()
        caller = current.f_back if current is not None else None
        name = caller.f_globals.get("__name__", "unknown") if caller is not None else "unknown"

    return logger.bind(name=name)


def log_function_call(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    函数调用日志装饰器

    Args:
        func: 要装饰的函数

    Returns:
        装饰后的函数
    """

    # 仅记录调用入口日志；异常交由被装饰函数自身的错误处理统一记录，避免在此重复
    # 输出 ERROR 与函数内部日志叠加，造成同一失败被记录多次。
    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.info("函数调用: {}()", func.__qualname__)
        return await func(*args, **kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.info("函数调用: {}()", func.__qualname__)
        return func(*args, **kwargs)

    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper
