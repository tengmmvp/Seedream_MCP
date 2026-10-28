"""跨层一致性守护测试。

锁定五组易漂移的双源声明与零覆盖小面：schemas 枚举取值与 validators 白名单、
能力表参考图上限与组图总数上限、MCP 注册工具名与 handlers ToolMetadata 工具名、
webapp 错误状态表与错误档案及错误码槽位取值、
路径相似建议与 CLI 端口解析的边界行为；另含 loguru exc_info 关键字、security
标记级别与环境变量键清洗前缀的全源码静态守护，download.py PermissionError
处理器归类守护，以及 repo-layout.md 仓库与 tests 结构契约的静态守护。新增取值
或改名时两侧须同步，本文件在各处失败即暴露漂移。
"""

from __future__ import annotations

import argparse
import ast
import functools
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import seedream_mcp
from _lib.generation_fixtures import make_generation_context
from _lib.no_param_tools import NO_PARAM_TOOLS
from conftest import HOST_ENV_SCRUB_PREFIXES
from seedream_mcp.tools.schemas import (
    BackgroundMode,
    GenerationToolType,
    OptimizePromptOptions,
    OutputFormat,
    ResponseFormat,
)
from seedream_mcp.tools.handlers._metadata import (
    IMAGE_TO_IMAGE,
    MULTI_IMAGE_FUSION,
    SEQUENTIAL_GENERATION,
    TEXT_TO_IMAGE,
)
from seedream_mcp.definitions import conventions
from seedream_mcp.definitions.conventions import MAX_SEQUENTIAL_TOTAL_IMAGES
from seedream_mcp.utils.core.validators import (
    VALID_BACKGROUND_MODES,
    VALID_GENERATION_TOOL_TYPES,
    VALID_OPTIMIZE_MODES,
    VALID_OUTPUT_FORMATS,
    VALID_RESPONSE_FORMATS,
    VALID_SIZE_PRESETS,
)
from seedream_mcp.utils.core import executors, sanitizers

# 生成工具元数据元组：三个守护测试共用，新增生成工具漏更任一测试即失败。
_GENERATION_TOOL_METADATA = (
    TEXT_TO_IMAGE,
    IMAGE_TO_IMAGE,
    MULTI_IMAGE_FUSION,
    SEQUENTIAL_GENERATION,
)

