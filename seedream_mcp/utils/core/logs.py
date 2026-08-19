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
import weakref
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

    经 asyncio.shield 共享的 task 在消费方被取消后可能无人消费结果，失败时事件循环
    会告警 "Task exception was never retrieved"；本回调显式检索异常即清除该标记并以
    warning 记录孤儿失败。登记时机由调用方的消费者计数约束，仅最后一个潜在消费者
    放弃且结果未被接收时才登记，异常已送抵消费者的场景由其既有错误通道记录，同一
    异常不重复入日志。供 images 与 io 子包的 single-flight 在途 task 经
    arm_unretrieved_exception_logging 登记。

    Args:
        task: 可能不再有等待者消费结果的在途后台任务。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).warning("后台共享任务失败: {}", exc)


# 已登记「未取回异常」检索回调的 task 集合。WeakSet 持弱引用，不延长 task 生命
# 周期；创建者与等待者相继放弃同一 task 时仅登记一次。
_UNRETRIEVED_ARMED_TASKS: "weakref.WeakSet[asyncio.Task[Any]]" = weakref.WeakSet()


def arm_unretrieved_exception_logging(
    task: "asyncio.Task[Any]",
    callback: Callable[["asyncio.Task[Any]"], None] | None = None,
) -> None:
    """为共享 task 登记「未取回异常」检索回调，供消费方放弃等待时调用。

    仅最后一个潜在消费者放弃且结果未被接收时调用本函数，此后 task 失败将无人检索
    异常，回调兜底检索并记录；异常已送抵消费者的场景不登记，避免重复入日志。task
    成功或被取消时回调检索无异常，静默无害。

    Args:
        task: 消费方已放弃等待的共享后台任务。
        callback: 触发时执行的回调，缺省为 log_unretrieved_task_exception；调用方
            持有消费者计数等附加上下文时可传自定义回调，在触发时复查登记前提。
    """
    if task in _UNRETRIEVED_ARMED_TASKS:
        return
    _UNRETRIEVED_ARMED_TASKS.add(task)
    resolved_callback = callback if callback is not None else log_unretrieved_task_exception
    task.add_done_callback(resolved_callback)


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

        # 向上跳过 emit 自身帧与 logging 模块内部的帧，定位真实调用者以计算日志深度。
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

    作为全局 patcher 在每条日志格式化前剥离，一处覆盖所有日志点：路径名、上游错误
    体中的 CR/LF 原样记录会在日志文件中伪造额外行。exc_info 的异常消息文本经
    _strip_exception_control_chars 同步清洗，traceback 帧源代码行来自本地文件不受
    影响。本层只压平控制字符，不剥离键值形态的凭据：日志通道有意保留异常原文便于
    排障，键值脱敏由调用点承担。
    """
    message = record["message"]
    if _LOG_MESSAGE_CONTROL_CHARS.search(message):
        record["message"] = _LOG_MESSAGE_CONTROL_CHARS.sub(" ", message)
    _strip_exception_control_chars(record)


def _strip_exception_control_chars(record: Any) -> None:
    """清洗 exc_info 记录携带的异常消息文本中的控制字符。

    异常 str 输出由 args 派生，原地清洗 args 即同时修正本条与后续对该异常实例的
    字符串化显示，携带换行的上游错误消息无法在日志文件中伪造行；仅处理含字符串
    args 的异常，无 args 或重写 __str__ 的保持原样。
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

    未显式传入 log_file 时，默认路径 ``.seedream/logs/seedream_mcp.log`` 相对进程
    工作目录解析；不同启动方式的 CWD 可能不同，如需固定位置请传入绝对路径或经
    LOG_FILE 环境变量配置。

    Args:
        log_level: 日志级别，取 DEBUG、INFO、WARNING、ERROR 或 CRITICAL。
        force_standard_logging: 是否强制接管标准库 logging 配置；未强制且 root
            logger 已有 handler 时标准库日志不被拦截，输出 warning 提示。
    """
    logger.remove()
    # 全局 patcher 剥离日志消息控制字符，防日志注入。
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

    # 安装 InterceptHandler，将标准库 logging 的全部调用重定向至 loguru；root
    # logger 已有 handler 且未强制接管时 basicConfig 整体 no-op，标准库日志绕过
    # 桥接与控制字符防护，输出 warning 提示部署方处置。
    if not force_standard_logging and logging.getLogger().hasHandlers():
        logger.warning(
            "标准库 root logger 已有 handler 且 force_standard_logging=False，"
            "标准库日志未被 loguru 拦截，也不经控制字符防护"
        )
    logging.basicConfig(
        handlers=[InterceptHandler()],
        level=0,
        force=force_standard_logging,
    )

    # 压制第三方库的 DEBUG/INFO 噪音：httpx 每次 API 调用、httpcore 每个连接均输出
    # INFO 日志，桥接后全量进入会淹没项目业务日志。
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger.info("日志系统初始化完成，级别: {}", level)
    if enable_file and log_file:
        logger.info("日志文件: {}", log_file)


def get_logger(name: str | None = None) -> Logger:
    """获取绑定了指定名称的 loguru logger 实例。

    name 仅绑定到 extra 字段，输出中渲染的模块名始终取真实调用帧；当前调用方统一
    传 ``__name__``，二者恰好一致，传自定义名称不改变渲染输出。name 为 None 时自动
    取调用模块名。
    """
    if name is None:
        # 自动获取调用模块名；帧对象会形成引用环，用后立即显式 del 以便及时回收。
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
    """装饰函数并在调用入口记录日志，同步与异步在实现内分流。

    以 ``ParamSpec``/``TypeVar`` 透传参数规格与返回类型，装饰后的静态签名与原函数
    完全一致。声明采用单一 ``Callable[P, R]`` 而非 overload 区分同步异步：mypy 对
    签名含 ``Any`` 的异步函数做 overload 约束求解时会把 ParamSpec 擦除为
    ``(*Any, **Any) -> Any``，单一签名可精确穿透。
    """

    # 仅记录调用入口；异常由被装饰函数自身的错误处理统一记录，避免同一失败重复入日志。
    if inspect.iscoroutinefunction(func):
        # Awaitable[R] 视图使 await 表达式还原出 R，内层按 R 声明，外层 cast 收敛
        # 包装产生的第二层 coroutine 容器。
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
