"""按级别记录格式化消息的 loguru logger 测试替身与真实日志的捕获器。

RecordingLogger 供 test_client_refactor、test_logging_setup、
test_workspace_roots_scope、test_validation_prompt、test_tighten_schema_runtime_probe
与 test_prepare_cache_single_flight 复用，替代各文件自持的近实现替身。
opt(lazy=True) 的 callable 实参在记录时求值，若不求值，lambda 对象本身进入
格式化字符串，会掩盖 _summarize_prompt 等求值路径未运行的回归。
capture_loguru_messages 捕获进程级真实 loguru logger 的指定级别消息，供
config_builder、find_images_directory 与 io_path_polish 等断言服务端告警。
preserved_loguru_globals 放行块内对进程级 loguru sink 与 root logging 全局的
改写、退出时恢复，供 test_logging_setup 与 test_cli_log_init_failure 复用。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from seedream_mcp.utils.core.logs import get_logger


class RecordingLogger:
    """按级别记录格式化消息的 loguru logger 替身。

    Attributes:
        info_messages: 完成 lazy 实参求值与模板格式化后的 info 消息列表。
        warnings: 格式化后的 warning 消息列表。
        errors: 格式化后的 error 消息列表。
        opt_kwargs: 每次 opt 调用携带的关键字参数，无参调用不记录。
        add_kwargs: 每次 add 调用携带的关键字参数。
    """

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.opt_kwargs: list[dict[str, Any]] = []
        self.add_kwargs: list[dict[str, Any]] = []

    def remove(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def add(self, *args: Any, **kwargs: Any) -> int:
        del args
        self.add_kwargs.append(dict(kwargs))
        return 0

    def configure(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def opt(self, *args: Any, **kwargs: Any) -> "RecordingLogger":
        del args
        if kwargs:
            self.opt_kwargs.append(dict(kwargs))
        return self

    def _record(self, bucket: list[str], message: str, args: tuple[Any, ...]) -> None:
        evaluated = tuple(arg() if callable(arg) else arg for arg in args)
        bucket.append(message.format(*evaluated) if evaluated else message)

    def info(self, message: str, *args: Any) -> None:
        self._record(self.info_messages, message, args)

    def warning(self, message: str, *args: Any) -> None:
        self._record(self.warnings, message, args)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self._record(self.errors, message, args)

    def debug(self, message: str, *args: Any) -> None:
        del message, args


@contextmanager
def capture_loguru_messages(records: list[str], level: str = "WARNING") -> Iterator[None]:
    """把进程级真实 loguru logger 的指定级别格式化消息捕获进 records。"""
    handler_id = get_logger().add(lambda message: records.append(str(message)), level=level)
    try:
        yield
    finally:
        try:
            get_logger().remove(handler_id)
        except ValueError:
            # sink 已被块内 blanket remove 拆除时无物可移
            pass


@contextmanager
def preserved_loguru_globals() -> Iterator[None]:
    """放行块内对进程级日志全局的改写，退出时恢复到进入前状态。

    loguru sink 的构造参数不可由 id 取回，且 setup_logging 自身会拆除既有
    sink，故退出时 loguru 重建为默认 stderr 单 sink 而非逐一复原；root 与
    stdlib 具名 logger 的级别逐名恢复，块内新实例化者重置为 NOTSET。
    """
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    root_level = root.level
    saved_levels = {
        name: logging.getLogger(name).level for name in list(logging.root.manager.loggerDict)
    }
    try:
        yield
    finally:
        # complete 先落盘在途消息再拆除本用例安装的 sink。
        logger = get_logger()
        logger.complete()
        logger.remove()
        logger.add(sys.stderr)
        # configure 对 patcher=None 不生效，以空 patcher 中和块内设置的 patcher。
        logger.configure(patcher=lambda record: None)
        root.handlers = root_handlers
        root.setLevel(root_level)
        for name in list(logging.root.manager.loggerDict):
            named = logging.getLogger(name)
            named.setLevel(saved_levels.get(name, logging.NOTSET))
