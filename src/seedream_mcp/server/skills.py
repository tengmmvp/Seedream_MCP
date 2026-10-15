"""Agent Skills 读取机制：常量、路径锚定与打包树读取函数。

skills 数据目录随包分发于包根 skills/，双轨暴露：整目录拷贝到 ~/.claude/skills/
手动安装，或经 skill:// 资源供客户端自动发现；资源注册与载荷缓存位于
_registration。
"""

from __future__ import annotations

from io import TextIOWrapper
from pathlib import Path

from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.shared.path_security import PathEscapeError, safe_join

from ..utils.io.files import open_packaged_read

# 渐进式披露三层：SKILL.md 条目常驻 resources/list，正文在读取时加载，
# references 模板资源按需读取。
_SKILL_NAME = "seedream-image-generation"
_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
_SKILL_MANIFEST_PATH = _SKILLS_DIR / _SKILL_NAME / "SKILL.md"
_SKILL_REFERENCES_DIR = _SKILLS_DIR / _SKILL_NAME / "references"

# 与 skills/seedream-image-generation/SKILL.md frontmatter 的 description 保持一致，
# 两侧一致性由 test_skill_resource_description_matches_frontmatter 锁定。
_SKILL_DESCRIPTION = (
    "Seedream 图像生成 MCP 服务器的使用指南，覆盖文生图、图生图、多图融合、"
    "组图生成与图层拆分。当用户要求生成图片、画图、改图、换风格、融合多张图、"
    "制作连环画或故事书、拆分图层、生成透明背景，或需要调用 text_to_image、"
    "image_to_image、multi_image_fusion、sequential_generation、browse_images、"
    "get_model_info 工具，确认当前模型能力与尺寸档位，排查 401/402/403/413/429 "
    "报错，以及找回已保存的图片时使用本技能。Use when generating or editing "
    "images via the Seedream MCP server."
)


def _read_packaged_text(path: Path) -> str | None:
    """按打包树打开纪律读取静态文本文件，打开与解码失败统一收敛为 None。"""
    try:
        opened = open_packaged_read(path)
        if opened is None:
            # FIFO 等非常规文件按不存在口径拒绝，其阻塞读会钉死工作线程。
            return None
        handle, _stat_result = opened
        with TextIOWrapper(handle, encoding="utf-8") as text:
            return text.read()
    # UnicodeDecodeError 非 OSError 子类，损坏的非 UTF-8 打包文件同样按不可读取收敛。
    except (OSError, UnicodeDecodeError):
        return None


def _read_skill_manifest() -> str:
    """读取 SKILL.md 主文件，含打包树打开等同步文件系统调用。"""
    payload = _read_packaged_text(_SKILL_MANIFEST_PATH)
    if payload is None:
        raise ResourceNotFoundError("skill 主文件不存在或不可读取")
    return payload


def _read_skill_reference(path: str) -> str:
    """定位并读取 skill 参考文档，含 safe_join 等同步文件系统调用。"""
    try:
        target = safe_join(_SKILL_REFERENCES_DIR, path)
    except PathEscapeError:
        raise ResourceNotFoundError(f"skill 参考文件路径越界: {path}")
    payload = _read_packaged_text(target)
    if payload is None:
        raise ResourceNotFoundError(f"skill 参考文件不存在或不可读取: {path}")
    return payload
