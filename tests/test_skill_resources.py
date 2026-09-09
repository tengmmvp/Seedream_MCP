"""Agent Skills 开放标准目录与 skill:// 资源守护测试。

三组守护：静态文件符合 Agent Skills 规范，覆盖 frontmatter 约束、行数预算、
引用可解析与工具名不杜撰；资源注册形态为静态主文件加 references 模板，mime 为
text/markdown；经 in-process Client 的线上读取管线验证内容与磁盘一致、越界
与缺失路径收敛 -32602。frontmatter 用轻量正则解析，不引入 pyyaml 依赖。
"""

from __future__ import annotations

import re
import threading

import pytest
from mcp import MCPError
from mcp.client import Client
from mcp.types import ReadResourceResult, TextResourceContents

import seedream_mcp.server as server

# lifespan 复位 fixture reset_lifespan_singletons 由 tests/conftest.py 共享提供

_SKILL_DIR = server._SKILLS_DIR / server._SKILL_NAME
_SKILL_MANIFEST_PATH = server._SKILL_MANIFEST_PATH
_SKILL_REFERENCES_DIR = server._SKILL_REFERENCES_DIR
_MANIFEST_URI = "skill://seedream-image-generation/SKILL.md"
_REFERENCE_TEMPLATE_URIS = {
    "skill://seedream-image-generation/references/workflows.md",
    "skill://seedream-image-generation/references/troubleshooting.md",
}
# SKILL.md 正文行数上限：Agent Skills 渐进式披露建议正文 <5000 tokens，
# 行数预算取 500 行为硬上限，日常目标 300 行以内。
_MANIFEST_MAX_LINES = 500


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md 顶部 YAML frontmatter 的 name 与 description 单行字段。

    项目自持该文件格式，字段均为单行标量，轻量正则即可满足，不引入 pyyaml。
    """
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match is not None, "SKILL.md 必须以 YAML frontmatter 开头"
    fields: dict[str, str] = {}
    for key in ("name", "description"):
        key_match = re.search(rf"^{key}: (.+)$", match.group(1), re.MULTILINE)
        assert key_match is not None, f"frontmatter 缺少 {key} 字段"
        fields[key] = key_match.group(1).strip()
    return fields


def _single_text_content(result: ReadResourceResult) -> str:
    """断言读取结果为单一文本内容并返回其文本。"""
    assert len(result.contents) == 1
    content = result.contents[0]
    assert content.mime_type == "text/markdown"
    assert isinstance(content, TextResourceContents)
    return content.text


# ==================== 静态文件规范 ====================


def test_skill_directory_name_matches_frontmatter_name() -> None:
    """frontmatter name 必须与父目录名一致，Agent Skills 规范硬约束。"""
    fields = _parse_frontmatter(_SKILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert fields["name"] == _SKILL_DIR.name


def test_skill_frontmatter_name_conforms_to_standard() -> None:
    """name 仅小写字母数字与单个连字符，长度 1-64 字符。"""
    fields = _parse_frontmatter(_SKILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    name = fields["name"]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) is not None
    assert 1 <= len(name) <= 64


def test_skill_frontmatter_description_within_limits() -> None:
    """description 长度 1-1024 字符且非空。"""
    fields = _parse_frontmatter(_SKILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert 1 <= len(fields["description"]) <= 1024


def test_skill_manifest_within_disclosure_budget() -> None:
    """SKILL.md 正文行数不超过渐进式披露预算上限。"""
    lines = _SKILL_MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= _MANIFEST_MAX_LINES


def test_skill_references_tree_is_markdown_only() -> None:
    """references 目录只含 .md 文件，资源注册的 text/markdown mime 才真实。"""
    files = [p for p in _SKILL_REFERENCES_DIR.rglob("*") if p.is_file()]
    assert files, "references 目录不应为空"
    assert all(p.suffix == ".md" for p in files)


def test_skill_relative_links_resolve() -> None:
    """SKILL.md 与各 reference 中引用 references/ 下文件的相对路径必须存在。"""
    markdown_files = [_SKILL_MANIFEST_PATH, *_SKILL_REFERENCES_DIR.rglob("*.md")]
    pattern = re.compile(r"\]\((references/[A-Za-z0-9_./-]+\.md)\)")
    for source in markdown_files:
        for relative in pattern.findall(source.read_text(encoding="utf-8")):
            assert (source.parent / relative).is_file(), f"{source.name} 引用 {relative} 不存在"


async def test_skill_mentions_all_registered_tools() -> None:
    """SKILL.md 正文逐一提及全部注册工具名，防止杜撰或遗漏工具。"""
    tools = await server.mcp.list_tools()
    manifest = _SKILL_MANIFEST_PATH.read_text(encoding="utf-8")
    for tool in tools:
        assert tool.name in manifest, f"SKILL.md 未提及工具 {tool.name}"


async def test_skill_resource_description_matches_frontmatter() -> None:
    """静态资源注册的 description 与 frontmatter 同文，双份维护不漂移。"""
    resources = await server.mcp.list_resources()
    by_uri = {str(resource.uri): resource for resource in resources}
    registered = by_uri[_MANIFEST_URI].description or ""
    fields = _parse_frontmatter(_SKILL_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert registered == fields["description"]


# ==================== 注册断言 ====================


async def test_skill_resources_registered() -> None:
    """主文件以静态资源注册，references 以模板资源注册。"""
    resources = await server.mcp.list_resources()
    uris = {str(resource.uri) for resource in resources}
    assert _MANIFEST_URI in uris

    templates = await server.mcp.list_resource_templates()
    template_uris = {str(template.uri_template) for template in templates}
    assert "skill://seedream-image-generation/references/{+path}" in template_uris


async def test_skill_resources_declare_markdown_mime() -> None:
    """静态资源与模板资源均声明 text/markdown。"""
    resources = await server.mcp.list_resources()
    by_uri = {str(resource.uri): resource for resource in resources}
    assert by_uri[_MANIFEST_URI].mime_type == "text/markdown"

    templates = await server.mcp.list_resource_templates()
    by_template = {str(t.uri_template): t for t in templates}
    template = by_template["skill://seedream-image-generation/references/{+path}"]
    assert template.mime_type == "text/markdown"


# ==================== 线上读取 ====================


async def test_skill_manifest_readable_over_wire(reset_lifespan_singletons: None) -> None:
    """经真实读取管线取回 SKILL.md，内容与磁盘文件全文一致。"""
    async with Client(server.mcp) as client:
        result = await client.read_resource(_MANIFEST_URI)
    assert _single_text_content(result) == _SKILL_MANIFEST_PATH.read_text(encoding="utf-8")


async def test_skill_manifest_wire_read_runs_off_event_loop_thread(
    reset_lifespan_singletons: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """经注册链读取 SKILL.md 时文件 I/O 在工作线程执行。

    锁定注册目标为带 to_thread 下沉的缓存实现：装饰器挂回同步直读函数时，
    线上路径在事件循环做磁盘 I/O，本用例失败。
    """
    monkeypatch.setattr(server, "_skill_manifest_payload", None)
    original = server._read_skill_manifest
    seen_threads: list[int] = []

    def spy() -> str:
        seen_threads.append(threading.get_ident())
        return original()

    monkeypatch.setattr(server, "_read_skill_manifest", spy)
    async with Client(server.mcp) as client:
        await client.read_resource(_MANIFEST_URI)

    assert seen_threads and seen_threads[0] != threading.get_ident()


@pytest.mark.parametrize("uri", sorted(_REFERENCE_TEMPLATE_URIS))
async def test_skill_reference_readable_over_wire(
    reset_lifespan_singletons: None, uri: str
) -> None:
    """references 模板资源按具体 URI 读取，内容与磁盘文件全文一致。"""
    relative = uri.rsplit("/", 1)[-1]
    async with Client(server.mcp) as client:
        result = await client.read_resource(uri)
    assert _single_text_content(result) == (_SKILL_REFERENCES_DIR / relative).read_text(
        encoding="utf-8"
    )


async def test_skill_reference_read_runs_off_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """参考文档定位与读取经工作线程执行，同步文件系统调用不占事件循环。"""
    original = server._read_skill_reference
    seen_threads: list[int] = []

    def spy(path: str) -> str:
        seen_threads.append(threading.get_ident())
        return original(path)

    monkeypatch.setattr(server, "_read_skill_reference", spy)
    text = await server.skill_reference_resource("workflows.md")

    assert text == (_SKILL_REFERENCES_DIR / "workflows.md").read_text(encoding="utf-8")
    assert len(seen_threads) == 1
    assert seen_threads[0] != threading.get_ident()


async def test_skill_manifest_read_runs_off_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SKILL.md 首次读取经工作线程执行，同步文件系统调用不占事件循环。"""
    monkeypatch.setattr(server, "_skill_manifest_payload", None)
    original = server._read_skill_manifest
    seen_threads: list[int] = []

    def spy() -> str:
        seen_threads.append(threading.get_ident())
        return original()

    monkeypatch.setattr(server, "_read_skill_manifest", spy)
    text = await server.skill_manifest_resource()

    assert text == _SKILL_MANIFEST_PATH.read_text(encoding="utf-8")
    assert len(seen_threads) == 1
    assert seen_threads[0] != threading.get_ident()


