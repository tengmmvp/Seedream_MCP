"""跨层一致性守护测试。

锁定三组易漂移的双源声明与零覆盖小面：schemas 枚举取值与 validators 白名单、
MCP 注册工具名与 impl ToolMetadata 工具名、路径相似建议与 CLI 端口解析的边界行为。
新增取值或改名时两侧须同步，本文件在各处失败即暴露漂移。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from seedream_mcp.tools.core.schemas import BackgroundMode, GenerationToolType, ResponseFormat
from seedream_mcp.utils.core.validators import (
    VALID_BACKGROUND_MODES,
    VALID_GENERATION_TOOL_TYPES,
    VALID_RESPONSE_FORMATS,
)


def test_response_format_enum_matches_validator_whitelist() -> None:
    """ResponseFormat 枚举取值与 validators 白名单一致，新增格式须两侧同步。"""
    assert {item.value for item in ResponseFormat} == set(VALID_RESPONSE_FORMATS)


def test_generation_tool_type_enum_matches_validator_whitelist() -> None:
    """GenerationToolType 枚举取值与 VALID_GENERATION_TOOL_TYPES 一致。"""
    assert {item.value for item in GenerationToolType} == set(VALID_GENERATION_TOOL_TYPES)


def test_background_mode_enum_matches_validator_whitelist() -> None:
    """BackgroundMode 枚举取值与 VALID_BACKGROUND_MODES 一致，新增取值须两侧同步。"""
    assert {item.value for item in BackgroundMode} == set(VALID_BACKGROUND_MODES)


async def test_mcp_registered_tool_names_match_impl_metadata() -> None:
    """server 注册的 MCP 工具名与 impl ToolMetadata 声明一致。

    任一侧改名会使 structuredContent.tool 与注册名静默错位，两侧字面量分布在不同
    模块，靠本断言锁定一致。
    """
    from seedream_mcp.resources import mcp
    from seedream_mcp.tools.impl._common import (
        IMAGE_TO_IMAGE,
        MULTI_IMAGE_FUSION,
        SEQUENTIAL_GENERATION,
        TEXT_TO_IMAGE,
    )

    tools = await mcp.list_tools()
    registered = {tool.name for tool in tools}
    declared = {
        metadata.tool_name
        for metadata in (TEXT_TO_IMAGE, IMAGE_TO_IMAGE, MULTI_IMAGE_FUSION, SEQUENTIAL_GENERATION)
    }
    declared.add("browse_images")

    assert declared == registered


# ==================== 零覆盖小面 ====================


def test_suggest_similar_paths_finds_close_names(tmp_path: Path) -> None:
    """相似路径建议按目标文件名子串匹配，无参调用返回空列表不扫描 CWD。"""
    from seedream_mcp.utils.io.io_path import suggest_similar_paths

    (tmp_path / "portrait.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "other.jpg").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 匹配方向为目标名是候选文件名的子串，覆盖"缺扩展名/少打字母"的手误形态
    suggestions = suggest_similar_paths("portrait", search_dirs=[str(tmp_path)])

    assert suggestions == [str(tmp_path / "portrait.png")]
    # 未提供搜索目录时不扫描任何目录，强制调用方显式给出边界
    assert suggest_similar_paths("portrait") == []


def test_log_function_call_wraps_sync() -> None:
    """日志装饰器对同步函数透传参数与返回值；异步覆盖见下方专门用例。"""
    from seedream_mcp.utils.core.logs import log_function_call

    @log_function_call
    def sync_fn(value: int) -> int:
        return value * 2

    assert sync_fn(21) == 42


async def test_log_function_call_wraps_async() -> None:
    from seedream_mcp.utils.core.logs import log_function_call

    @log_function_call
    async def async_fn(value: int) -> int:
        return value + 1

    assert await async_fn(1) == 2


@pytest.mark.slow
def test_log_function_call_signature_survives_mypy(tmp_path: Path) -> None:
    """mypy 对装饰后方法的签名精确穿透，参数含 Any 的异步方法不退化为 Any。

    装饰器曾以 overload 加 Awaitable 分支声明，mypy 对签名含 Any 的异步函数做
    约束求解时将 ParamSpec 擦除为 (*Any, **Any) -> Any，使直接 return 装饰方法
    结果的代码触发 no-any-return。本用例在严格模式下编译最小片段并断言零告警，
    防止装饰器声明回退到会触发擦除的形态。

    本用例需起 mypy 子进程并以 --strict 编译，为全包最重的单用例，标记 slow；
    本地迭代可用 ``pytest -m "not slow" --basetemp=./.pytest-tmp`` 排除以加速，
    CI 保持默认全量运行不排除。
    """
    pytest.importorskip("mypy")

    snippet = tmp_path / "typing_guard.py"
    snippet.write_text(
        "from typing import Any\n"
        "\n"
        "from seedream_mcp.utils.core.logs import log_function_call\n"
        "\n"
        "\n"
        "class Guard:\n"
        "    @log_function_call\n"
        "    async def fetch(\n"
        "        self, key: str, meta: dict[str, Any] | None = None\n"
        "    ) -> dict[str, Any]:\n"
        "        return {}\n"
        "\n"
        "\n"
        "async def call(guard: Guard) -> dict[str, Any]:\n"
        '    return await guard.fetch(key="k")\n',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(snippet)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=Path(__file__).resolve().parent.parent,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_cli_port_type_rejects_invalid_port() -> None:
    """CLI 端口解析拒绝非数字与超范围端口，接受合法端口。"""
    from seedream_mcp.cli import _port_type

    assert _port_type("8000") == 8000
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("not-a-port")
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("70000")
