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
    cast,
)

from loguru import logger

from .errors import CONTROL_CHARS_PATTERN

if TYPE_CHECKING:
    # loguru 顶层运行时仅导出 logger 实例，Logger 类只在随包存根中声明，类型检查期导入。
    from loguru import Logger


def log_unretrieved_task_exception(task: "asyncio.Task[Any]") -> None:
    """检索并记录共享后台 task 的异常，消除事件循环告警噪音。

    经 asyncio.shield 共享的 task 在创建者被取消后，其 outer 不再消费 task 结果；
    若 task 随后失败且无其他等待者，事件循环会告警 "Task exception was never
    retrieved"。挂载本回调显式检索异常即清除该标记，有等待者正常消费时重复检索
    无副作用。供 images 与 io 子包的 single-flight 在途 task 共用。

    Args:
        task: 可能不再有等待者消费结果的在途后台任务。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("后台共享任务失败: {}", exc)


class InterceptHandler(logging.Handler):
    """将标准库 logging 的调用重定向至 loguru 的桥接处理器。"""

    def emit(self, record: logging.LogRecord) -> None:
        """将标准库日志记录转写为 loguru 日志调用。

        级别名经 loguru 级别表映射，未注册的级别名回退为数值级别；日志深度
        回溯至 logging 模块之外的原始调用帧，使记录的调用位置为真实调用者。
        """
        log_level: str | int
        try:
            log_level = logger.level(record.levelname).name
        except ValueError:
            log_level = record.levelno

        # 向上跳过 emit 自身帧与 logging 模块内部的帧，定位真实调用者以计算正确的
        # 日志深度。首帧无条件跳过，其后仅当帧位于 logging 模块内时继续跳过。
        frame: FrameType | None = logging.currentframe()
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            depth += 1
            frame = frame.f_back

        logger.opt(depth=depth, exception=record.exc_info).log(log_level, record.getMessage())


# 日志消息中的控制字符，剥离以防文件名、上游错误体等经由日志注入伪造日志行。
# 字符类取 errors.CONTROL_CHARS_PATTERN 单一来源，与错误文本脱敏通道保持同一口径。
_LOG_MESSAGE_CONTROL_CHARS = CONTROL_CHARS_PATTERN


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

    未显式传入 log_file 时，默认日志路径为 ``.seedream/logs/seedream_mcp.log``，
    该相对路径相对于进程工作目录（CWD）解析；不同启动方式的 CWD 可能不同，如需固定
    位置请传入绝对路径或经 LOG_FILE 环境变量配置。

    Args:
        log_level: 日志级别，取 DEBUG、INFO、WARNING、ERROR 或 CRITICAL。
        log_file: 日志文件路径，为 None 时使用默认路径 .seedream/logs/seedream_mcp.log。
        enable_console: 是否启用控制台输出。
        enable_file: 是否启用文件输出。
        force_standard_logging: 是否强制接管标准库 logging 配置。
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
            log_dir = Path(".seedream") / "logs"
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
    # httpx 每次 API 调用输出一条 INFO "HTTP Request"，与 urllib3/aiohttp 同列压制。
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logger.info("日志系统初始化完成，级别: {}", level)
    if enable_file and log_file:
        logger.info("日志文件: {}", log_file)


def get_logger(name: str | None = None) -> Logger:
    """获取 logger 实例。

    name 仅绑定到日志记录的 extra 字段，输出中渲染的模块名始终取真实调用帧；
    当前调用方统一传 ``__name__``，二者恰好一致，传自定义名称不会改变渲染输出。

    Args:
        name: 绑定到 extra 的名称，为 None 时自动取调用模块名。

    Returns:
        绑定了指定名称的 loguru logger 实例。
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


def log_function_call(func: Callable[P, R]) -> Callable[P, R]:
    """装饰函数并在调用入口记录日志。

    使用 ``ParamSpec`` 透传被装饰函数的参数规格，``TypeVar`` 绑定原始返回类型；
    同步与异步在实现内分流，装饰后的静态签名与原函数完全一致，``await`` 链路保持
    精确返回类型。异步函数的 ``R`` 求解为其原生 coroutine 类型，与未装饰时一致。
    声明采用单一 ``Callable[P, R]`` 而非 overload 区分同步与异步分支：mypy 对
    签名含 ``Any`` 的异步函数做 overload 约束求解时会把 ParamSpec 整体擦除为
    ``(*Any, **Any) -> Any``，单一签名不经接口约束求解，类型可精确穿透。

    Args:
        func: 要装饰的函数。

    Returns:
        装饰后的函数。
    """

    # 仅记录调用入口日志；异常交由被装饰函数自身的错误处理统一记录，避免在此重复
    # 输出 ERROR 与函数内部日志叠加，造成同一失败被记录多次。
    if inspect.iscoroutinefunction(func):
        # 异步函数的返回类型即 coroutine，Awaitable[R] 视图使 await 表达式还原出 R，
        # 内层包装按 R 声明返回类型，外层 cast 收敛包装产生的第二层 coroutine 容器。
        async_func = cast("Callable[P, Awaitable[R]]", func)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger.info("函数调用: {}()", func.__qualname__)
            return await async_func(*args, **kwargs)

        return cast("Callable[P, R]", async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        logger.info("函数调用: {}()", func.__qualname__)
        return func(*args, **kwargs)

    return sync_wrapper
