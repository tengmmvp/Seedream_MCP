"""配置默认值文档对账守护测试。

config 的 SeedreamConfig 字段默认值是默认值的单一数据源，经 ENV_DEFAULTS 以环境
变量名为键导出；.env.example 注释的「默认：X」与三语 README 环境变量块行尾注释
中的默认值标注共四份文档镜像该数据源。本文件把镜像与数据源的漂移变成测试强制：
代码改默认值而任一处文档未同步（或反向）立即变红。无法以机械规则解析的标注形态
建立显式豁免清单并注明理由，新增键或新增标注未归类时同样失败。
"""

from __future__ import annotations

import re

import pytest

import seedream_mcp.config as config_module
from _readme_helpers import _env_block

README_FILES = ("README.md", "README.en.md", "README.zh-TW.md")

# .env.example 注释行的「默认：」标注形态，冒号兼容全角。
_EXAMPLE_DEFAULT_PATTERN = re.compile(r"^#\s*默认[:：]\s*(.+)$")

# 标注取值边界：全角/半角括号、逗号、分号与句号，其后为说明文字不参与取值。
_VALUE_BOUNDARY_PATTERN = re.compile(r"[（(，,；;。]")

# 空值标注 token，对应字段默认 None（ENV_DEFAULTS 导出为空串）。
_EMPTY_VALUE_TOKENS = frozenset({"空", "未设置"})

# .env.example 侧无法机械对账的键及理由；新增键的标注缺解析规则时不允许静默落入此处。
_EXAMPLE_EXEMPT: dict[str, str] = {
    "ARK_API_KEY": "必填字段无代码默认值，example 不标注默认",
    "LOG_FILE": "标注的是未设置时日志系统的推导路径，字段默认 None",
    "SEEDREAM_AUTO_SAVE_BASE_DIR": "标注的是未设置时按工作区根推导的目录，字段默认 None",
    "SEEDREAM_WORKSPACE_ROOT": "无「默认：」标注行，回退行为在说明行描述，字段默认 None",
    "SEEDREAM_HTTP_AUTH_TOKEN": "无「默认：」标注行，字段默认 None",
}

# README 注释中带数值+容量单位默认值的键，token 换算字节后与代码默认值对账。
_README_SIZE_DEFAULT_KEYS = frozenset(
    {
        "SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE",
        "SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES",
        "SEEDREAM_HTTP_MAX_BODY_SIZE",
        "SEEDREAM_PREPARE_CACHE_MAX_BYTES",
        "SEEDREAM_STREAM_BUFFER_MAX_SIZE",
        "SEEDREAM_STREAM_CHUNK_SIZE",
    }
)

# README 注释中以开/关措辞标注布尔默认值的键。
_README_BOOL_DEFAULT_KEYS = frozenset(
    {"SEEDREAM_AUTO_SAVE_FSYNC", "SEEDREAM_PREVIEW_ENABLED", "SEEDREAM_WEB_ENABLED"}
)

# README 注释含默认措辞但无法解析为取值的键及理由。
_README_MARKER_EXEMPT: dict[str, str] = {
    "ARK_BASE_URL": "注释以文字描述默认端点而非取值 token，取值经赋值行与 example 对账",
    "SEEDREAM_ALLOW_HTTP_BASE_URL": "「默认拒绝」为行为描述而非该键的布尔取值",
    "LOG_FILE": "注释标注未设置时的推导路径，字段默认 None",
    "SEEDREAM_AUTO_SAVE_BASE_DIR": "注释标注按工作区根推导的目录，字段默认 None",
    "SEEDREAM_REQUEST_STATE_KEYS": "「SDK 默认进程临时密钥」描述的是留空时 SDK 的行为而非该键取值，字段默认 None",
}

# 各 README 语言的默认措辞探测，用于锁定「含默认标注的键集合」。
_DEFAULT_WORD_PATTERN = {
    "README.md": re.compile(r"默认"),
    "README.zh-TW.md": re.compile(r"預設"),
    "README.en.md": re.compile(r"default", re.IGNORECASE),
}

# 数值+容量单位 token：措辞后紧跟数字与 MB/GB。
_SIZE_TOKEN_PATTERN = {
    "README.md": re.compile(r"默认\s*(\d+(?:\.\d+)?)\s*(MB|GB)"),
    "README.zh-TW.md": re.compile(r"預設\s*(\d+(?:\.\d+)?)\s*(MB|GB)"),
    "README.en.md": re.compile(r"default\s+(?:to\s+)?(\d+(?:\.\d+)?)\s*(MB|GB)", re.IGNORECASE),
}

# 布尔 token：中文为措辞后接开/关，英文支持 default on/off 与 on/off by default 两种语序。
_BOOL_TOKEN_PATTERN = {
    "README.md": re.compile(r"默认\s*(开启|关闭)"),
    "README.zh-TW.md": re.compile(r"預設\s*(開啟|關閉)"),
    "README.en.md": re.compile(r"default\s+(on|off)|(on|off)\s+by\s+default", re.IGNORECASE),
}

_BOOL_TOKENS = {
    "开启": True,
    "关闭": False,
    "開啟": True,
    "關閉": False,
    "on": True,
    "off": False,
}

_BYTE_UNITS = {"MB": 1024**2, "GB": 1024**3}


def _parse_documented_value(raw: str) -> str:
    """把「默认：X」标注文本归一为可与 ENV_DEFAULTS 比较的取值。

    截断到首个边界标点取主体 token，空值 token 归空串，其余原样返回。
    """
    token = _VALUE_BOUNDARY_PATTERN.split(raw, maxsplit=1)[0].strip()
    if token in _EMPTY_VALUE_TOKENS:
        return ""
    return token


