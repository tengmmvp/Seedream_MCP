"""按级别记录格式化消息的 loguru logger 测试替身与真实日志的捕获器。

RecordingLogger 供 test_browse_tool_result、test_client_refactor、
test_download_manager_security、test_logging_setup、
test_parallel_generation_tools、test_workspace_roots_scope、
test_validation_prompt、test_tighten_schema_runtime_probe 与
test_prepare_cache_single_flight 复用，替代各文件自持的近实现替身。
opt(lazy=True) 的 callable 实参在记录时求值，若不求值，lambda 对象本身进入
格式化字符串，会掩盖 _summarize_prompt 等求值路径未运行的回归。
capture_loguru_messages 捕获进程级真实 loguru logger 的指定级别消息，供
config_builder、find_images_directory 与 io_path_polish 等断言服务端告警。
capture_loguru_records 同机制捕获记录对象本身，供按记录字段过滤的断言复用。
preserved_loguru_globals 放行块内对进程级 loguru sink 与 root logging
全局的改写、退出时恢复，供 test_logging_setup、test_cli_log_init_failure
与 test_call_id_logging 复用。capturing_call_id_records 装配 call_id 日志配置
并捕获记录对象，直接供 test_logging_setup、test_server_transport_options、
test_transport_middleware_assembly 与 test_webapp_file_api 复用，
另经 conftest 日志捕获类 fixture 供 test_call_id_propagation、
test_call_id_dispatch_boundary 与 test_image_preview 复用。
stage_call_ids 按关键词取记录的 call_id 列表。
is_real_call_id 判定 call_id 是否为真实形态（非占位符）。
assert_single_shared_real_call_id 断言两组关键词各恰命中一条记录
且共享同一真实 call_id。line_with 取文件日志中含关键词的首行。
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from seedream_mcp.utils.core.logs import CALL_ID_PLACEHOLDER, get_logger, setup_logging


class RecordingLogger:
    """按级别记录格式化消息的 loguru logger 替身。

    Attributes:
        debug_messages: 格式化后的 debug 消息列表。
        info_messages: 完成 lazy 实参求值与模板格式化后的 info 消息列表。
        warnings: 格式化后的 warning 消息列表。
        errors: 格式化后的 error 消息列表。
        opt_kwargs: 每次 opt 调用携带的关键字参数，无参调用不记录。
        add_kwargs: 每次 add 调用携带的关键字参数。
    """

    def __init__(self) -> None:
        self.debug_messages: list[str] = []
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

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self._record(self.debug_messages, message, args)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self._record(self.info_messages, message, args)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self._record(self.warnings, message, args)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        del kwargs
        self._record(self.errors, message, args)


@contextmanager
def _capture_loguru_into(
    records: list[Any], level: str, payload: Callable[[Any], Any]
) -> Iterator[None]:
    """把进程级真实 loguru logger 的指定级别记录经 payload 转换后捕获进 records。"""
    handler_id = get_logger().add(lambda message: records.append(payload(message)), level=level)
    try:
        yield
    finally:
        try:
            get_logger().remove(handler_id)
        except ValueError:
            # sink 已被块内无参 remove 整体拆除时无物可移。
            pass


@contextmanager
def capture_loguru_messages(records: list[str], level: str = "WARNING") -> Iterator[None]:
    """把进程级真实 loguru logger 的指定级别格式化消息捕获进 records。"""
    with _capture_loguru_into(records, level, str):
        yield


@contextmanager
def capture_loguru_records(records: list[dict[str, Any]], level: str = "DEBUG") -> Iterator[None]:
    """把进程级真实 loguru logger 的指定级别记录对象捕获进 records。"""
    with _capture_loguru_into(records, level, lambda message: message.record):
        yield


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
        logger = get_logger()
        logger.remove()
        logger.add(sys.stderr)
        # configure 对 patcher=None 不生效，以空 patcher 中和块内设置的 patcher。
        logger.configure(patcher=lambda record: None)
        root.handlers = root_handlers
        root.setLevel(root_level)
        for name in list(logging.root.manager.loggerDict):
            named = logging.getLogger(name)
            named.setLevel(saved_levels.get(name, logging.NOTSET))


# capturing_call_id_records 的活动层数，重入守卫据此判定嵌套。
_call_id_capture_depth = 0


@contextmanager
def capturing_call_id_records(records: list[Any], level: str = "DEBUG") -> Iterator[None]:
    """装配 call_id 日志配置并捕获记录对象，供按 record extra 断言 call_id。

    record extra 的 call_id 由 setup_logging 装配的 patcher 注入，捕获器须与其
    嵌套才可见。InterceptHandler 桥接 SDK stdlib 日志依赖 force_standard_logging=True。
    行级渲染由 test_call_id_logging 覆盖。不可重入：内层 setup_logging 的无参
    logger.remove() 会剥离外层捕获 sink，嵌套进入即抛 AssertionError。
    """
    global _call_id_capture_depth
    if _call_id_capture_depth:
        raise AssertionError(
            "capturing_call_id_records 不可重入：内层 setup_logging 的无参 "
            "logger.remove() 会剥离外层捕获 sink，外层 records 将为空、"
            "断言以误导性计数失败；应单次捕获并取最低级别，断言按记录级别过滤。"
        )
    _call_id_capture_depth += 1
    try:
        with preserved_loguru_globals():
            setup_logging(
                level, enable_console=False, enable_file=False, force_standard_logging=True
            )
            with capture_loguru_records(records, level=level):
                yield
    finally:
        _call_id_capture_depth -= 1


_CALL_ID_SHAPE = re.compile(r"[0-9a-fA-F]{12}")


def is_real_call_id(value: str) -> bool:
    """对 call_id 原值做形状校验：12 位十六进制且非占位符。

    不收紧到 uuid 前缀来源判定——uuid4().hex[:12] 之外的合法形态会误判为非真实。
    """
    if value == CALL_ID_PLACEHOLDER:
        return False
    return _CALL_ID_SHAPE.fullmatch(value) is not None


def stage_call_ids(records: list[Any], keyword: str) -> list[str]:
    """取消息含关键词的记录的 call_id 列表，空列表即该阶段日志未出现。"""
    return [record["extra"]["call_id"] for record in records if keyword in record["message"]]


def assert_single_shared_real_call_id(
    records: list[Any], first_keyword: str, second_keyword: str
) -> None:
    """断言两组关键词各恰命中一条记录且共享同一真实 call_id。"""
    first_ids = stage_call_ids(records, first_keyword)
    second_ids = stage_call_ids(records, second_keyword)
    assert len(first_ids) == 1, f"关键词 {first_keyword!r} 的记录应恰一条，实为 {len(first_ids)}"
    assert len(second_ids) == 1, f"关键词 {second_keyword!r} 的记录应恰一条，实为 {len(second_ids)}"
    assert is_real_call_id(first_ids[0]), f"关键词 {first_keyword!r} 的 call_id 非真实形态"
    assert first_ids == second_ids, "两组关键词的 call_id 不相等"


# ==================== 文件日志断言辅助 ====================


def line_with(log_file: Path, keyword: str) -> str:
    """取日志文件中首条含关键词的行，缺失时以断言消息给出文件行数。"""
    lines = log_file.read_text(encoding="utf-8").splitlines()
    matched = [line for line in lines if keyword in line]
    assert matched, f"日志中未找到关键词 {keyword!r}（共 {len(lines)} 行）"
    return matched[0]