async def test_skill_reference_missing_file_raises_invalid_params(
    reset_lifespan_singletons: None,
) -> None:
    """读取不存在的参考文件按协议返回 -32602，data 携带请求 URI。"""
    uri = "skill://seedream-image-generation/references/nope.md"
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.read_resource(uri)
    assert excinfo.value.code == -32602
    assert excinfo.value.data is not None
    assert excinfo.value.data.get("uri") == uri


async def test_skill_reference_rejects_path_traversal(
    reset_lifespan_singletons: None,
) -> None:
    """路径穿越变体在 SDK 安全预检或 safe_join 处被拒，同样收敛 -32602。"""
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as excinfo:
            await client.read_resource(
                "skill://seedream-image-generation/references/..%2F..%2Fserver.py"
            )
    assert excinfo.value.code == -32602


def test_troubleshooting_numeric_claims_match_code_constants() -> None:
    """troubleshooting 与 SKILL.md 的数值声明与代码常量对账，漂移即失败。"""
    from seedream_mcp.config import SeedreamConfig
    from seedream_mcp.utils.core.formats import MAX_IMAGE_PIXELS
    from seedream_mcp.utils.core.logs import (
        DEFAULT_LOG_RETENTION_DAYS,
        DEFAULT_LOG_ROTATION_SIZE_MB,
    )
    from seedream_mcp.utils.images.image_thumbnail import (
        PREVIEW_MAX_IMAGES,
        THUMBNAIL_MAX_EDGE,
    )
    from seedream_mcp.utils.images.image_validation import (
        MAX_IMAGE_FILE_SIZE,
        MIN_IMAGE_EDGE,
    )

    troubleshooting = (_SKILL_REFERENCES_DIR / "troubleshooting.md").read_text(encoding="utf-8")
    manifest = _SKILL_MANIFEST_PATH.read_text(encoding="utf-8")
    defaults = SeedreamConfig(api_key="k")
    # 可选字段的默认为真实 int（10GB 配额），None 分支只存在于显式关闭配置。
    assert defaults.auto_save_max_total_bytes is not None
    max_total_gb = defaults.auto_save_max_total_bytes // (1024**3)

    expected_troubleshooting_claims = (
        f"{MIN_IMAGE_EDGE * MIN_IMAGE_EDGE} 像素 ~ {MAX_IMAGE_PIXELS // 10000} 万像素",
        f"每边至少 {MIN_IMAGE_EDGE} px",
        f"不超过 {MAX_IMAGE_FILE_SIZE // (1024 * 1024)} MB",
        f"长边 ≤{THUMBNAIL_MAX_EDGE}px",
        f"最多 {PREVIEW_MAX_IMAGES} 张",
        f"{defaults.auto_save_cleanup_days} 天自动清理、总量 " f"{max_total_gb}GB 上限",
        f"{DEFAULT_LOG_ROTATION_SIZE_MB}MB 轮转、保留 {DEFAULT_LOG_RETENTION_DAYS} 天",
    )
    for claim in expected_troubleshooting_claims:
        assert claim in troubleshooting, f"troubleshooting.md 缺少数值声明: {claim}"

    manifest_claims = (
        f"{defaults.auto_save_cleanup_days} 天",
        f"{max_total_gb}GB",
    )
    for claim in manifest_claims:
        assert claim in manifest, f"SKILL.md 缺少数值声明: {claim}"
