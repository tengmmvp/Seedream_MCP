"""Seedream MCP 日志配置模块。

基于 loguru 初始化日志系统，配置控制台与文件双通道输出。文件日志按 10 MB 轮换、
保留 30 天并压缩归档。通过 InterceptHandler 将标准库 logging 调用重定向至 loguru，
统一第三方库与项目内部的日志通道。另含 asyncio 共享后台任务的可观测性设施：
InflightEntry 提供 single-flight 在途条目与孤儿异常登记判定，供 io_download 的
DNS 在途解析与 image_prepare 的参考图预处理在途去重共用；消费者全部放弃且结果
未被消费时，失败 task 经 arm_unretrieved_exception_logging 登记的兜底回调以
warning 记录。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import weakref
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from loguru import logger

from .errors import CONTROL_CHARS_PATTERN

if TYPE_CHECKING:
    # loguru 顶层运行时仅导出 logger 实例，Logger 类只在随包存根中声明，类型检查期导入。
    from loguru import Logger


def log_unretrieved_task_exception(task: "asyncio.Task[Any]") -> None:
    """检索并记录共享后台 task 的异常，消除事件循环告警噪音。

    经 asyncio.shield 共享的 task 在消费方被取消后可能无人消费结果，失败时事件循环
    会告警 "Task exception was never retrieved"；本回调显式检索异常即清除该标记并以
    warning 记录孤儿失败。task 成功或被取消时检索无异常，静默无害。

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
    异常，回调兜底检索并记录；异常已送抵消费者的场景不登记，避免重复入日志。

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


# single-flight 在途条目持有的共享 task 结果类型。
_InflightResultT = TypeVar("_InflightResultT")


class InflightEntry(Generic[_InflightResultT]):
    """single-flight 共享在途条目：共享 task、活动消费者计数与结果消费标记。

    消费者经 consume 以 shield 等待共享 task：本调用被取消时仅取消自身 await，
    底层 task 继续运行保护其他等待者；结果或异常送达任一消费者即置位 observed，
    此后计数归零不再登记兜底日志；全部消费者放弃且结果未被消费时经
    arm_orphan_logging 登记兜底日志回调。on_cancel 在取消异常向外传播前执行，
    供创建者转移信号量槽位等清理责任。
    """

    __slots__ = ("task", "consumers", "observed")

    def __init__(self, task: "asyncio.Task[_InflightResultT]") -> None:
        self.task = task
        self.consumers = 0
        self.observed = False

    async def consume(self, on_cancel: Callable[[], None] | None = None) -> _InflightResultT:
        """以消费者身份等待共享 task，返回其结果或向调用方重放其异常。

        消费者计数在 await 期间登记、finally 释放；取消异常向外传播前执行
        on_cancel；结果与异常送达均置位 observed，计数归零且结果未被消费时经
        arm_orphan_logging 登记兜底日志回调。
        """
        self.consumers += 1
        try:
            try:
                result = await asyncio.shield(self.task)
            except asyncio.CancelledError:
                if on_cancel is not None:
                    on_cancel()
                raise
            except Exception:
                # 异常经 shield 送达本消费者，置位已消费标记，不再登记兜底日志。
                self.observed = True
                raise
            self.observed = True
            return result
        finally:
            self.consumers -= 1
            self.arm_orphan_logging()

    def arm_orphan_logging(self) -> None:
        """最后一个潜在消费者放弃且结果未被消费时，登记兜底日志回调。

        回调触发时复查登记前提：登记后新消费者加入又放弃或消费的窗口内前提可能
        已不成立，复查不成立即静默跳过，避免同一异常重复入日志。
        """

        def _log_if_orphaned(task: "asyncio.Task[_InflightResultT]") -> None:
            # 触发时经模块全局名解析通用记录函数，保持调用方可替换该实现做测试观测。
            if self.consumers == 0 and not self.observed:
                log_unretrieved_task_exception(task)

        if self.consumers > 0 or self.observed:
            return
        arm_unretrieved_exception_logging(self.task, _log_if_orphaned)


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
        log_file: 日志文件路径；None 时默认 ``.seedream/logs/seedream_mcp.log``
            相对进程工作目录解析。
        enable_console: 是否启用控制台通道，输出至 stderr。
        enable_file: 是否启用文件通道，按 10 MB 轮换、保留 30 天并压缩归档。
        force_standard_logging: 是否强制接管标准库 logging 配置；未强制且 root
            logger 已有 handler 时标准库日志不被拦截，输出 warning 提示。
    """
    logger.remove()
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


def get_logger() -> Logger:
    """返回项目统一的 loguru logger 实例。

    输出中渲染的模块名由 sink 从真实调用帧取值，无需调用方传入名称。
    """
    return logger
