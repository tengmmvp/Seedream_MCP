"""配置默认值文档对账守护测试。

config 的 SeedreamConfig 字段默认值是默认值的单一数据源，经 ENV_DEFAULTS 以环境
变量名为键导出；.env.example 注释的「默认：X」是唯一文档镜像。本文件把镜像与
数据源的漂移变成测试强制：代码改默认值而文档未同步（或反向）立即变红。无法以
机械规则解析的键建立显式豁免清单并注明理由，豁免仅移出覆盖检查，豁免键一旦
标注默认值仍参与取值对账。另守护 .env.example 说明块以全角句号收束的形态规则。
"""

from __future__ import annotations

import re

import pytest

import seedream_mcp.config.sources as config_sources

# .env.example 注释行的「默认：」标注形态，冒号兼容全角。
_EXAMPLE_DEFAULT_PATTERN = re.compile(r"^#\s*默认[:：]\s*(.+)$")

# 标注取值边界：全角/半角括号、逗号、分号与句号，其后为说明文字不参与取值。
_VALUE_BOUNDARY_PATTERN = re.compile(r"[（(，,；;。]")

# 空值标注 token，对应字段默认 None（ENV_DEFAULTS 导出为空串）。
_EMPTY_VALUE_TOKENS = frozenset({"空", "未设置"})

# .env.example 侧无法机械对账的键及理由。
_EXAMPLE_EXEMPT: dict[str, str] = {
    "ARK_API_KEY": "必填字段无代码默认值，example 不标注默认",
    "SEEDREAM_DATA_ROOT": "无「默认：」标注行，回退行为在说明行描述，字段默认 None",
    "SEEDREAM_WORKSPACE_ROOT": "无「默认：」标注行，回退行为在说明行描述，字段默认 None",
    "SEEDREAM_HTTP_AUTH_TOKEN": "无「默认：」标注行，字段默认 None",
}

_DESCRIPTION_PREFIX = "# 说明："
_DESCRIPTION_TERMINATOR = "。"


def test_env_example_description_blocks_end_with_period() -> None:
    """含「说明：」的注释块，最后一个非列表注释行须以全角句号收束。

    句号统一规则：说明正文是完整句子，句尾一律句号。句间句号在多句说明中
    不可避免，行尾缺句号会形成句间加、句尾不加的混排。列表项（# - 开头，
    如尺寸档位清单）是值形态附属，不选为收束行；标注行（默认/可选值/
    获取方式/生成命令及命令字面量）位于块尾时同样按收束行校验。
    """
    example_text = config_sources.PROJECT_ROOT.joinpath(".env.example").read_text(encoding="utf-8")
    lines = example_text.splitlines()
    offenders: list[str] = []
    for index, raw in enumerate(lines):
        assignment = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", raw)
        if assignment is None:
            continue
        block_end = index
        block_start = block_end
        while block_start > 0 and lines[block_start - 1].lstrip().startswith("#"):
            block_start -= 1
        block = [line.strip() for line in lines[block_start:block_end]]
        has_description = any(line.startswith(_DESCRIPTION_PREFIX) for line in block)
        if not has_description:
            continue
        prose_lines = [line for line in block if not line.startswith("# - ")]
        last_comment = prose_lines[-1]
        if not last_comment.endswith(_DESCRIPTION_TERMINATOR):
            offenders.append(f"{assignment.group(1)}: {last_comment}")
    assert not offenders, "说明块末行未以全角句号收束:\n" + "\n".join(offenders)


def _parse_documented_value(raw: str) -> str:
    """把「默认：X」标注文本归一为可与 ENV_DEFAULTS 比较的取值。

    截断到首个边界标点取主体 token，空值 token 归空串，其余原样返回。
    """
    token = _VALUE_BOUNDARY_PATTERN.split(raw, maxsplit=1)[0].strip()
    if token in _EMPTY_VALUE_TOKENS:
        return ""
    return token


def _assert_annotations_match_defaults(
    annotations: dict[str, str], env_defaults: dict[str, str]
) -> None:
    """对账核心：未豁免键缺标注为覆盖缺口，已标注键取值与代码默认值不同为漂移。"""
    all_keys = set(env_defaults) | {"ARK_API_KEY"}
    exempt = set(_EXAMPLE_EXEMPT)
    missing = [key for key in sorted(all_keys - exempt) if key not in annotations]
    assert not missing, f".env.example 缺少默认值标注且未列入豁免清单: {missing}"

    drift: list[str] = []
    for env_key in sorted(all_keys):
        # 豁免键未标注时跳过对账，补标注后与普通键同口径参与。
        if env_key in exempt and env_key not in annotations:
            continue
        documented = _parse_documented_value(annotations[env_key])
        expected = env_defaults.get(env_key, "")
        if env_key == "SEEDREAM_MODEL_ID":
            documented = config_sources.normalize_model_selector(documented)
            expected = config_sources.normalize_model_selector(expected)
        if documented != expected:
            drift.append(f"{env_key}: 文档 {documented!r} != 代码 {expected!r}")
    assert not drift, ".env.example 默认值标注与代码默认值漂移:\n" + "\n".join(drift)


def test_env_example_default_annotations_match_config() -> None:
    """.env.example 各键「默认：」标注须与代码默认值一致。

    模型选择器两侧经别名展开后比较，文档允许写别名而代码存完整 Model ID。
    """
    _assert_annotations_match_defaults(_example_default_annotations(), config_sources.ENV_DEFAULTS)


def test_exempt_key_with_annotation_still_checked_for_drift() -> None:
    """豁免键一旦标注默认值即参与取值对账。"""
    with pytest.raises(AssertionError, match="SEEDREAM_DATA_ROOT"):
        _assert_annotations_match_defaults({"SEEDREAM_DATA_ROOT": "42"}, {"SEEDREAM_DATA_ROOT": ""})


def _example_default_annotations() -> dict[str, str]:
    """提取 .env.example 各键注释块中「默认：」标注的原始文本。

    标注行须位于赋值行上方连续的注释块内，空白行与赋值行都会结束当前块的归集；
    连续多条标注行时取最后一条。
    """
    example_text = config_sources.PROJECT_ROOT.joinpath(".env.example").read_text(encoding="utf-8")
    annotations: dict[str, str] = {}
    pending: str | None = None
    for raw in example_text.splitlines():
        stripped = raw.strip()
        if not stripped:
            pending = None
            continue
        if stripped.startswith("#"):
            match = _EXAMPLE_DEFAULT_PATTERN.match(stripped)
            if match is not None:
                pending = match.group(1)
            continue
        assignment = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", stripped)
        if assignment is not None and pending is not None:
            annotations[assignment.group(1)] = pending
            pending = None
    return annotations
