"""跨层一致性守护测试。

锁定三组易漂移的双源声明与零覆盖小面：schemas 枚举取值与 validators 白名单、
MCP 注册工具名与 impl ToolMetadata 工具名、路径相似建议与 CLI 端口解析的边界行为；
另含 loguru exc_info 关键字的全源码静态守护。新增取值或改名时两侧须同步，本文件
在各处失败即暴露漂移。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

import seedream_mcp
from seedream_mcp.tools.core.schemas import (
    BackgroundMode,
    GenerationToolType,
    OptimizePromptOptions,
    OutputFormat,
    ResponseFormat,
)
from seedream_mcp.utils.core.validators import (
    VALID_BACKGROUND_MODES,
    VALID_GENERATION_TOOL_TYPES,
    VALID_OPTIMIZE_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_RESPONSE_FORMATS,
    VALID_SIZE_PRESETS,
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


def test_output_format_enum_matches_validator_whitelist() -> None:
    """OutputFormat 枚举取值与 VALID_OUTPUT_FORMATS 一致，新增格式须两侧同步。"""
    assert {item.value for item in OutputFormat} == set(VALID_OUTPUT_FORMATS)


def test_optimize_mode_literal_matches_validator_whitelist() -> None:
    """OptimizePromptOptions.mode 的 Literal 取值与 VALID_OPTIMIZE_MODES 一致。"""
    from typing import get_args

    mode_annotation = OptimizePromptOptions.model_fields["mode"].annotation
    literal_values = {value for value in get_args(mode_annotation) if isinstance(value, str)}

    assert literal_values == set(VALID_OPTIMIZE_MODES)


def test_unknown_family_presets_match_validator_whitelist() -> None:
    """unknown 家族的档位全集与 VALID_SIZE_PRESETS 一致，新增档位须两侧同步。"""
    from seedream_mcp.utils.model.model_capabilities import (
        MODEL_CAPABILITIES,
        MODEL_FAMILY_UNKNOWN,
    )

    assert set(MODEL_CAPABILITIES[MODEL_FAMILY_UNKNOWN].allowed_presets) == set(VALID_SIZE_PRESETS)


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


def test_loguru_calls_never_pass_exc_info_keyword() -> None:
    """全源码不出现 loguru 调用的 exc_info= 关键字，堆栈统一经 logger.exception。

    loguru 把未知关键字交 str.format 后丢弃，exc_info=True 的堆栈从不落日志；
    logs.py 的标准库桥接以 logger.opt(exception=...) 转写，白名单豁免。
    """
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        relative = source_path.relative_to(package_root).as_posix()
        if relative == "utils/core/logs.py":
            continue
        source_text = source_path.read_text(encoding="utf-8")
        if re.search(r"\bexc_info\s*=", source_text):
            offenders.append(relative)

    assert offenders == []


# ==================== 零覆盖小面 ====================


def test_suggest_similar_paths_finds_close_names(tmp_path: Path) -> None:
    """相似路径建议按目标文件名子串匹配，无参调用返回空列表不扫描 CWD。"""
    from seedream_mcp.utils.io.io_path import suggest_similar_paths

    (tmp_path / "portrait.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "other.jpg").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 匹配方向为目标名是候选文件名的子串，覆盖「缺扩展名/少打字母」的手误形态
    suggestions = suggest_similar_paths("portrait", search_dirs=[str(tmp_path)])

    assert suggestions == [str(tmp_path / "portrait.png")]
    # 未提供搜索目录时不扫描任何目录，强制调用方显式给出边界
    assert suggest_similar_paths("portrait") == []


def test_cli_port_type_rejects_invalid_port() -> None:
    """CLI 端口解析拒绝非数字与超范围端口，接受合法端口。"""
    from seedream_mcp.cli import _port_type

    assert _port_type("8000") == 8000
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("not-a-port")
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("70000")