# 环境键读取豁免只收真实旁路：动态键站点解不出静态文本，收录模块相对路径加调用原文。
_ENV_KEY_READ_EXEMPTIONS = frozenset(
    {
        "config/_builder.py:os.getenv(env_name)",
        "config/sources.py:os.getenv(env_key)",
        "utils/io/path.py:os.getenv(env_var)",
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


def _is_environ_access(node: ast.AST) -> bool:
    """判定节点是否 os.environ 或 from os import environ 的裸名拼写。"""
    if isinstance(node, ast.Name):
        return node.id == "environ"
    return isinstance(node, ast.Attribute) and node.attr == "environ"


def _is_env_key_read(func: ast.AST) -> bool:
    """判定被调函数是否 os.getenv 或 os.environ.get，含 from os import 的裸名拼写。"""
    if isinstance(func, ast.Name):
        return func.id == "getenv"
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "getenv":
        return True
    return func.attr == "get" and _is_environ_access(func.value)


def _env_key_read_sites(source: str) -> list[tuple[str, str]]:
    """提取源码内全部环境键读取站点，含下标读取形态，返回 (读取原文, 键静态前缀)。"""
    sites: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_env_key_read(node.func):
            if node.args:
                prefix, _fully_static = _static_key_text(node.args[0])
            else:
                prefix = ""
            sites.append((ast.unparse(node), prefix))
        elif isinstance(node, ast.Subscript) and _is_environ_access(node.value):
            prefix, _fully_static = _static_key_text(node.slice)
            sites.append((ast.unparse(node), prefix))
    return sites


# PermissionError 及其内建超类拼写（OSError 连同 IOError/EnvironmentError 别名）：
# except 捕获任一或裸 except 即实际覆盖 PermissionError。
_PERMISSION_ERROR_COVERING_NAMES = frozenset(
    {"PermissionError", "OSError", "IOError", "EnvironmentError"}
)

# 宽捕获豁免只收非文件面真实旁路：DNS 解析兜底 OSError 不涉杀软文件持锁，
# 按终态域名失败处置是既有裁决。
_BROAD_CATCH_EXEMPTIONS = frozenset({"_resolve_public_ips_uncached:OSError"})


def _except_catches_permission_error(handler: ast.ExceptHandler) -> bool:
    """判定 except 捕获类型是否覆盖 PermissionError：裸 except、内建超类与元组逐项。"""
    caught = handler.type
    if caught is None:
        return True
    if isinstance(caught, ast.Name):
        return caught.id in _PERMISSION_ERROR_COVERING_NAMES
    return isinstance(caught, ast.Tuple) and any(
        isinstance(item, ast.Name) and item.id in _PERMISSION_ERROR_COVERING_NAMES
        for item in caught.elts
    )


def _permission_error_handler_classified(handler: ast.ExceptHandler) -> bool:
    """判定处理器体内是否出现三类归类形态之一：转可重试、作废路径、终态抛出。"""
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_raise_transient_file_lock":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "restart":
                return True
        if isinstance(node, ast.Raise) and node.exc is not None:
            rendered = ast.unparse(node.exc)
            if "DownloadError" in rendered and "权限拒绝" in rendered:
                return True
    return False


def _permission_error_receiving_handlers(
    tree: ast.Module,
) -> list[tuple[str, ast.ExceptHandler]]:
    """产出 (所属函数, 实际收到 PermissionError 的 except 处理器)。

    异常派发命中的是首个覆盖捕获：同 try 内先序兄弟已覆盖 PermissionError
    时，后序更宽捕获不再实际收到它；豁免登记的 (函数, 捕获原文) 不计入。
    """
    receivers: list[tuple[str, ast.ExceptHandler]] = []

    def visit(nodes: list[ast.stmt], owner: str) -> None:
        for stmt in nodes:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(stmt.body, stmt.name)
            elif isinstance(stmt, ast.Try):
                visit(stmt.body, owner)
                intercepted = False
                for handler in stmt.handlers:
                    if _except_catches_permission_error(handler):
                        exempt = (
                            handler.type is not None
                            and f"{owner}:{ast.unparse(handler.type)}" in _BROAD_CATCH_EXEMPTIONS
                        )
                        if not intercepted and not exempt:
                            receivers.append((owner, handler))
                        intercepted = True
                    visit(handler.body, owner)
                visit(stmt.orelse, owner)
                visit(stmt.finalbody, owner)
            else:
                for field in ("body", "orelse", "finalbody"):
                    children = getattr(stmt, field, None)
                    if isinstance(children, list):
                        visit(children, owner)

    visit(tree.body, "<module>")
    return receivers


def _unclassified_permission_error_handlers(source: str) -> list[str]:
    """提取实际收到 PermissionError 而未归类的 except 处理器，返回行号定位文案。"""
    return [
        f"第 {handler.lineno} 行 except 处理器"
        for _owner, handler in _permission_error_receiving_handlers(ast.parse(source))
        if not _permission_error_handler_classified(handler)
    ]


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
    from seedream_mcp.utils.model.capabilities import (
        MODEL_CAPABILITIES,
        MODEL_FAMILY_UNKNOWN,
    )

    assert set(MODEL_CAPABILITIES[MODEL_FAMILY_UNKNOWN].allowed_presets) == set(VALID_SIZE_PRESETS)


def test_family_reference_caps_fit_sequential_total_images() -> None:
    """能力表各家族 max_reference_images ≤ 组图总数上限 - 1，不留生成位的家族使组图错误指引自相矛盾。"""
    from seedream_mcp.utils.model.capabilities import MODEL_CAPABILITIES

    offenders = [
        f"{family}: {caps.max_reference_images}"
        for family, caps in MODEL_CAPABILITIES.items()
        if caps.max_reference_images > MAX_SEQUENTIAL_TOTAL_IMAGES - 1
    ]

    assert offenders == [], (
        f"家族参考图上限须不超过 {MAX_SEQUENTIAL_TOTAL_IMAGES - 1}（总数上限 "
        f"{MAX_SEQUENTIAL_TOTAL_IMAGES} 须留出至少一张生成位）: {offenders}"
    )


async def test_mcp_registered_tool_names_match_impl_metadata() -> None:
    """server 注册的 MCP 工具名与 handlers ToolMetadata、browse_images 字面量及无参工具名单的并集一致。

    任一侧改名会使 structuredContent.tool 与注册名静默错位，两侧字面量分布在不同
    模块，靠本断言锁定一致。
    """
    from seedream_mcp.server.runtime import mcp

    tools = await mcp.list_tools()
    registered = {tool.name for tool in tools}
    declared = {metadata.tool_name for metadata in _GENERATION_TOOL_METADATA}
    declared.add("browse_images")
    declared |= NO_PARAM_TOOLS

    assert declared == registered


def test_webapp_error_status_map_derives_from_profiles() -> None:
    """webapp 状态表与错误档案同源：档案码段键值逐条一致，档案外登记段不撞键。"""
    from seedream_mcp.utils.core.errors import iter_error_profiles
    from seedream_mcp.webapp import _responses

    extra_status = {error.type: error.http_status for error in _responses._ENDPOINT_OWN_ERRORS}
    profile_status = {profile.error_code: profile.http_status for profile in iter_error_profiles()}
    assert not (
        set(profile_status) & set(extra_status)
    ), "档案外登记与档案错误码撞键，档案状态被静默覆盖"
    assert (
        _responses.ERROR_TYPE_STATUS == profile_status | extra_status
    ), "状态表偏离档案派生与档案外登记的并集"


def test_webapp_error_status_merge_rejects_conflicting_codes() -> None:
    """状态表合并对撞键 fail-fast，防按合并顺序静默覆盖一侧状态。"""
    from seedream_mcp.webapp import _responses

    with pytest.raises(ValueError, match="internal_error"):
        _responses._merge_error_type_status({"internal_error": 500}, {"internal_error": 400})


def test_webapp_error_status_profile_segment_rejects_conflicting_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """档案段状态表对同 error_code 不同 http_status fail-fast，防推导静默取后值。"""
    from seedream_mcp.webapp import _responses

    monkeypatch.setattr(
        _responses,
        "iter_error_profiles",
        lambda: [
            SimpleNamespace(error_code="conflicting_code", http_status=502),
            SimpleNamespace(error_code="conflicting_code", http_status=500),
        ],
    )

    with pytest.raises(AssertionError):
        _responses._profile_error_status()


# 错误码槽位的 AST 提取面：受跟踪错误构造调用（tools 层）与字符串形态的错误响应
# （webapp 层）的定位实参、任意调用的 error_type 关键字实参与 error_type 形参缺省值，
# 字面量与裸名两种取值形态同面提取；WebErrorInfo 构造不在提取面内，其状态随定义携带。
_LITERAL_ERROR_CODE_CALL_ARGS = {
    "build_error_dict": 0,
    "build_error_structured": 1,
    "respond_structured_error": 1,
}


def _call_tail_name(node: ast.Call) -> str | None:
    """取调用目标的末段名（`_responses.respond_structured_error` 取 respond_structured_error）。"""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _parameter_default_pairs(args: ast.arguments) -> list[tuple[ast.arg, ast.expr | None]]:
    """形参与缺省值按位配对，覆盖位置参数与仅关键字参数两形态，无缺省为 None。"""
    positional = [*args.posonlyargs, *args.args]
    return [
        *zip(positional[len(positional) - len(args.defaults) :], args.defaults),
        *zip(args.kwonlyargs, args.kw_defaults),
    ]


def _error_type_slot_expressions(tree: ast.Module) -> list[ast.expr]:
    """收集源码内全部 error_type 槽位上的表达式节点，槽位形态见提取面注释。"""
    exprs: list[ast.expr] = []

    def add(node: ast.expr | None) -> None:
        if node is not None:
            exprs.append(node)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            index = _LITERAL_ERROR_CODE_CALL_ARGS.get(_call_tail_name(node) or "")
            if index is not None and len(node.args) > index:
                add(node.args[index])
            for keyword in node.keywords:
                if keyword.arg == "error_type":
                    add(keyword.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for param, default in _parameter_default_pairs(node.args):
                if param.arg == "error_type":
                    add(default)
    return exprs


def _error_type_literal_sites(tree: ast.Module) -> list[str]:
    """提取 error_type 槽位上的字符串字面量取值。"""
    return [
        expr.value
        for expr in _error_type_slot_expressions(tree)
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str)
    ]


def _error_type_name_sites(tree: ast.Module) -> list[str]:
    """提取 error_type 槽位上的裸名，definitions 常量与变量路由两类同名收录。"""
    return [expr.id for expr in _error_type_slot_expressions(tree) if isinstance(expr, ast.Name)]


def test_error_type_slot_values_registered() -> None:
    """error_type 槽位取值（字面量与 definitions 常量）须落在 webapp 状态表内，任一侧改名即漂移。"""
    from seedream_mcp.webapp import _responses

    known = set(_responses.ERROR_TYPE_STATUS)
    defined = vars(conventions)
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.glob("**/*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        offenders += [
            f"{source_path.name}: {literal}"
            for literal in _error_type_literal_sites(tree)
            if literal not in known
        ]
        # definitions 常量按取值比对登记；不可解析裸名为变量路由，与属性形态同样跳过
        offenders += [
            f"{source_path.name}: {name}"
            for name in _error_type_name_sites(tree)
            if name in defined and str(defined[name]) not in known
        ]

    assert offenders == [], f"错误码槽位取值未登记: {offenders}"


def test_error_type_literal_extraction_covers_slot_forms() -> None:
    """提取面覆盖定位实参、error_type 关键字实参与形参缺省三种字面量形态，裸名同面提取。"""
    source = (
        "def _fail(state, message, error_type='browse_failed'):\n"
        "    build_error_dict(error_type, message)\n"
        "    build_error_dict(BROWSE_FAILED_ERROR_TYPE, message)\n"
        "    build_error_dict('generation_failed', message)\n"
        "    build_error_structured(tool, 'model_info_failed', message)\n"
        "    respond_structured_error(tool, 'validation_error', message)\n"
        "    _fail(state, message, error_type='validation_error')\n"
    )
    tree = ast.parse(source)

    assert sorted(_error_type_literal_sites(tree)) == [
        "browse_failed",
        "generation_failed",
        "model_info_failed",
        "validation_error",
        "validation_error",
    ]
    assert _error_type_name_sites(tree) == ["error_type", "BROWSE_FAILED_ERROR_TYPE"]


def test_error_type_literal_extraction_skips_variable_and_carried_forms() -> None:
    """属性取码与展开传参不入提取面；变量路由入裸名面，非 definitions 常量不参与校验。"""
    source = (
        "code = compute()\n"
        "build_error_dict(code, message)\n"
        "build_error_structured(tool, profile.error_code, message)\n"
        "respond_structured_error(tool, *error_info)\n"
        "info = WebErrorInfo('endpoint_error', message, 400)\n"
    )
    tree = ast.parse(source)

    assert _error_type_literal_sites(tree) == []
    assert _error_type_name_sites(tree) == ["code"]


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
    标准库桥接读取 record.exc_info 属性并以 exception= 转写，不含该关键字形态。
    """
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        relative = source_path.relative_to(package_root).as_posix()
        source_text = source_path.read_text(encoding="utf-8")
        if re.search(r"\bexc_info\s*=", source_text):
            offenders.append(relative)

    assert offenders == []


def test_cpu_offload_threshold_single_definition_in_executors() -> None:
    """卸载尺寸阈值单点定义于 executors，sanitizers 侧出现同名定义即归属漂移。"""
    assert hasattr(executors, "CPU_OFFLOAD_SIZE_THRESHOLD")
    assert not hasattr(sanitizers, "CPU_OFFLOAD_SIZE_THRESHOLD")


def test_parallel_request_count_single_definition_in_conventions() -> None:
    """并行生成上限单点定义于 definitions/conventions，他处同名赋值即归属漂移。"""
    assert hasattr(conventions, "MAX_PARALLEL_REQUEST_COUNT")
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        relative = source_path.relative_to(package_root).as_posix()
        if relative == "definitions/conventions.py":
            continue
        source_text = source_path.read_text(encoding="utf-8")
        if re.search(r"^MAX_PARALLEL_REQUEST_COUNT\s*=", source_text, re.MULTILINE):
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
    """环境变量键读取站点的键静态前缀落在 conftest 清洗前缀内，动态键站点须登记豁免。"""
    package_root = Path(seedream_mcp.__file__).resolve().parent
    offenders: list[str] = []
    for source_path in sorted(package_root.rglob("*.py")):
        relative = source_path.relative_to(package_root).as_posix()
        for render, prefix in _env_key_read_sites(source_path.read_text(encoding="utf-8")):
            if prefix.startswith(HOST_ENV_SCRUB_PREFIXES):
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
        ("from os import environ\nenviron.get('SEEDREAM_E')", "SEEDREAM_E"),
        ("import os\nos.environ['ARK_F']", "ARK_F"),
        ("from os import environ\nenviron['SEEDREAM_G']", "SEEDREAM_G"),
        ("import os\nos.environ[name]", ""),
        ("import os\nos.getenv(name)", ""),
    ],
)
def test_env_key_site_extraction_covers_quote_and_composition_forms(
    source: str, expected_prefix: str
) -> None:
    """键提取对引号拼写、f-string/拼接与下标读取形态不敏感，纯动态键解出空前缀。"""
    sites = _env_key_read_sites(source)

    assert [prefix for _render, prefix in sites] == [expected_prefix]


def test_env_key_site_extraction_ignores_unrelated_reads() -> None:
    """非 environ 接收者的 .get 调用与下标读取不进入环境键扫描结果。"""
    source = (
        "values = {'SEEDREAM_X': '1'}\n"
        "item = values.get('SEEDREAM_X')\n"
        "session.get()\n"
        "entry = values['SEEDREAM_X']"
    )

    assert _env_key_read_sites(source) == []


def test_download_permission_error_handlers_are_classified() -> None:
    """download.py 实际收到 PermissionError 的处理器必须显式归类为可重试、作废路径或终态之一。

    重试循环对 PermissionError 按权限拒绝默认判永久失败，新增 finalize 步骤若
    捕获（含经 OSError 与裸 except 的宽捕获）后未显式归类，瞬时杀软持锁会失去
    重试自愈；先序兄弟已截获或三类形态任一出现即合规，非文件面宽捕获须登记豁免。
    """
    package_root = Path(seedream_mcp.__file__).resolve().parent
    source = (package_root / "utils" / "io" / "download.py").read_text(encoding="utf-8")

    offenders = _unclassified_permission_error_handlers(source)

    assert offenders == [], (
        "捕获 PermissionError 的处理器未三择一归类（转可重试、作废路径、终态抛出）: " f"{offenders}"
    )


def test_permission_error_classification_reports_inline_violation() -> None:
    """判定函数报出内联的未归类处理器，宽捕获计入，先序截获与豁免形态不误报。"""
    source = (
        "try:\n"
        "    replace(path)\n"
        "except (PermissionError, RetryableDownloadError):\n"
        "    resume.restart()\n"
        "try:\n"
        "    truncate(fd)\n"
        "except PermissionError as exc:\n"
        "    _raise_transient_file_lock('终局截齐', exc)\n"
        "try:\n"
        "    save(path)\n"
        "except PermissionError as exc:\n"
        "    raise DownloadError(f'权限拒绝，不可重试: {exc}') from exc\n"
        "try:\n"
        "    finalize(fd)\n"
        "except PermissionError:\n"
        "    pass\n"
        "try:\n"
        "    finalize(fd)\n"
        "except OSError as exc:\n"
        "    raise DownloadError(f'域名解析失败: {exc}') from exc\n"
        "try:\n"
        "    replace(path)\n"
        "except PermissionError as exc:\n"
        "    _raise_transient_file_lock('续传临时文件重开', exc)\n"
        "except OSError:\n"
        "    resume.fd = None\n"
        "    raise\n"
        "try:\n"
        "    truncate(fd)\n"
        "except:\n"
        "    pass\n"
        "def _resolve_public_ips_uncached():\n"
        "    try:\n"
        "        getaddrinfo(host)\n"
        "    except OSError as exc:\n"
        "        raise DownloadError(f'域名解析失败: {exc}') from exc\n"
    )

    offenders = _unclassified_permission_error_handlers(source)

    assert offenders == [
        "第 15 行 except 处理器",
        "第 19 行 except 处理器",
        "第 30 行 except 处理器",
    ]


def test_style_prompt_prefix_names_registered_tools() -> None:
    """风格预设文案点名的工具存在于 handlers 元数据，工具改名时文案须同步。"""
    from seedream_mcp.server import _STYLE_PROMPT_PREFIX

    declared = {metadata.tool_name for metadata in _GENERATION_TOOL_METADATA}
    named = set(re.findall(r"([a-z][a-z_]+) 工具", _STYLE_PROMPT_PREFIX))

    assert named, "文案未解析到工具名点名，形态可能已变"
    assert named <= declared, f"文案点名了未注册的工具: {sorted(named - declared)}"


def test_context_probe_keys_match_schema_fields() -> None:
    """context 的工具特有探测键须为某输入模型的真字段或声明的 property 访问器。"""
    from seedream_mcp.tools.pipeline import context as context_module
    from seedream_mcp.tools.schemas import (
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
        declared |= {name for name, member in vars(model).items() if isinstance(member, property)}

    assert (
        not probed - declared
    ), f"context 探测键不在任何输入模型字段或访问器中: {sorted(probed - declared)}"


# ==================== 零覆盖小面 ====================


def test_suggest_similar_paths_finds_close_names(tmp_path: Path) -> None:
    """相似路径建议按目标文件名子串匹配，无参调用返回空列表不扫描 CWD。"""
    from seedream_mcp.utils.io.scan import suggest_similar_paths

    (tmp_path / "portrait.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "other.jpg").write_bytes(b"\x89PNG\r\n\x1a\n")

    # 匹配方向为目标名是候选文件名的子串，覆盖「缺扩展名/少打字母」的手误形态
    suggestions = suggest_similar_paths("portrait", search_dirs=[str(tmp_path)])

    assert suggestions == [str(tmp_path / "portrait.png")]
    # 未提供搜索目录时不扫描任何目录，强制调用方显式给出边界
    assert suggest_similar_paths("portrait") == []


def test_cli_port_type_rejects_invalid_port() -> None:
    """CLI 端口解析拒绝非数字与超范围端口，接受合法端口。"""
    from seedream_mcp.server.cli import _port_type

    assert _port_type("8000") == 8000
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("not-a-port")
    with pytest.raises(argparse.ArgumentTypeError):
        _port_type("70000")


# ==================== 仓库结构守护（repo-layout.md D4） ====================

_SRC_ROOT = Path(seedream_mcp.__file__).resolve().parent

# S1 包根准入的封闭集合：身份文件＋七域包＋skills 数据目录。
_ALLOWED_ROOT_FILES = frozenset({"__init__.py", "__main__.py", "version.py", "py.typed"})
_ALLOWED_ROOT_DIRS = frozenset(
    {"client", "config", "definitions", "server", "tools", "utils", "webapp", "skills"}
)

# D1 跨包边白名单：源组 -> 允许目标组集合（version 与 definitions 作目标全局豁免）。
_ALLOWED_MODULE_DEPS: dict[str, frozenset[str]] = {
    "definitions": frozenset(),
    "utils.model": frozenset(),
    "utils.core": frozenset({"utils.model"}),
    "utils.io": frozenset({"utils.core"}),
    "utils.images": frozenset({"utils.core", "utils.io"}),
    "config": frozenset({"utils"}),
    "client": frozenset({"config", "utils"}),
    "tools.schemas": frozenset({"utils"}),
    "tools.outputs": frozenset({"utils"}),
    "tools.pipeline": frozenset({"client", "config", "tools.schemas", "tools.outputs", "utils"}),
    "tools.handlers": frozenset(
        {"tools.pipeline", "tools.schemas", "tools.outputs", "config", "utils"}
    ),
    "tools.runners": frozenset({"tools.handlers", "tools.schemas", "config", "utils.io"}),
    "server": frozenset({"tools", "config", "utils"}),
    "webapp": frozenset({"tools", "config", "utils"}),
    "seedream_mcp": frozenset({"client", "config", "server"}),
    "tools": frozenset({"tools.schemas", "tools.runners"}),
}

# 仅 TYPE_CHECKING 形态放行的边（模块级运行时导入仍禁止）。
_ALLOWED_TYPE_CHECKING_ONLY = frozenset({("server", "client"), ("tools.handlers", "client")})

# D3 函数级导入白名单（精确目标模块），新增须先在 repo-layout.md 登记再使用。
_ALLOWED_FUNCTION_IMPORTS = frozenset(
    {
        ("seedream_mcp.config._model", "seedream_mcp.config._builder"),
        ("seedream_mcp.server.runtime", "seedream_mcp.client"),
        ("seedream_mcp.server.runtime", "seedream_mcp.config"),
        ("seedream_mcp.server.runtime", "seedream_mcp.config.sources"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.images.thumbnail"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.download"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.files"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.path"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.save"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.scan"),
        ("seedream_mcp.server.runtime", "seedream_mcp.utils.io.storage"),
        ("seedream_mcp.server.transport", "seedream_mcp.server.runtime"),
        ("seedream_mcp.server.transport", "seedream_mcp.webapp"),
        ("seedream_mcp.server.transport", "seedream_mcp.webapp.constants"),
        ("seedream_mcp.utils.images.thumbnail", "seedream_mcp.utils.core.formats"),
        ("seedream_mcp.utils.io.files", "seedream_mcp.utils.core.logs"),
        ("seedream_mcp.webapp.context", "seedream_mcp.server"),
        ("seedream_mcp.webapp.routes", "seedream_mcp.server.runtime"),
        ("seedream_mcp.webapp.routes", "seedream_mcp.webapp.files"),
        ("seedream_mcp.webapp.routes", "seedream_mcp.webapp.gallery"),
        ("seedream_mcp.webapp.routes", "seedream_mcp.webapp.generate"),
        ("seedream_mcp.webapp.routes", "seedream_mcp.webapp.meta"),
    }
)

# S3 执行面三件套封闭集与装配支撑面白名单。
_TOOLS_EXEC_FACE = frozenset(
    {"tools.runners", "tools.schemas", "tools.outputs", "tools.outputs.channels"}
)
_SERVER_TOOLS_EXTRA = frozenset({"tools.pipeline", "tools.handlers.get_model_info"})

# S2 冻结面五门面 __all__ 快照，与实现逐字一致。
_FACADE_ALL_SNAPSHOTS = {
    "seedream_mcp": [
        "__version__",
        "SeedreamClient",
        "SeedreamConfig",
        "get_active_config",
        "get_global_config",
        "set_active_config",
        "mcp",
        "cli_main",
    ],
    "seedream_mcp.tools": [
        "run_browse_images",
        "run_get_model_info",
        "run_image_to_image",
        "run_multi_image_fusion",
        "run_sequential_generation",
        "run_text_to_image",
        "BrowseImagesInput",
        "ImageToImageInput",
        "MultiImageFusionInput",
        "SequentialGenerationInput",
        "TextToImageInput",
    ],
    "seedream_mcp.client": ["SeedreamClient"],
    "seedream_mcp.config": [
        "DEFAULT_HTTP_HOST",
        "DEFAULT_HTTP_PORT",
        "ENV_FAMILY_PREFIXES",
        "HTTP_AUTH_TOKEN_MIN_LENGTH",
        "LIFESPAN_KEY_CLIENT",
        "LIFESPAN_KEY_CONFIG",
        "LIFESPAN_KEY_DOWNLOAD_MANAGER",
        "PROJECT_ROOT",
        "SeedreamConfig",
        "active_request_state_keys",
        "build_config_from_sources",
        "drain_pending_build_warnings",
        "get_active_config",
        "get_global_config",
        "set_active_config",
    ],
    "seedream_mcp.server": [
        "SERVER_NAME",
        "SERVER_VERSION",
        "app_lifespan",
        "cli_main",
        "mcp",
        "models_info_resource",
        "server_info_resource",
        "workspace_roots_resource",
    ],
}


def _dep_group(module: str) -> str:
    """模块路径归并为 D1 判定组；入口垫片 __main__ 归并父包。"""
    if module == "seedream_mcp":
        return "seedream_mcp"
    rest = module[len("seedream_mcp.") :]
    if rest.endswith(".__main__") or rest == "__main__":
        rest = rest.rpartition(".__main__")[0] if "." in rest else ""
        if not rest:
            return "seedream_mcp"
    head, _, tail = rest.partition(".")
    if head in ("tools", "utils"):
        sub = tail.partition(".")[0]
        return f"{head}.{sub}" if sub else head
    return head


def _dep_allows(src_group: str, dst_group: str) -> bool:
    """D1 判定：细粒度直配，整包条目（utils/tools）展开覆盖全部子组。"""
    allowed = _ALLOWED_MODULE_DEPS.get(src_group, frozenset())
    if dst_group in allowed:
        return True
    if "utils" in allowed and dst_group.startswith("utils"):
        return True
    if "tools" in allowed and dst_group.startswith("tools"):
        return True
    return False


def _submodule_exists(prefix: str, name: str) -> bool:
    """判定 prefix.name 是否为源码树内真实子模块（from X import 名的歧义消解）。"""
    parts = prefix.split(".") + [name]
    candidate = _SRC_ROOT.joinpath(*parts[len("seedream_mcp".split(".")) :])
    return (candidate.with_suffix(".py")).exists() or (candidate / "__init__.py").exists()


def _resolve_relative(current: str, level: int, target: str | None, is_package: bool) -> str:
    parts = current.split(".")
    if not is_package:
        parts = parts[:-1]
    for _ in range(level - 1):
        parts = parts[:-1]
    prefix = ".".join(parts)
    return f"{prefix}.{target}" if target else prefix


@functools.cache
def _scan_module_edges() -> dict[str, dict[str, set[str]]]:
    """AST 扫描源码树，按模块产出三类导入边（runtime/type_checking/function）。

    会话内源码树静态，多处断言共用一份扫描结果。
    """
    edges: dict[str, dict[str, set[str]]] = {}
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if "webapp" in path.parts and "static" in path.parts:
            continue
        rel = path.relative_to(_SRC_ROOT.parent).with_suffix("")
        parts = list(rel.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        name = ".".join(parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        buckets: dict[str, set[str]] = {"runtime": set(), "type_checking": set(), "function": set()}

        def record(node: ast.Import | ast.ImportFrom, kind: str) -> None:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    buckets[kind].add(alias.name)
                return
            if node.level:
                prefix = _resolve_relative(name, node.level, None, is_package)
                if node.module:
                    target = f"{prefix}.{node.module}"
                    buckets[kind].add(target)
                elif _submodule_exists(prefix, node.names[0].name):
                    # from X import 名 的名恰为子模块时按子模块边，否则按包（符号）边。
                    for alias in node.names:
                        buckets[kind].add(f"{prefix}.{alias.name}")
                else:
                    buckets[kind].add(prefix)
                return
            target = node.module or name
            buckets[kind].add(target)

        def walk(nodes: list[ast.stmt], in_function: bool, in_tc: bool) -> None:
            for node in nodes:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if in_function:
                        record(node, "function")
                    else:
                        record(node, "type_checking" if in_tc else "runtime")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for stmt in ast.walk(node):
                        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                            record(stmt, "function")
                elif isinstance(node, ast.If) and not in_function:
                    is_tc = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                    walk(node.body, False, is_tc or in_tc)
                    walk(node.orelse, False, in_tc)
                elif isinstance(node, ast.Try):
                    walk(node.body, in_function, in_tc)
                    for handler in node.handlers:
                        walk(handler.body, in_function, in_tc)
                    walk(node.orelse, in_function, in_tc)
                    walk(node.finalbody, in_function, in_tc)
                else:
                    for field in ("body", "orelse", "finalbody"):
                        if hasattr(node, field) and isinstance(getattr(node, field), list):
                            walk(getattr(node, field), in_function, in_tc)

        walk(tree.body, False, False)
        edges[name] = buckets
    return edges


def _lazy_export_edges() -> set[tuple[str, str]]:
    """两个门面的 _LAZY_EXPORTS 字符串表解析入依赖图（D4-2）。

    门面对该表可能采用裸赋值或带注解赋值（ast.Assign / ast.AnnAssign）两种形态。
    """
    result: set[tuple[str, str]] = set()
    for facade, rel_path in (
        ("seedream_mcp", "__init__.py"),
        ("seedream_mcp.tools", "tools/__init__.py"),
    ):
        path = _SRC_ROOT / rel_path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                if not (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "_LAZY_EXPORTS"
                    and node.value is not None
                ):
                    continue
                values = [node.value]
            elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in node.targets
            ):
                values = [node.value]
            else:
                continue
            for value in values:
                table = ast.literal_eval(value)
                for _, (rel, _) in table.items():
                    result.add((facade, f"{facade}{rel}"))
    return result


def test_package_root_admission() -> None:
    """包根仅允许身份文件、七域包与 skills 数据目录驻留（S1）。"""
    for entry in sorted(_SRC_ROOT.iterdir()):
        if entry.name == "__pycache__":
            continue
        if entry.is_file():
            assert entry.name in _ALLOWED_ROOT_FILES, f"包根禁驻文件: {entry.name}"
        else:
            assert entry.name in _ALLOWED_ROOT_DIRS, f"包根禁驻目录: {entry.name}"


def test_module_dependency_edges_follow_d1() -> None:
    """模块级与 TYPE_CHECKING 跨包导入按 D1 白名单判定，version 与 definitions 作目标全局豁免。"""
    violations: list[str] = []
    for src, buckets in _scan_module_edges().items():
        src_group = _dep_group(src)
        for dst in buckets["runtime"] | buckets["type_checking"]:
            if (
                not dst.startswith("seedream_mcp")
                or dst == "seedream_mcp.version"
                or dst.startswith("seedream_mcp.definitions")
            ):
                continue
            dst_group = _dep_group(dst)
            if dst_group == src_group:
                continue
            pair = (src_group, dst_group)
            if dst in buckets["type_checking"] and dst not in buckets["runtime"]:
                if pair in _ALLOWED_TYPE_CHECKING_ONLY:
                    continue
            if not _dep_allows(src_group, dst_group):
                violations.append(f"{src} -> {dst}（{src_group} -> {dst_group}）")
    assert not violations, "D1 跨包边违规:\n" + "\n".join(sorted(violations))


def test_lazy_export_edges_follow_d1() -> None:
    """门面 _LAZY_EXPORTS 表解析出的边同样受 D1 约束。"""
    violations = []
    for src, dst in _lazy_export_edges():
        src_group = _dep_group(src)
        dst_group = _dep_group(dst)
        if dst_group == src_group:
            continue
        if not _dep_allows(src_group, dst_group):
            violations.append(f"{src} -> {dst}")
    assert not violations, f"门面延迟导出边违规: {sorted(violations)}"


def test_module_import_graph_acyclic() -> None:
    """模块级导入图（runtime＋TYPE_CHECKING＋门面延迟导出）无环（D2）。"""
    graph: dict[str, set[str]] = {}
    for src, buckets in _scan_module_edges().items():
        graph.setdefault(src, set()).update(
            d for d in buckets["runtime"] | buckets["type_checking"] if d.startswith("seedream_mcp")
        )
    for src, dst in _lazy_export_edges():
        graph.setdefault(src, set()).add(dst)

    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        if state.get(node) == 1:
            cycle = stack[stack.index(node) :] + [node]
            raise AssertionError(f"模块级导入环: {' -> '.join(cycle)}")
        if state.get(node) == 2:
            return
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            visit(nxt)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        visit(node)


def test_function_level_imports_within_whitelist() -> None:
    """函数级导入仅允许 D3 白名单登记的二元组（标准库与第三方不入判定）。"""
    actual: set[tuple[str, str]] = set()
    for src, buckets in _scan_module_edges().items():
        for dst in buckets["function"]:
            if dst.startswith("seedream_mcp"):
                actual.add((src, dst))
    unregistered = actual - _ALLOWED_FUNCTION_IMPORTS
    stale = _ALLOWED_FUNCTION_IMPORTS - actual
    assert not unregistered, f"未登记的函数级导入: {sorted(unregistered)}"
    assert not stale, f"白名单 stale 条目: {sorted(stale)}"


def test_import_module_confined_to_facades() -> None:
    """import_module 调用仅允许出现在根与 tools 两个门面（D4-2）。"""
    facades = {"__init__.py", "tools/__init__.py"}
    offenders = set()
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if "webapp" in path.parts and "static" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_bare = isinstance(node.func, ast.Name) and node.func.id == "import_module"
            is_qualified = (
                isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            )
            if is_bare or is_qualified:
                offenders.add(path.relative_to(_SRC_ROOT).as_posix())
    assert offenders <= facades, f"import_module 仅限门面使用: {sorted(offenders - facades)}"


def _tools_face(domain: str) -> set[str]:
    """收集域内全部模块对 tools 的导入目标，门面导入经 _LAZY_EXPORTS 展开。"""
    import seedream_mcp.tools as tools_facade

    face: set[str] = set()
    for src, buckets in _scan_module_edges().items():
        if not src.startswith(f"seedream_mcp.{domain}"):
            continue
        for dst in buckets["runtime"] | buckets["type_checking"] | buckets["function"]:
            if dst == "seedream_mcp.tools":
                for alias in tools_facade._LAZY_EXPORTS:
                    face.add("tools" + tools_facade._LAZY_EXPORTS[alias][0])
            elif dst.startswith("seedream_mcp.tools"):
                face.add("tools." + dst[len("seedream_mcp.tools.") :])
    return face


def test_webapp_tools_import_face_follows_s3() -> None:
    """webapp 对 tools 的导入集 ⊆ 执行面三件套封闭集（S3）。"""
    offenders = _tools_face("webapp") - _TOOLS_EXEC_FACE
    assert not offenders, f"webapp 越出执行面三件套: {sorted(offenders)}"


def test_server_tools_import_face_follows_s3() -> None:
    """server 对 tools 的导入集 ⊆ 执行面三件套 ∪ 装配支撑面白名单（S3）。"""
    offenders = _tools_face("server") - _TOOLS_EXEC_FACE - _SERVER_TOOLS_EXTRA
    assert not offenders, f"server 越出装配支撑面: {sorted(offenders)}"


def test_frozen_facade_paths_importable() -> None:
    """S2 冻结面五路径可导入（模块转包后路径保持）。"""
    import importlib

    for path in (
        "seedream_mcp",
        "seedream_mcp.client",
        "seedream_mcp.config",
        "seedream_mcp.server",
        "seedream_mcp.tools",
    ):
        assert importlib.import_module(path) is not None, path


def test_facade_all_matches_frozen_snapshots() -> None:
    """五门面 __all__ 与冻结面快照逐字一致（D4-4）。"""
    import seedream_mcp.client
    import seedream_mcp.config
    import seedream_mcp.server
    import seedream_mcp.tools

    modules = {
        "seedream_mcp": seedream_mcp,
        "seedream_mcp.tools": seedream_mcp.tools,
        "seedream_mcp.client": seedream_mcp.client,
        "seedream_mcp.config": seedream_mcp.config,
        "seedream_mcp.server": seedream_mcp.server,
    }
    for name, module in modules.items():
        assert (
            module.__all__ == _FACADE_ALL_SNAPSHOTS[name]
        ), f"{name}.__all__ 与快照漂移:\n  实际: {module.__all__}\n  快照: {_FACADE_ALL_SNAPSHOTS[name]}"


# ==================== tests 结构守护（repo-layout.md D4-5，随阶段 5 启用） ====================

_TESTS_ROOT = _SRC_ROOT.parents[1] / "tests"


def test_test_files_basename_unique() -> None:
    """test 文件全树 basename 唯一（N8，pytest prepend 模式的硬前提）。"""
    seen: dict[str, str] = {}
    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        rel = path.relative_to(_TESTS_ROOT).as_posix()
        assert path.name not in seen, f"basename 重复: {path.name}（{seen[path.name]} 与 {rel}）"
        seen[path.name] = rel


def test_conftest_only_at_tests_root() -> None:
    """conftest.py 仅存在于 tests 根，子目录不得再挂局部夹具层。"""
    offenders = [
        p.relative_to(_TESTS_ROOT).as_posix()
        for p in _TESTS_ROOT.rglob("conftest.py")
        if p.parent != _TESTS_ROOT
    ]
    assert not offenders, f"子目录 conftest 违规: {offenders}"


def test_tests_directory_structure() -> None:
    """六域与 contracts/e2e 子目录无 __init__.py，_lib 以包形态存在。"""
    for domain in ("client", "config", "server", "tools", "utils", "webapp", "contracts", "e2e"):
        assert not (_TESTS_ROOT / domain / "__init__.py").exists(), domain
    assert (_TESTS_ROOT / "_lib" / "__init__.py").exists()
