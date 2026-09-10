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

import seedream_mcp._config_sources as config_sources

# 环境变量键形态：前缀限定 SEEDREAM_/ARK_，键名由大写字母、数字、下划线组成。
# 形如「- SEEDREAM_ 服务行为」的前缀目录行后接空白，不构成完整键，不会被命中。
_ENV_KEY_PATTERN = re.compile(r"\b(?:SEEDREAM|ARK)_[A-Z0-9_]+")

# 配置复述的判定形态：环境变量键紧跟等号的赋值行。改名迁移说明等正文提及键名
# 但不构成赋值，不算复述。
_ENV_ASSIGNMENT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:SEEDREAM|ARK)_[A-Z0-9_]+=")

# README 键集守护的基准文件。
_BASE_README = "README.md"

# README 环境变量配置节的标题锚点，三语各一条。
_README_CONFIG_HEADINGS = {
    "README.md": "## 🔑 环境变量配置",
    "README.en.md": "## 🔑 Environment Variables",
    "README.zh-TW.md": "## 🔑 環境變數設定",
}


def _repo_root() -> Path:
    """返回仓库根目录，即 config 包所在目录的上一级。"""
    return Path(config_sources.__file__).resolve().parent.parent


def _example_path() -> Path:
    """返回仓库根目录下的 .env.example 路径。"""
    return _repo_root() / ".env.example"


def _example_env_keys() -> set[str]:
    """提取 .env.example 全文出现的全部环境变量键，注释与赋值行都计入。"""
    content = _example_path().read_text(encoding="utf-8")
    return set(_ENV_KEY_PATTERN.findall(content))


def _config_read_env_keys() -> set[str]:
    """config 配置构建实际读取的全部环境变量键。

    _FIELD_ENV_MAP 覆盖经 _pick_config_value 读取的字段键；无 env metadata 的
    必填与显式读取键从 _NON_METADATA_FIELD_ENV 派生，新增字段自动纳入对账。
    """
    return set(config_sources._FIELD_ENV_MAP.values()) | set(
        config_sources._NON_METADATA_FIELD_ENV.values()
    )


def _desktop_sample_env_keys() -> set[str]:
    """提取 claude_desktop_config.json 中 mcpServers 各条目 env 的键集合。"""
    sample_path = _repo_root() / "docs" / "samples" / "claude_desktop_config.json"
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for server in payload.get("mcpServers", {}).values():
        keys.update(server.get("env", {}))
    return keys


def _readme_config_section(readme_name: str = _BASE_README) -> str:
    """提取指定 README 环境变量配置节的正文，到下一同级标题为止。"""
    text = (_repo_root() / readme_name).read_text(encoding="utf-8")
    heading = _README_CONFIG_HEADINGS[readme_name]
    start = text.index(heading) + len(heading)
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
    """三语 README 环境变量配置节直接引用 .env.example，不得复述配置键。

    配置的唯一文档镜像为 .env.example，README 复述会在每次配置变更时引入
    同步负担；引用缺失或重新长出键复述时在此失败。
    """
    for readme_name in _README_CONFIG_HEADINGS:
        section = _readme_config_section(readme_name)
        assert (
            ".env.example" in section
        ), f"{readme_name} 环境变量配置节缺少指向 .env.example 的引用"
        restated = sorted(set(_ENV_ASSIGNMENT_PATTERN.findall(section)))
        assert (
            not restated
        ), f"{readme_name} 环境变量配置节复述了配置键，应仅引用 .env.example: {restated}"


def test_desktop_sample_env_keys_are_all_read_by_config() -> None:
    """claude_desktop_config.json 的 env 键须全部被 config 读取。

    子集关系成立即可，样本允许只登记部分键；config 键改名而样本未同步时在此失败。
    """
    unknown = _desktop_sample_env_keys() - _config_read_env_keys()

    assert (
        not unknown
    ), f"docs/samples/claude_desktop_config.json 存在 config 未读取的 env 键: {sorted(unknown)}"


# ==================== docker-compose 环境变量对账 ====================

# compose environment 条目形态：- KEY=${KEY} 或 - KEY=${KEY:-DEFAULT}。
_COMPOSE_ENV_ENTRY_PATTERN = re.compile(r"^\s*-\s+([A-Z0-9_]+)=\$\{\1(?::-(.*))?\}\s*$")

# compose 默认值与 config 默认不同的部署专属值及理由。
_COMPOSE_DEFAULT_OVERRIDES = {
    "SEEDREAM_DATA_ROOT": "容器内挂载位置 /app",
    "SEEDREAM_WORKSPACE_ROOT": "容器内挂载位置 /app",
    "SEEDREAM_MODEL_ID": "别名形态，validate 展开后与完整 ID 等价",
}


