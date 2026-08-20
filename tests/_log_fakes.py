"""按级别记录格式化消息的 loguru logger 测试替身。

供 test_client_refactor、test_logging_setup 与 test_workspace_roots_scope 复用，
替代各文件自持的近实现替身。opt(lazy=True) 的 callable 实参在记录时求值，若不
求值，lambda 对象本身进入格式化字符串，会掩盖 _summarize_prompt 等求值路径
未运行的回归。
"""

from __future__ import annotations

from typing import Any


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