def test_env_example_default_annotations_match_config() -> None:
    """.env.example 各键「默认：」标注须与代码默认值一致。

    未标注默认且未豁免的键为覆盖缺口；标注解析结果与 ENV_DEFAULTS 不同的键为
    漂移。模型选择器两侧经别名展开后比较，文档允许写别名而代码存完整 Model ID。
    """
    annotations = _example_default_annotations()
    all_keys = set(config_module.ENV_DEFAULTS) | {"ARK_API_KEY"}
    exempt = set(_EXAMPLE_EXEMPT)
    missing = [key for key in sorted(all_keys - exempt) if key not in annotations]
    assert not missing, f".env.example 缺少默认值标注且未列入豁免清单: {missing}"

    drift: list[str] = []
    for env_key in sorted(all_keys - exempt):
        documented = _parse_documented_value(annotations[env_key])
        expected = config_module.ENV_DEFAULTS[env_key]
        if env_key == "SEEDREAM_MODEL_ID":
            documented = config_module.normalize_model_selector(documented)
            expected = config_module.normalize_model_selector(expected)
        if documented != expected:
            drift.append(f"{env_key}: 文档 {documented!r} != 代码 {expected!r}")
    assert not drift, ".env.example 默认值标注与代码默认值漂移:\n" + "\n".join(drift)


def _example_default_annotations() -> dict[str, str]:
    """提取 .env.example 各键注释块中「默认：」标注的原始文本。

    标注行须位于赋值行上方连续的注释块内，空白行与赋值行都会结束当前块的归集；
    连续多条标注行时取最后一条。
    """
    example_text = config_module.PROJECT_ROOT.joinpath(".env.example").read_text(encoding="utf-8")
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


def _readme_env_comments(name: str) -> dict[str, str]:
    """返回 README 环境变量配置块中各赋值键的行尾注释文本，无注释的键不入表。"""
    comments: dict[str, str] = {}
    for line in _env_block(name).lines:
        stripped = line.strip()
        if "#" not in stripped:
            continue
        head, _, comment = stripped.partition("#")
        assignment = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", head.strip())
        if assignment is not None:
            comments[assignment.group(1)] = comment
    return comments


@pytest.mark.parametrize("readme_name", README_FILES)
def test_readme_env_default_tokens_match_config(readme_name: str) -> None:
    """README 环境变量块注释中的默认值 token 须与代码默认值一致。

    仅对账带可解析 token（容量数值、开/关布尔）的键；措辞无法解析为取值的键须
    出现在豁免清单，否则失败，防止新标注形式静默绕过对账。
    """
    word_pattern = _DEFAULT_WORD_PATTERN[readme_name]
    size_pattern = _SIZE_TOKEN_PATTERN[readme_name]
    bool_pattern = _BOOL_TOKEN_PATTERN[readme_name]
    drift: list[str] = []
    untriaged: list[str] = []
    for env_key, comment in _readme_env_comments(readme_name).items():
        if env_key not in config_module.ENV_DEFAULTS or not word_pattern.search(comment):
            continue
        if env_key in _README_SIZE_DEFAULT_KEYS:
            match = size_pattern.search(comment)
            assert (
                match is not None
            ), f"{readme_name} 的 {env_key} 注释未解析到默认容量 token: {comment!r}"
            documented = int(float(match.group(1))) * _BYTE_UNITS[match.group(2).upper()]
            expected = int(config_module.ENV_DEFAULTS[env_key])
            if documented != expected:
                drift.append(f"{env_key}: 文档 {match.group(0)!r} != 代码 {expected} 字节")
        elif env_key in _README_BOOL_DEFAULT_KEYS:
            match = bool_pattern.search(comment)
            assert (
                match is not None
            ), f"{readme_name} 的 {env_key} 注释未解析到布尔默认 token: {comment!r}"
            token = next(group for group in match.groups() if group is not None)
            documented = _BOOL_TOKENS[token.lower()]
            expected = config_module.parse_bool(config_module.ENV_DEFAULTS[env_key])
            if documented != expected:
                drift.append(f"{env_key}: 文档 {documented} != 代码 {expected}")
        elif env_key not in _README_MARKER_EXEMPT:
            untriaged.append(env_key)
    assert (
        not untriaged
    ), f"{readme_name} 注释含默认措辞的键未纳入对账或豁免: {sorted(set(untriaged))}"
    assert not drift, f"{readme_name} 默认值标注与代码默认值漂移:\n" + "\n".join(drift)


@pytest.mark.parametrize("readme_name", README_FILES)
def test_readme_default_marker_keys_are_expected(readme_name: str) -> None:
    """README 环境变量块中含默认措辞的键集合须与既定分类全等。

    新增默认标注、措辞改写导致探测失配或删除标注都会先在此失败，逼维护者为
    新形态归类：可解析 token 进对账集合，否则进豁免清单。
    """
    word_pattern = _DEFAULT_WORD_PATTERN[readme_name]
    marker_keys = {
        env_key
        for env_key, comment in _readme_env_comments(readme_name).items()
        if word_pattern.search(comment)
    }
    expected = _README_SIZE_DEFAULT_KEYS | _README_BOOL_DEFAULT_KEYS | set(_README_MARKER_EXEMPT)
    assert marker_keys == expected, (
        f"{readme_name} 含默认措辞的键集合与既定分类漂移:\n"
        f"  多出: {sorted(marker_keys - expected)}\n"
        f"  缺少: {sorted(expected - marker_keys)}"
    )
