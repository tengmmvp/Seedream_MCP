"""Seedream MCP 日志配置模块。

基于 loguru 初始化日志系统，配置控制台与文件双通道输出。文件日志按 10 MB 轮换、
保留 30 天并压缩归档。通过 InterceptHandler 将标准库 logging 调用重定向至 loguru，
统一第三方库与项目内部的日志通道。全局 patcher 在每条日志格式化前剥离消息与异常
文本的控制字符，防文件名、上游错误体等经日志注入伪造日志行。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

from loguru import logger

from .errors import CONTROL_CHARS_PATTERN

if TYPE_CHECKING:
    # loguru 顶层运行时仅导出 logger 实例，Logger 类只在随包存根中声明，类型检查期导入。
    from loguru import Logger


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
    # INFO 日志，桥接后全量进入会淹没项目业务日志；httpx2/httpcore2 为 mcp SDK v2
    # 的 HTTP 客户端日志源，一并压制。
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)

    logger.info("日志系统初始化完成，级别: {}", level)
    if enable_file and log_file:
        logger.info("日志文件: {}", log_file)


def get_logger() -> Logger:
    """返回项目统一的 loguru logger 实例。

    输出中渲染的模块名由 sink 从真实调用帧取值，无需调用方传入名称。
    """
    return logger
