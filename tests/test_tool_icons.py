"""工具 icons 元数据契约：五工具各带 PNG 与 SVG 双 data URI 图标。"""

from __future__ import annotations

import base64
import io
import xml.etree.ElementTree as ET

import pytest

import seedream_mcp.server  # noqa: F401  工具注册发生在 server 导入期
from seedream_mcp._icons import TOOL_PNG_SIZE, tool_icon_png_src
from seedream_mcp.resources import mcp
from seedream_mcp.utils.core.formats import infer_extension_from_bytes, parse_data_uri

_EXPECTED_ICON_TOOLS = frozenset(
    {
        "text_to_image",
        "image_to_image",
        "multi_image_fusion",
        "sequential_generation",
        "browse_images",
    }
)


async def test_every_tool_has_png_and_svg_data_uri_icons() -> None:
    tools = await mcp.list_tools()
    icons = {tool.name: tool.icons for tool in tools}
    assert _EXPECTED_ICON_TOOLS == set(icons)
    for name, tool_icons in icons.items():
        assert tool_icons is not None, name
        assert len(tool_icons) == 2, name
        by_mime = {icon.mime_type: icon for icon in tool_icons}
        assert set(by_mime) == {"image/png", "image/svg+xml"}, name

        png_icon = by_mime["image/png"]
        png_media, png_payload, png_is_b64 = parse_data_uri(png_icon.src)
        assert png_media == "image/png", name
        assert png_is_b64, name
        assert png_icon.sizes == [TOOL_PNG_SIZE], name
        png_bytes = base64.b64decode(png_payload)
        assert infer_extension_from_bytes(png_bytes) == ".png", name

        svg_icon = by_mime["image/svg+xml"]
        svg_media, svg_payload, svg_is_b64 = parse_data_uri(svg_icon.src)
        assert svg_media == "image/svg+xml", name
        assert svg_is_b64, name
        assert ET.fromstring(base64.b64decode(svg_payload)).tag.endswith("svg"), name


def test_icon_dicts_share_tool_key_set() -> None:
    """矢量与光栅两表键集一致，新增工具漏更任一格式即失败。"""
    from seedream_mcp._icons import _TOOL_PNG_B64, _TOOL_SVGS

    assert set(_TOOL_SVGS) == set(_TOOL_PNG_B64) == set(_EXPECTED_ICON_TOOLS)


@pytest.mark.parametrize("tool_name", sorted(_EXPECTED_ICON_TOOLS))
def test_png_icon_declared_size_matches_payload(tool_name: str) -> None:
    """尺寸常量声明与 PNG 载荷实际尺寸一致。"""
    from PIL import Image

    _, payload, _ = parse_data_uri(tool_icon_png_src(tool_name))
    expected = tuple(int(part) for part in TOOL_PNG_SIZE.split("x"))
    with Image.open(io.BytesIO(base64.b64decode(payload))) as decoded:
        assert decoded.size == expected, tool_name