def _compose_env_entries() -> dict[str, str | None]:
    """解析 docker-compose.yml 的 environment 条目为键到默认值映射。"""
    entries: dict[str, str | None] = {}
    in_environment = False
    for line in (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        if line.strip() == "environment:":
            in_environment = True
            continue
        if in_environment:
            matched = _COMPOSE_ENV_ENTRY_PATTERN.match(line)
            if matched:
                entries[matched.group(1)] = matched.group(2)
                continue
            if line and not line.startswith(" "):
                break
    return entries


def test_compose_env_keys_match_config_read_keys() -> None:
    """compose environment 键集与 config 读取键集完全一致。

    compose 全量透传配置，多出为残留、缺失会使容器部署静默沿用旧默认值。
    """
    compose_keys = set(_compose_env_entries())
    config_keys = _config_read_env_keys() | {"ARK_API_KEY"}

    assert compose_keys == config_keys, (
        f"compose 与 config 键集不一致: "
        f"compose 多出 {sorted(compose_keys - config_keys)}, "
        f"compose 缺失 {sorted(config_keys - compose_keys)}"
    )


def test_compose_env_defaults_match_config_defaults() -> None:
    """compose 条目的回退默认值与 config 默认值一致，部署专属值豁免登记。"""
    mismatches: list[str] = []
    for key, raw_compose_default in _compose_env_entries().items():
        if key in _COMPOSE_DEFAULT_OVERRIDES or key == "ARK_API_KEY":
            continue
        if key not in config_sources.ENV_DEFAULTS:
            continue
        # compose 的空回退与 config 可选字段的空串默认同为未配置形态，归一比对。
        compose_default: str | None = raw_compose_default or None
        config_default: str | None = config_sources.ENV_DEFAULTS[key] or None
        if compose_default != config_default:
            mismatches.append(f"{key}: compose={compose_default!r} config={config_default!r}")

    assert not mismatches, f"compose 默认值与 config 不一致: {mismatches}"


def test_example_model_alias_list_matches_model_aliases() -> None:
    """.env.example 的模型别名清单与 MODEL_ALIASES 键集一致，README CLI 块同源。

    新增模型别名时清单与三语 README 的 --model 行须同步，否则用户不知道
    新别名可用。
    """
    from seedream_mcp.utils.model.model_capabilities import MODEL_ALIASES

    example_text = _example_path().read_text(encoding="utf-8")
    model_section = example_text.split("SEEDREAM_MODEL_ID=", 1)[0].rsplit(
        "SEEDREAM_ALLOW_HTTP_BASE_URL", 1
    )[-1]
    section_aliases = set(re.findall(r"doubao-seedream-[0-9a-z.\-]+", model_section))

    assert section_aliases == set(MODEL_ALIASES), (
        f".env.example 别名清单与 MODEL_ALIASES 不一致: "
        f"多出 {sorted(section_aliases - set(MODEL_ALIASES))}, "
        f"缺失 {sorted(set(MODEL_ALIASES) - section_aliases)}"
    )


def test_readme_cli_model_line_matches_model_aliases() -> None:
    """三语 README 的 --model 行别名清单与 MODEL_ALIASES 键集一致。"""
    from seedream_mcp.utils.model.model_capabilities import MODEL_ALIASES

    for readme in ("README.md", "README.en.md", "README.zh-TW.md"):
        text = (_repo_root() / readme).read_text(encoding="utf-8")
        model_lines = [line for line in text.splitlines() if line.lstrip().startswith("--model")]
        assert model_lines, f"{readme} 缺少 --model 参数行"
        line_aliases = set(re.findall(r"doubao-seedream-[0-9a-z.\-]+", model_lines[0]))
        assert line_aliases == set(MODEL_ALIASES), (
            f"{readme} --model 行别名与 MODEL_ALIASES 不一致: "
            f"多出 {sorted(line_aliases - set(MODEL_ALIASES))}, "
            f"缺失 {sorted(set(MODEL_ALIASES) - line_aliases)}"
        )


def test_example_size_preset_comments_match_capability_table() -> None:
    """.env.example 的家族档位注释与能力表 allowed_presets 一致。"""
    from seedream_mcp.utils.model.model_capabilities import (
        MODEL_CAPABILITIES,
        MODEL_FAMILY_40,
        MODEL_FAMILY_45,
        MODEL_FAMILY_50_LITE,
        MODEL_FAMILY_50_PRO,
    )

    expected_lines = {
        "# - Seedream 5.0 Pro：1K, 1.5K, 2K": MODEL_FAMILY_50_PRO,
        "# - Seedream 5.0 / 5.0 Lite：2K, 3K, 4K": MODEL_FAMILY_50_LITE,
        "# - Seedream 4.5：2K, 4K": MODEL_FAMILY_45,
        "# - Seedream 4.0：1K, 2K, 4K": MODEL_FAMILY_40,
    }
    example_text = _example_path().read_text(encoding="utf-8")
    for comment_line, family in expected_lines.items():
        assert comment_line in example_text, f".env.example 缺少档位注释行: {comment_line}"
        presets_text = comment_line.split("：", 1)[1]
        presets = [item.strip() for item in presets_text.split(",")]
        assert presets == sorted(
            MODEL_CAPABILITIES[family].allowed_presets,
            key=lambda p: float(p.removesuffix("K")),
        ), f"档位注释与能力表不一致: {comment_line}"


def test_compose_image_tag_major_minor_matches_version() -> None:
    """compose 镜像标签的 major.minor 与包版本一致，发版漏更标签在此报警。"""
    from seedream_mcp.version import __version__

    compose = (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"image:\s*ghcr\.io/tengmmvp/seedream_mcp:(\d+\.\d+)", compose)
    assert match is not None, "docker-compose.yml 未找到 seedream_mcp 镜像标签"
    major_minor = ".".join(__version__.split(".")[:2])
    assert match.group(1) == major_minor, (
        f"compose 镜像标签 {match.group(1)} 与版本 {__version__} 的 major.minor 漂移，"
        "发版须同步更新标签"
    )
