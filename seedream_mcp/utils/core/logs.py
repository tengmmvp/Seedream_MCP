"""Seedream MCP 日志配置模块。

基于 loguru 初始化日志系统，配置控制台与文件双通道输出。文件日志按 10 MB 轮换、
保留 30 天并压缩归档。通过 InterceptHandler 将标准库 logging 调用重定向至 loguru，
统一第三方库与项目内部的日志通道。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import sys
from pathlib import Path
from types import FrameType
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ParamSpec,
    TypeVar,
    overload,
)

from loguru import logger

if TYPE_CHECKING:
    # loguru 顶层运行时仅导出 logger 实例，Logger 类只在随包存根中声明，类型检查期导入。
    from loguru import Logger


def log_unretrieved_task_exception(task: "asyncio.Task[Any]") -> None:
    """检索并记录共享后台 task 的异常，消除事件循环告警噪音。

    经 asyncio.shield 共享的 task 在创建者被取消后，其 outer 不再消费 task 结果；
    若 task 随后失败且无其他等待者，事件循环会告警 "Task exception was never
    retrieved"。挂载本回调显式检索异常即清除该标记，有等待者正常消费时重复检索
    无副作用。供 images 与 io 子包的 single-flight 在途 task 共用。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("后台共享任务失败: {}", exc)


class InterceptHandler(logging.Handler):
    """将标准库 logging 的调用重定向至 loguru 的桥接处理器。"""

    def emit(self, record: logging.LogRecord) -> None:
        log_level: str | int
        try:
            log_level = logger.level(record.levelname).name
        except ValueError:
            log_level = record.levelno

        # 向上跳过 logging 模块自身的帧，定位真实调用者以计算正确的日志深度。
        frame: FrameType | None = logging.currentframe()
        depth = 2
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(log_level, record.getMessage())


# 日志消息中的控制字符，剥离以防文件名、上游错误体等经由日志注入伪造日志行。
_LOG_MESSAGE_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _strip_message_control_chars(record: Any) -> None:
    """剥离日志消息与异常消息的控制字符，防日志注入。

    路径名、上游错误体等可能含 CR/LF 等控制字符，原样记录会在日志文件中伪造额外行，
    干扰审计取证。作为全局 patcher 在每条日志格式化前剥离，一处覆盖所有日志点，无需
    逐处 sanitize 路径或错误文本。exc_info 渲染的 traceback 不经过 record["message"]，
    其异常消息文本经 _strip_exception_control_chars 同步清洗；traceback 帧的源代码行
    来自本地文件，不含不可信输入。
    """
    message = record["message"]
    if _LOG_MESSAGE_CONTROL_CHARS.search(message):
        record["message"] = _LOG_MESSAGE_CONTROL_CHARS.sub(" ", message)
    _strip_exception_control_chars(record)


def _strip_exception_control_chars(record: Any) -> None:
    """清洗 exc_info 记录携带的异常消息文本中的控制字符。

    loguru 对 exception 字段的 traceback 渲染独立于 message，异常 str 输出由 args
    派生，原地清洗 args 即同时修正本条与后续对该异常实例的字符串化显示，使携带
    换行的上游错误消息无法在日志文件中伪造行。仅处理含字符串 args 的异常，无 args
    或重写 __str__ 的异常保持原样，帧源代码行不受影响。
    """
    exc = record.get("exception")
    if exc is None:
        return
    value = exc.value
    args = getattr(value, "args", None)
    if not args:
        return
    cleaned = tuple(
        _LOG_MESSAGE_CONTROL_CHARS.sub(" ", arg) if isinstance(arg, str) else arg for arg in args
    )
    if cleaned != args:
        value.args = cleaned


def setup_logging(
    log_level: str = "INFO",
    log_file: str | None = None,
    enable_console: bool = True,
    enable_file: bool = True,
    force_standard_logging: bool = False,
) -> None:
    """设置日志配置。

    未显式传入 log_file 时，默认日志路径为 ``logs/seedream_mcp.log``，该相对路径
    相对于进程工作目录（CWD）解析；不同启动方式的 CWD 可能不同，如需固定位置请传入
    绝对路径或经 LOG_FILE 环境变量配置。

    Args:
        log_level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件路径，如果为 None 则使用默认路径
        enable_console: 是否启用控制台输出
        enable_file: 是否启用文件输出
        force_standard_logging: 是否强制接管标准库 logging 配置
    """
    logger.remove()
    # 全局剥离日志消息控制字符，防路径名与上游错误体经日志注入伪造行。
    logger.configure(patcher=_strip_message_control_chars)

    level = log_level.upper()

    if enable_console:
        logger.add(
            sys.stderr,
            level=level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>",
            # colorize=None 交由 loguru 按流是否 TTY 自动决定，非终端 sink 不输出 ANSI 转义。
            colorize=None,
            backtrace=True,
            diagnose=False,
            enqueue=True,
        )

    if enable_file:
        if log_file is None:
            log_dir = Path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
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

    # 安装 InterceptHandler，将标准库 logging 的全部调用重定向至 loguru。
    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=0,
        force=force_standard_logging,
    )

    # 压制第三方库的 DEBUG/INFO 噪音：桥接后其全量日志会淹没项目自身的业务日志。
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logger.info("日志系统初始化完成，级别: {}", level)
    if enable_file and log_file:
        logger.info("日志文件: {}", log_file)


def get_logger(name: str | None = None) -> Logger:
    """获取 logger 实例。

    Args:
        name: logger 名称，如果为 None 则使用调用模块名

    Returns:
        logger 实例
    """
    if name is None:
        # 自动获取调用模块名。inspect.currentframe() 返回的帧对象会形成引用环，
        # CPython 建议使用后立即显式 del 以便循环引用垃圾回收及时回收该帧。
        current = inspect.currentframe()
        try:
            caller = current.f_back if current is not None else None
            name = caller.f_globals.get("__name__", "unknown") if caller is not None else "unknown"
        finally:
            del current

    return logger.bind(name=name)


P = ParamSpec("P")
R = TypeVar("R")


@overload
def log_function_call(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]: ...


@overload
def log_function_call(func: Callable[P, R]) -> Callable[P, R]: ...


def log_function_call(func: Callable[P, Any]) -> Callable[P, Any]:
    """装饰函数并在调用入口记录日志。

    使用 ``ParamSpec`` 透传被装饰函数的参数规格，使用 ``TypeVar`` 保留原始返回类型；
    同步与异步经由两条 overload 声明分别匹配，使装饰后的函数对静态类型检查器仍保持
    精确签名，避免 ``await`` 链路返回类型退化为 ``Any``。

    Args:
        func: 要装饰的函数

    Returns:
        装饰后的函数
    """

    # 仅记录调用入口日志；异常交由被装饰函数自身的错误处理统一记录，避免在此重复
    # 输出 ERROR 与函数内部日志叠加，造成同一失败被记录多次。
    @functools.wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        logger.info("函数调用: {}()", func.__qualname__)
        return await func(*args, **kwargs)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
        logger.info("函数调用: {}()", func.__qualname__)
        return func(*args, **kwargs)

    if inspect.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
