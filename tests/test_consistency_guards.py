"""跨层一致性守护测试。

锁定三组易漂移的双源声明与零覆盖小面：schemas 枚举取值与 validators 白名单、
MCP 注册工具名与 impl ToolMetadata 工具名、路径相似建议与 CLI 端口解析的边界行为；
另含 loguru exc_info 关键字、security 标记级别与环境变量键清洗前缀的全源码静态
守护。新增取值或改名时两侧须同步，本文件在各处失败即暴露漂移。
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

import pytest

import seedream_mcp
from _generation_fixtures import make_generation_context
from conftest import HOST_ENV_SCRUB_PREFIXES
from seedream_mcp.tools.core.schemas import (
    BackgroundMode,
    GenerationToolType,
    OptimizePromptOptions,
    OutputFormat,
    ResponseFormat,
)
from seedream_mcp.tools.impl._metadata import (
    IMAGE_TO_IMAGE,
    MULTI_IMAGE_FUSION,
    SEQUENTIAL_GENERATION,
    TEXT_TO_IMAGE,
)
from seedream_mcp.utils.core.validators import (
    VALID_BACKGROUND_MODES,
    VALID_GENERATION_TOOL_TYPES,
    VALID_OPTIMIZE_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_RESPONSE_FORMATS,
    VALID_SIZE_PRESETS,
)

# 生成工具元数据元组：三个守护测试共用，新增生成工具漏更任一测试即失败。
_GENERATION_TOOL_METADATA = (
    TEXT_TO_IMAGE,
    IMAGE_TO_IMAGE,
    MULTI_IMAGE_FUSION,
    SEQUENTIAL_GENERATION,
)

# 环境键读取豁免只收真实旁路：静态键收录键原文，动态键站点解不出静态文本、收录
# 模块相对路径加调用原文。
_ENV_KEY_READ_EXEMPTIONS = frozenset(
    {
        "config.py:os.getenv(env_name)",
        "_config_sources.py:os.getenv(env_key)",
        "utils/io/io_path.py:os.getenv(env_var)",
    }
)


def _static_key_text(node: ast.AST) -> tuple[str, bool]:
    """取键实参的静态文本与是否整体静态，首个动态部分截断后续文本。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left_text, left_static = _static_key_text(node.left)
        right_text, right_static = _static_key_text(node.right)
        if not left_static:
            return left_text, False
        return left_text + right_text, right_static
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                return "".join(parts), False
            parts.append(value.value)
        return "".join(parts), True
    return "", False


def _is_env_key_read(func: ast.AST) -> bool:
    """判定被调函数是否 os.getenv 或 os.environ.get，含 from os import 的裸名拼写。"""
    if isinstance(func, ast.Name):
        return func.id == "getenv"
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return True
    return (
        func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
    )


def _env_key_read_sites(source: str) -> list[tuple[str, str]]:
    """提取源码内全部环境键读取站点，返回 (调用原文, 键静态前缀)。"""
    sites: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_env_key_read(node.func):
            continue
        if node.args:
            prefix, _fully_static = _static_key_text(node.args[0])
        else:
            prefix = ""
        sites.append((ast.unparse(node), prefix))
    return sites


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

    tools = await mcp.list_tools()
    registered = {tool.name for tool in tools}
    declared = {metadata.tool_name for metadata in _GENERATION_TOOL_METADATA}
    declared.add("browse_images")

    assert declared == registered


def test_start_log_placeholders_match_builder_arity() -> None:
    """各 ToolMetadata 的开始日志模板 {} 占位个数与 builder 参数个数一致。

    双源字面量分布两处，loguru 惰性格式化使错位只在落日志时打错误不抛异常，
    故按个数锁定并实际执行一次格式化兜底。
    """
    # sequential 的 max_images 取非 None 值，覆盖 builder 读取的全部上下文字段
    context = make_generation_context(prompt="生成提示词", max_images=4)

    for metadata in _GENERATION_TOOL_METADATA:
        values = metadata.start_log_values_builder(context)
        placeholders = metadata.start_log_message.count("{}")
        assert len(values) == placeholders, (
            f"{metadata.tool_name} 开始日志模板 {placeholders} 个占位 != "
            f"builder {len(values)} 个参数"
        )
        metadata.start_log_message.format(*values)


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


