"""`.env.example` 与 config 实际读取的环境变量键双向守护测试。

example 中出现的全部 SEEDREAM_/ARK_ 键须与 config 实际读取的键集合一致，
注释与赋值行都计入，多出为文档残留、缺失为文档遗漏。另守护 README 环境变量
配置节直接引用 .env.example 而非复述配置，以及 docs/samples/
claude_desktop_config.json 样本 env 键集为 config 读取键集的子集。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import seedream_mcp.config as config_module

# 环境变量键形态：前缀限定 SEEDREAM_/ARK_，键名由大写字母、数字、下划线组成。
# 形如「- SEEDREAM_ 服务行为」的前缀目录行后接空白，不构成完整键，不会被命中。
_ENV_KEY_PATTERN = re.compile(r"\b(?:SEEDREAM|ARK)_[A-Z0-9_]+")

# 配置复述的判定形态：环境变量键紧跟等号的赋值行。改名迁移说明等正文提及键名
# 但不构成赋值，不算复述。
_ENV_ASSIGNMENT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:SEEDREAM|ARK)_[A-Z0-9_]+=")

# README 键集守护的基准文件。
_BASE_README = "README.md"

# README 环境变量配置节的标题锚点。
_README_CONFIG_HEADING = "## ⚙️ 环境变量配置"


def _repo_root() -> Path:
    """返回仓库根目录，即 config 包所在目录的上一级。"""
    return Path(config_module.__file__).resolve().parent.parent


def _example_path() -> Path:
    """返回仓库根目录下的 .env.example 路径。"""
    return _repo_root() / ".env.example"


def _example_env_keys() -> set[str]:
    """提取 .env.example 全文出现的全部环境变量键，注释与赋值行都计入。"""
    content = _example_path().read_text(encoding="utf-8")
    return set(_ENV_KEY_PATTERN.findall(content))


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


def _readme_config_section() -> str:
    """提取 README.md 环境变量配置节的正文，到下一同级标题为止。"""
    text = (_repo_root() / _BASE_README).read_text(encoding="utf-8")
    start = text.index(_README_CONFIG_HEADING) + len(_README_CONFIG_HEADING)
    remainder = text[start:]
    next_heading = remainder.find("\n## ")
    return remainder if next_heading < 0 else remainder[:next_heading]


def test_env_example_keys_are_all_read_by_config() -> None:
    """.env.example 中的键须全部被 config 读取，多余键为文档残留应删除。"""
    residue = _example_env_keys() - _config_read_env_keys()

    assert not residue, f".env.example 存在 config 未读取的残留键: {sorted(residue)}"


def test_config_env_keys_are_all_documented_in_example() -> None:
    """config 读取的键须全部在 .env.example 登记，缺失即文档遗漏。"""
    missing = _config_read_env_keys() - _example_env_keys()

    assert not missing, f".env.example 漏登记 config 读取的键: {sorted(missing)}"


def test_readme_config_section_references_env_example() -> None:
    """README 环境变量配置节直接引用 .env.example，不得复述配置键。

    配置的唯一文档镜像为 .env.example，README 复述会在每次配置变更时引入
    同步负担；引用缺失或重新长出键复述时在此失败。
    """
    section = _readme_config_section()

    assert ".env.example" in section, "README 环境变量配置节缺少指向 .env.example 的引用"
    restated = sorted(set(_ENV_ASSIGNMENT_PATTERN.findall(section)))
    assert not restated, f"README 环境变量配置节复述了配置键，应仅引用 .env.example: {restated}"


def test_desktop_sample_env_keys_are_all_read_by_config() -> None:
    """claude_desktop_config.json 的 env 键须全部被 config 读取。

    子集关系成立即可，样本允许只登记部分键；config 键改名而样本未同步时在此失败。
    """
    unknown = _desktop_sample_env_keys() - _config_read_env_keys()

    assert (
        not unknown
    ), f"docs/samples/claude_desktop_config.json 存在 config 未读取的 env 键: {sorted(unknown)}"
