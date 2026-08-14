"""`.env.example` 与 config 实际读取的环境变量键双向守护测试。

example 中出现的全部 SEEDREAM_/ARK_/LOG_ 键（注释与赋值行都算）须与配置构建实际
读取的键集合一致：example 多出的键属文档残留应删除，config 读取而 example 未登记
的键属文档遗漏。集合来源以 config 实际读取的全部 env 为准，即 _FIELD_ENV_MAP 的
值集合与显式读取的 ARK_API_KEY。
"""

from __future__ import annotations

import re
from pathlib import Path

import seedream_mcp.config as config_module

# 环境变量键形态：前缀限定 SEEDREAM_/ARK_/LOG_，键名由大写字母、数字、下划线组成。
# 前缀目录行（如 “- SEEDREAM_ 服务行为”）后接空白不构成完整键，不会被命中。
_ENV_KEY_PATTERN = re.compile(r"\b(?:SEEDREAM|ARK|LOG)_[A-Z0-9_]+")


def _example_env_keys() -> set[str]:
    """提取 .env.example 全文（注释与赋值行）出现的全部环境变量键。"""
    example_path = Path(config_module.__file__).resolve().parent.parent / ".env.example"
    content = example_path.read_text(encoding="utf-8")
    return set(_ENV_KEY_PATTERN.findall(content))


def _config_read_env_keys() -> set[str]:
    """config 配置构建实际读取的全部环境变量键。

    _FIELD_ENV_MAP 覆盖经 _pick_config_value 读取的字段键；api_key 为必填字段无
    env metadata，由 _build_config_from_sources 显式读取 ARK_API_KEY，单独并入。
    """
    return set(config_module._FIELD_ENV_MAP.values()) | {"ARK_API_KEY"}


def test_env_example_keys_are_all_read_by_config() -> None:
    """.env.example 中的键须全部被 config 读取，多余键为文档残留应删除。"""
    residue = _example_env_keys() - _config_read_env_keys()

    assert not residue, f".env.example 存在 config 未读取的残留键: {sorted(residue)}"


def test_config_env_keys_are_all_documented_in_example() -> None:
    """config 读取的键须全部在 .env.example 登记，缺失即文档遗漏。"""
    missing = _config_read_env_keys() - _example_env_keys()

    assert not missing, f".env.example 漏登记 config 读取的键: {sorted(missing)}"