def test_security_marked_logs_are_warning_level() -> None:
    """security 标记的日志调用均为 warning，低于该级别的标记会被 sink 级别门丢弃。

    级别门取配置与 WARNING 的较小值，_sink_filter 的放行仅对过门记录生效。
    """
    package_root = Path(seedream_mcp.__file__).resolve().parent
    pattern = re.compile(r"\.bind\(security=True\)\s*\.\s*(\w+)\(")
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        source_text = source_path.read_text(encoding="utf-8")
        for match in pattern.finditer(source_text):
            if match.group(1) != "warning":
                relative = source_path.relative_to(package_root).as_posix()
                offenders.append(f"{relative}: {match.group(0)}")

    assert offenders == []


def test_env_key_reads_covered_by_host_scrub_prefixes() -> None:
    """getenv 系调用的键静态前缀落在 conftest 清洗前缀内，动态键站点须登记豁免。"""
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        relative = source_path.relative_to(package_root).as_posix()
        for render, prefix in _env_key_read_sites(source_path.read_text(encoding="utf-8")):
            if prefix.startswith(HOST_ENV_SCRUB_PREFIXES):
                continue
            if prefix in _ENV_KEY_READ_EXEMPTIONS:
                continue
            if f"{relative}:{render}" in _ENV_KEY_READ_EXEMPTIONS:
                continue
            offenders.append(f"{relative}: {render}")

    assert offenders == [], (
        "环境变量键读取不在 conftest 清洗前缀内，须同步 HOST_ENV_SCRUB_PREFIXES 或登记豁免: "
        f"{offenders}"
    )


@pytest.mark.parametrize(
    ("source", "expected_prefix"),
    [
        ('import os\nos.getenv("SEEDREAM_A")', "SEEDREAM_A"),
        ("import os\nos.getenv('ARK_B')", "ARK_B"),
        ('import os\nos.getenv(f"SEEDREAM_{name}")', "SEEDREAM_"),
        ('import os\nos.getenv("SEEDREAM_" + name)', "SEEDREAM_"),
        ("from os import getenv\ngetenv('SEEDREAM_C')", "SEEDREAM_C"),
        ("import os\nos.environ.get('ARK_D', '')", "ARK_D"),
        ("import os\nos.getenv(name)", ""),
    ],
)
def test_env_key_site_extraction_covers_quote_and_composition_forms(
    source: str, expected_prefix: str
) -> None:
    """键提取对引号拼写与 f-string/拼接形态不敏感，纯动态键解出空前缀。"""
    sites = _env_key_read_sites(source)

    assert [prefix for _render, prefix in sites] == [expected_prefix]


def test_env_key_site_extraction_ignores_unrelated_get_calls() -> None:
    """非 environ 接收者的 .get 调用不进入环境键扫描结果。"""
    source = "values = {'SEEDREAM_X': '1'}\nitem = values.get('SEEDREAM_X')\nsession.get()"

    assert _env_key_read_sites(source) == []


def test_style_prompt_prefix_names_registered_tools() -> None:
    """风格预设文案点名的工具存在于 impl 元数据，工具改名时文案须同步。"""
    from seedream_mcp.server import _STYLE_PROMPT_PREFIX

    declared = {metadata.tool_name for metadata in _GENERATION_TOOL_METADATA}
    named = set(re.findall(r"([a-z][a-z_]+) 工具", _STYLE_PROMPT_PREFIX))

    assert named, "文案未解析到工具名点名，形态可能已变"
    assert named <= declared, f"文案点名了未注册的工具: {sorted(named - declared)}"


def test_context_probe_keys_match_schema_fields() -> None:
    """context 的工具特有字段探测键须为某输入模型的真字段，改名漂移即失败。"""
    from seedream_mcp.tools.core import context as context_module
    from seedream_mcp.tools.core.schemas import (
        ImageToImageInput,
        MultiImageFusionInput,
        SequentialGenerationInput,
        TextToImageInput,
    )

    source = Path(context_module.__file__).read_text(encoding="utf-8")
    probed = set(re.findall(r'(?:getattr|hasattr)\(params, "([a-z_]+)"', source))
    assert probed, "context 未解析到字段探测点，探测形态可能已变"

    declared: set[str] = set()
    for model in (
        TextToImageInput,
        ImageToImageInput,
        MultiImageFusionInput,
        SequentialGenerationInput,
    ):
        declared |= set(model.model_fields)

    assert (
        not probed - declared
    ), f"context 探测键不在任何输入模型字段中: {sorted(probed - declared)}"


# ==================== 零覆盖小面 ====================


def test_suggest_similar_paths_finds_close_names(tmp_path: Path) -> None:
    """相似路径建议按目标文件名子串匹配，无参调用返回空列表不扫描 CWD。"""
    from seedream_mcp.utils.io.io_scan import suggest_similar_paths

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
