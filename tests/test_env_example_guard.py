"""`.env.example` 与 config 实际读取的环境变量键双向守护测试。

example 中出现的全部 SEEDREAM_/ARK_/LOG_ 键（注释与赋值行都算）须与配置构建实际
读取的键集合一致：example 多出的键属文档残留应删除，config 读取而 example 未登记
的键属文档遗漏。集合来源以 config 实际读取的全部 env 为准，即 _FIELD_ENV_MAP 的
值集合与显式读取的 ARK_API_KEY。

另一守护维度为 README 与 example 的键集关系：README.md 环境变量配置块的键集须覆盖
example 全部实际赋值的功能键，example 登记功能键而 README 未同步时失败。

用户面样本 docs/samples/claude_desktop_config.json 是第三处配置键声明点，其
mcpServers 各条目 env 的键集须为 config 实际读取键集的子集，键名漂移时失败。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import seedream_mcp.config as config_module

from _readme_helpers import _env_block

# 环境变量键形态：前缀限定 SEEDREAM_/ARK_/LOG_，键名由大写字母、数字、下划线组成。
# 前缀目录行（如 “- SEEDREAM_ 服务行为”）后接空白不构成完整键，不会被命中。
_ENV_KEY_PATTERN = re.compile(r"\b(?:SEEDREAM|ARK|LOG)_[A-Z0-9_]+")

# .env.example 实际赋值行形态，行首即为键名与等号，注释行以 # 开头不会命中。
_EXAMPLE_ASSIGNMENT_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")

# README 键集守护的基准文件，键集须与 example 功能键全等或为其超集。
_BASE_README = "README.md"


def _repo_root() -> Path:
    """返回仓库根目录，即 config 包所在目录的上一级。"""
    return Path(config_module.__file__).resolve().parent.parent


def _example_path() -> Path:
    """返回仓库根目录下的 .env.example 路径。"""
    return _repo_root() / ".env.example"


def _example_env_keys() -> set[str]:
    """提取 .env.example 全文（注释与赋值行）出现的全部环境变量键。"""
    content = _example_path().read_text(encoding="utf-8")
    return set(_ENV_KEY_PATTERN.findall(content))


def _example_assigned_keys() -> set[str]:
    """提取 .env.example 实际赋值行的功能键集合，注释行中的键不计入。"""
    keys: set[str] = set()
    for raw in _example_path().read_text(encoding="utf-8").splitlines():
        match = _EXAMPLE_ASSIGNMENT_PATTERN.match(raw.strip())
        if match is not None:
            keys.add(match.group(1))
    return keys


def _config_read_env_keys() -> set[str]:
    """config 配置构建实际读取的全部环境变量键。

    _FIELD_ENV_MAP 覆盖经 _pick_config_value 读取的字段键；api_key 为必填字段无
    env metadata，由 _build_config_from_sources 显式读取 ARK_API_KEY，单独并入。
    """
    return set(config_module._FIELD_ENV_MAP.values()) | {"ARK_API_KEY"}


def _desktop_sample_env_keys() -> set[str]:
    """提取 claude_desktop_config.json 中 mcpServers 各条目 env 的键集合。"""
    sample_path = _repo_root() / "docs" / "samples" / "claude_desktop_config.json"
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for server in payload.get("mcpServers", {}).values():
        keys.update(server.get("env", {}))
    return keys


def test_env_example_keys_are_all_read_by_config() -> None:
    """.env.example 中的键须全部被 config 读取，多余键为文档残留应删除。"""
    residue = _example_env_keys() - _config_read_env_keys()

    assert not residue, f".env.example 存在 config 未读取的残留键: {sorted(residue)}"


def test_config_env_keys_are_all_documented_in_example() -> None:
    """config 读取的键须全部在 .env.example 登记，缺失即文档遗漏。"""
    missing = _config_read_env_keys() - _example_env_keys()

    assert not missing, f".env.example 漏登记 config 读取的键: {sorted(missing)}"


def test_readme_env_block_covers_example_assigned_keys() -> None:
    """README.md 环境变量配置块的键集须覆盖 .env.example 全部功能键。

    基准为简体 README.md，键集与 example 功能键全等或为其超集皆可；配置块定位
    复用 _readme_helpers 的 _env_block 锚点逻辑，避免两处提取实现漂移。
    """
    readme_keys = set(_ENV_KEY_PATTERN.findall("\n".join(_env_block(_BASE_README).lines)))
    missing = _example_assigned_keys() - readme_keys

    assert not missing, f"README.md 环境变量配置块缺少 .env.example 登记的功能键: {sorted(missing)}"


def test_desktop_sample_env_keys_are_all_read_by_config() -> None:
    """claude_desktop_config.json 的 env 键须全部被 config 读取。

    样本是用户直接复制的配置声明点，config 键改名而样本未同步时在此失败，
    子集关系成立即可，样本允许只登记部分键。
    """
    unknown = _desktop_sample_env_keys() - _config_read_env_keys()

    assert (
        not unknown
    ), f"docs/samples/claude_desktop_config.json 存在 config 未读取的 env 键: {sorted(unknown)}"
