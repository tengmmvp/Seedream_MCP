"""MCP 风格预设 Prompt 的注册、渲染与参数面错误码测试。

注册形态经 server.mcp.list_prompts 断言；渲染与参数面错误（缺失、未知参数与
值校验失败的 -32602 收敛）经 in-process Client 的 get_prompt 断言，
_targets_argument 与 _locate_validation_error 的口径另以单元用例锁定。
"""

from __future__ import annotations

from typing import Annotated

import pytest
from mcp.client import Client
from mcp.server.mcpserver.prompts.base import Prompt
from mcp.shared.exceptions import MCPError
from mcp.types import GetPromptResult, INVALID_PARAMS, TextContent
from pydantic import BaseModel, Field, ValidationError

import seedream_mcp.server as server
from seedream_mcp.resources import _locate_validation_error, _targets_argument
from seedream_mcp.tools.core.schemas import PROMPT_MAX_LENGTH

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


@pytest.mark.parametrize(
    "subject",
    [
        "",
        "   ",
        pytest.param("x" * (PROMPT_MAX_LENGTH + 1), id="overlong"),
    ],
)
@pytest.mark.parametrize("name", sorted(_STYLE_PROMPTS))
async def test_style_prompt_rejects_blank_subject(
    reset_lifespan_singletons: None, name: str, subject: str
) -> None:
    """空串、纯空白与超长主题被参数校验拒绝为 -32602，消息定位到 subject。"""
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.get_prompt(name, arguments={"subject": subject})

    assert exc_info.value.code == INVALID_PARAMS
    assert "subject" in str(exc_info.value)


def _flag_prompt_fn(flag: str) -> str:
    return f"flag={flag}"


def _install_flag_prompt(name: str) -> str:
    """安装声明必填 flag 参数的 prompt 并返回注册名。"""
    prompt = Prompt.from_function(_flag_prompt_fn, name=name)
    server.mcp.add_prompt(prompt)
    return prompt.name


async def test_missing_required_prompt_argument_returns_invalid_params(
    reset_lifespan_singletons: None,
) -> None:
    """缺必填参数的 prompt 在预检点拒绝为 -32602，消息定位到缺失参数名。"""
    name = _install_flag_prompt("pytest_missing_arg")
    try:
        async with Client(server.mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt(name)
    finally:
        server.mcp.remove_prompt(name)

    assert exc_info.value.code == INVALID_PARAMS
    assert "缺少必填参数" in str(exc_info.value)
    assert "flag" in str(exc_info.value)


async def test_unknown_prompt_argument_returns_invalid_params(
    reset_lifespan_singletons: None,
) -> None:
    """未知参数在预检点拒绝为 -32602，消息定位到多余参数名。"""
    name = _install_flag_prompt("pytest_unknown_arg")
    try:
        async with Client(server.mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt(name, arguments={"flag": "x", "bogus": "y"})
    finally:
        server.mcp.remove_prompt(name)

    assert exc_info.value.code == INVALID_PARAMS
    assert "未知参数" in str(exc_info.value)
    assert "bogus" in str(exc_info.value)


def _messages_arg_prompt(messages: Annotated[str, Field(min_length=2)]) -> str:
    return f"messages={messages}"


async def test_messages_named_argument_value_error_returns_invalid_params(
    reset_lifespan_singletons: None,
) -> None:
    """参数名撞结果模型字段（messages）的值校验失败仍收敛 -32602，不误放 -32603。"""
    prompt = Prompt.from_function(_messages_arg_prompt, name="pytest_messages_arg")
    server.mcp.add_prompt(prompt)
    try:
        async with Client(server.mcp) as client:
            with pytest.raises(MCPError) as exc_info:
                await client.get_prompt(prompt.name, arguments={"messages": "x"})
    finally:
        server.mcp.remove_prompt(prompt.name)

    assert exc_info.value.code == INVALID_PARAMS
    assert "messages" in str(exc_info.value)


class _SubjectModel(BaseModel):
    subject: str = Field(min_length=2)


class _MessagesModel(BaseModel):
    messages: str = Field(min_length=2)


def _short_value_error(model_cls: type[BaseModel], field_name: str) -> ValidationError:
    """以过短字段值触发一次校验失败并返回其 ValidationError。"""
    with pytest.raises(ValidationError) as exc_info:
        model_cls(**{field_name: "x"})
    return exc_info.value


def test_targets_argument_narrows_conversion_scope() -> None:
    """loc 首段命中已声明参数名的校验错误才转 -32602，参数名撞结果模型字段不例外。"""
    subject_error = _short_value_error(_SubjectModel, "subject")
    assert _targets_argument(subject_error, {"subject"}) is True
    assert _targets_argument(subject_error, {"image"}) is False

    messages_error = _short_value_error(_MessagesModel, "messages")
    assert _targets_argument(messages_error, {"messages"}) is True


def test_locate_validation_error_follows_implicit_context_chain() -> None:
    """无 raise-from 的旧版 SDK 包装经隐式链仍可定位 ValidationError。"""
    inner = _short_value_error(_SubjectModel, "subject")
    with pytest.raises(ValueError) as exc_info:
        try:
            raise inner
        except ValidationError:
            # 旧版 SDK 形态：捕获后裸抛 ValueError，无显式 raise-from。
            raise ValueError(f"Error rendering prompt: {inner}")

    assert _locate_validation_error(exc_info.value) is inner


def test_locate_validation_error_stops_on_suppressed_context() -> None:
    """from None 抑制的隐式链不跟随，定位返回 None。"""
    inner = _short_value_error(_SubjectModel, "subject")
    with pytest.raises(ValueError) as exc_info:
        try:
            raise inner
        except ValidationError:
            raise ValueError("Error rendering prompt") from None

    assert _locate_validation_error(exc_info.value) is None


def test_locate_validation_error_terminates_on_cyclic_context_chain() -> None:
    """隐式链成环时遍历终止并返回 None。"""
    first, second = ValueError("first"), ValueError("second")
    first.__context__ = second
    second.__context__ = first

    assert _locate_validation_error(first) is None
