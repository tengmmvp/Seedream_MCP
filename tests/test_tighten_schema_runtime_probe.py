"""_tighten_flat_tool_schemas 运行期 SDK 私有路径探测测试。

私有属性缺失时函数记录一条明确错误并跳过收紧，不抛异常不阻断启动；探测只在
SDK 升级改动私有面时触发，正常路径由 test_server_lifespan 的私有面守护覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import seedream_mcp.server as server


class _CaptureLogger:
    """捕获 error 与 warning 调用的 loguru 替身，按 loguru 模板格式化参数。"""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str, *args: object) -> None:
        self.errors.append(message.format(*args) if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.warnings.append(message.format(*args) if args else message)


class _FakeTool:
    """记录 parameters 是否被收紧改写的工具替身，fn_metadata 形态由用例定制。"""

    def __init__(self, fn_metadata: Any = None) -> None:
        self.parameters: dict[str, Any] = {}
        if fn_metadata is not None:
            self.fn_metadata = fn_metadata


class _FakeToolManager:
    """按名字返回同一工具替身的工具管理器。"""

    def __init__(self, tool: _FakeTool | None) -> None:
        self._tool = tool

    def get_tool(self, name: str) -> _FakeTool | None:
        del name
        return self._tool


def _install_fake_mcp(
    monkeypatch: pytest.MonkeyPatch, capture: _CaptureLogger, tool_manager: Any
) -> None:
    """以替身接管 server 模块的 mcp 与 logger，隔离真实注册面。"""
    monkeypatch.setattr(server, "logger", capture)
    monkeypatch.setattr(server, "mcp", SimpleNamespace(_tool_manager=tool_manager))


def test_tighten_skips_silently_when_tool_manager_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mcp 对象缺少 _tool_manager 属性时记录一条错误并整体跳过，不抛异常。"""
    capture = _CaptureLogger()
    monkeypatch.setattr(server, "logger", capture)
    monkeypatch.setattr(server, "mcp", object())

    server._tighten_flat_tool_schemas()

    assert len(capture.errors) == 1
    assert "mcp._tool_manager" in capture.errors[0]
    assert "inputSchema 收紧被跳过" in capture.errors[0]
    assert "additionalProperties" in capture.errors[0]
    assert "守护测试将失败" in capture.errors[0]


def test_tighten_skips_when_tool_fn_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工具缺少 fn_metadata 属性时记录一条错误并跳过，不改写任何工具。"""
    capture = _CaptureLogger()
    tool = _FakeTool()
    _install_fake_mcp(monkeypatch, capture, _FakeToolManager(tool))

    server._tighten_flat_tool_schemas()

    assert len(capture.errors) == 1
    assert "fn_metadata" in capture.errors[0]
    assert "inputSchema 收紧被跳过" in capture.errors[0]
    assert tool.parameters == {}


def test_tighten_skips_when_fn_metadata_arg_model_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fn_metadata 存在但 arg_model 为 None 时同样判定私有面失效并跳过。"""
    capture = _CaptureLogger()
    tool = _FakeTool(fn_metadata=SimpleNamespace(arg_model=None))
    _install_fake_mcp(monkeypatch, capture, _FakeToolManager(tool))

    server._tighten_flat_tool_schemas()

    assert len(capture.errors) == 1
    assert "fn_metadata.arg_model" in capture.errors[0]
    assert tool.parameters == {}
