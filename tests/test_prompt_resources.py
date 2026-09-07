"""MCP 风格预设 Prompt 的注册与渲染测试。

注册形态经 server.mcp.list_prompts 断言；输出形态经 in-process Client 的
get_prompt 渲染断言固定前缀、主题与风格后缀的拼接。
"""

from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.types import GetPromptResult, TextContent

import seedream_mcp.server as server

# lifespan 复位 fixture reset_lifespan_singletons 由 tests/conftest.py 共享提供

_STYLE_PROMPTS = {
    "seedream_style_anime": "日系动漫风格",
    "seedream_style_realistic": "写实摄影风格",
    "seedream_style_watercolor": "水彩画风格",
    "seedream_style_oil_painting": "油画风格",
}

_DEFAULT_SUBJECTS = {
    "seedream_style_anime": "一个女孩站在樱花树下",
    "seedream_style_realistic": "城市夜景",
    "seedream_style_watercolor": "山间小屋",
    "seedream_style_oil_painting": "海边夕阳",
}


def _single_user_text(result: GetPromptResult) -> str:
    """断言渲染结果为单一 user 文本消息并返回其文本。"""
    assert len(result.messages) == 1
    message = result.messages[0]
    assert message.role == "user"
    assert isinstance(message.content, TextContent)
    return message.content.text


async def test_style_prompts_registered() -> None:
    """四个风格预设 Prompt 均以注册名注册，数量恰为四个。"""
    prompts = await server.mcp.list_prompts()

    names = {prompt.name for prompt in prompts}
    assert names == set(_STYLE_PROMPTS)
    assert len(prompts) == len(_STYLE_PROMPTS)


async def test_style_prompts_declare_title_description_and_subject_argument() -> None:
    """每个风格 Prompt 声明 title 与 description，subject 参数带说明且非必填。"""
    prompts = await server.mcp.list_prompts()
    by_name = {prompt.name: prompt for prompt in prompts}

    for name in _STYLE_PROMPTS:
        prompt = by_name[name]
        assert prompt.title, f"{name} 缺少 title"
        assert prompt.description, f"{name} 缺少 description"
        assert prompt.arguments is not None
        arguments = {argument.name: argument for argument in prompt.arguments}
        subject = arguments.get("subject")
        assert subject is not None, f"{name} 缺少 subject 参数"
        assert subject.description, f"{name} 的 subject 参数缺少说明"
        assert subject.required is False


@pytest.mark.parametrize("name", sorted(_STYLE_PROMPTS))
async def test_style_prompt_renders_default_subject(
    reset_lifespan_singletons: None, name: str
) -> None:
    """无参渲染使用注册声明的默认主题，输出为固定前缀接主题与风格后缀。"""
    async with Client(server.mcp) as client:
        result = await client.get_prompt(name)

    text = _single_user_text(result)
    assert text.startswith(f"{server._STYLE_PROMPT_PREFIX}{_DEFAULT_SUBJECTS[name]}，")
    assert _STYLE_PROMPTS[name] in text


@pytest.mark.parametrize("name", sorted(_STYLE_PROMPTS))
async def test_style_prompt_renders_custom_subject(
    reset_lifespan_singletons: None, name: str
) -> None:
    """自定义主题替换默认值，固定前缀与风格后缀保持拼接形态。"""
    subject = "一只戴墨镜的柴犬"
    async with Client(server.mcp) as client:
        result = await client.get_prompt(name, arguments={"subject": subject})

    text = _single_user_text(result)
    assert text.startswith(f"{server._STYLE_PROMPT_PREFIX}{subject}，")
    assert _STYLE_PROMPTS[name] in text
