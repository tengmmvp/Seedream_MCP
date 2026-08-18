"""工具 outputSchema 结构化输出声明测试。

验证所有工具都声明了 outputSchema，对齐 MCP 2025-06-18 规范，供客户端程序化解析与校验。
outputSchema 由 MCPServer 依据工具返回类型注解自动生成，涉及 GenerationStructuredOutput
与 BrowseImagesStructuredOutput；运行时工具仍返回手动构造的 CallToolResult，
保留人类可读 content 文本与 structuredContent。
"""

from __future__ import annotations

from seedream_mcp.server import mcp


async def test_all_tools_declare_output_schema() -> None:
    tools = await mcp.list_tools()
    assert tools, "未注册任何工具"

    for tool in tools:
        assert tool.output_schema is not None, f"{tool.name} 缺少 outputSchema"
        assert tool.output_schema.get("type") == "object"
        properties = tool.output_schema.get("properties", {})
        assert "tool" in properties, f"{tool.name} outputSchema 缺少 tool"
        assert "success" in properties, f"{tool.name} outputSchema 缺少 success"


async def test_generation_tools_output_schema_covers_core_fields() -> None:
    tools = await mcp.list_tools()
    generation_tools = {tool.name: tool for tool in tools if tool.name != "browse_images"}

    for name, tool in generation_tools.items():
        schema = tool.output_schema
        assert schema is not None
        properties = schema["properties"]
        for field in ("data", "usage", "batch", "auto_save", "prompt", "size"):
            assert field in properties, f"{name} outputSchema 缺少 {field}"


async def test_browse_tool_output_schema_covers_core_fields() -> None:
    tools = await mcp.list_tools()
    browse = next(tool for tool in tools if tool.name == "browse_images")
    schema = browse.output_schema
    assert schema is not None

    properties = schema["properties"]
    for field in ("images", "count", "directory", "workspace_roots"):
        assert field in properties, f"browse outputSchema 缺少 {field}"
