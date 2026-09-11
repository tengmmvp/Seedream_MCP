"""工具 icons 元数据契约：五工具各带一枚可解码的 SVG data URI 图标。"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET

import seedream_mcp.server  # noqa: F401  工具注册发生在 server 导入期
from seedream_mcp.resources import mcp
from seedream_mcp.utils.core.formats import parse_data_uri

_EXPECTED_ICON_TOOLS = frozenset(
    {
        "text_to_image",
        "image_to_image",
        "multi_image_fusion",
        "sequential_generation",
        "browse_images",
    }
)


async def test_every_tool_has_single_svg_data_uri_icon() -> None:
    tools = await mcp.list_tools()
    icons = {tool.name: tool.icons for tool in tools}
    assert _EXPECTED_ICON_TOOLS == set(icons)
    for name, tool_icons in icons.items():
        assert tool_icons is not None, name
        assert len(tool_icons) == 1, name
        icon = tool_icons[0]
        media_type, payload, is_base64 = parse_data_uri(icon.src)
        assert media_type == "image/svg+xml", name
        assert is_base64, name
        assert icon.mime_type == "image/svg+xml", name
        root = ET.fromstring(base64.b64decode(payload))
        assert root.tag.endswith("svg"), name
