"""ErrorText 结构化错误文本契约测试：分类渲染、单遍替换、幂等与双通道语义。

模板为代码常量、动态值经分类参数进入是全量结构化重写的构造性保证；本文件锁定
五类参数的双通道渲染差异、替换的单遍性（参数内 token 字面量不被二次替换）、
safe 渲染对出口幂等净化的恒等性，异常承载的日志原文/用户净化双通道分野，以及
raw/safe 生产者与两步坍缩形态的产物字节一致。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from seedream_mcp.utils.core.errors import (
    ErrorText,
    SeedreamAPIError,
    SeedreamConfigError,
    as_detail,
    as_plain,
    as_secret,
    as_text,
    as_url,
    assemble_error_segments,
    error_text,
    format_error_for_user,
    handle_api_error,
    raw_error_text,
    render_boundary_error_text,
    safe_error_text,
    validation_error_user_message,
)
from seedream_mcp.tools.outputs import build_error_dict
from seedream_mcp.utils.core.sanitizers import sanitize_assembled_text


def test_error_text_rejects_template_param_mismatch() -> None:
    """模板占位符与参数名双向校验，缺参或多参在构造期即失败。"""
    with pytest.raises(ValueError):
        error_text("缺参数 {a} {b}", a=as_text("x"))
    with pytest.raises(ValueError):
        error_text("多参数 {a}", a=as_text("x"), b=as_text("y"))


def test_producer_renders_match_two_step_forms() -> None:
    """raw/safe 生产者与两步形态（error_text 构造后坍缩）产物逐字节一致。"""
    template = "值 {plain}｜{detail}｜{secret}｜{url}"
    params = dict(
        plain=as_plain(7),
        detail=as_detail("api_key=hunter2 继续文本"),
        secret=as_secret("sk-live-abcdef"),
        url=as_url("https://user:pass@example.com/a.png?sig=1"),
    )
    two_step = error_text(template, **params)

    assert raw_error_text(template, **params) == two_step.raw_text()
    assert safe_error_text(template, **params) == two_step.safe_text()
    # 双通道产物确实分野，字节一致断言不是同串恒等的空转。
    assert two_step.raw_text() != two_step.safe_text()


def test_producers_reject_template_param_mismatch() -> None:
    """生产者沿用构造期模板-参数双向校验，缺参或多参即抛 ValueError。"""
    with pytest.raises(ValueError):
        raw_error_text("缺参数 {a} {b}", a=as_text("x"))
    with pytest.raises(ValueError):
        safe_error_text("多参数 {a}", a=as_text("x"), b=as_text("y"))


def test_param_policies_raw_channel_rendering() -> None:
    """日志通道：plain/text/detail 原样，secret 恒掩码，url 剥凭据与查询。"""
    text = error_text(
        "值 {plain}｜{value}｜{detail}｜{secret}｜{url}",
        plain=as_plain(5),
        value=as_text("D:\\data\\图片"),
        detail=as_detail("upstream said boom"),
        secret=as_secret("sk-live-abcdef"),
        url=as_url("https://user:pass@example.com/a.png?sig=1"),
    )
    assert text.raw_text() == (
        "值 5｜D:\\data\\图片｜upstream said boom｜***｜"
        "https://example.com/a.png?<query-redacted>"
    )


def test_safe_text_masks_credential_shapes_in_params() -> None:
    """用户通道：text/detail 参数中的凭据形态被掩码，raw 通道保留原文。"""
    text = error_text("上游返回 {detail}", detail=as_detail("api_key=hunter2 继续文本"))
    assert "hunter2" not in text.safe_text()
    assert "hunter2" in text.raw_text()


def test_substitution_is_single_pass() -> None:
    """参数值中的 {token} 字面量不被二次替换，链式注入从构造上排除。"""
    text = error_text("前 {a} 后 {b}", a=as_text("{b}"), b=as_text("真值"))
    assert text.raw_text() == "前 {b} 后 真值"


def test_safe_text_survives_idempotent_replay() -> None:
    """safe 渲染产物经出口幂等净化重放字节不变。"""
    text = error_text("失败 {value}；请稍后重试。", value=as_text("token=abc123 耗尽"))
    safe = text.safe_text()
    assert sanitize_assembled_text(safe) == safe


def test_str_coerces_to_safe_render() -> None:
    """str() 取 safe 渲染：误传字符串上下文时按用户通道净化，fail-closed。"""
    text = error_text("鉴权 {secret}", secret=as_secret("sk-live-xyz"))
    assert str(text) == "鉴权 ***"


def test_multilingual_templates_render_language_agnostic() -> None:
    """中/英/日模板与指引混合渲染一致，掩码判定不依赖文字脚本。"""
    zh = error_text("失败 {reason}", reason=as_text("api_key=zzz")).safe_text()
    en = error_text("failed: {reason}", reason=as_text("api_key=zzz")).safe_text()
    ja = error_text("失敗 {reason}", reason=as_text("api_key=zzz")).safe_text()
    assert zh == "失败 api_key=***"
    assert en == "failed: api_key=***"
    assert ja == "失敗 api_key=***"


def test_empty_value_credential_tail_terminated_at_message_end() -> None:
    """空值尾形（token=）在消息末位被补掩码终止，拼接指引后不吞并。"""
    text = error_text("失败 {value}", value=as_text("前置 token="))
    assert text.safe_text().endswith("token=***")


def test_exception_carrier_dual_channel() -> None:
    """ErrorText 构造的异常：str 与 .message 为日志原文，用户渲染经 format 净化。"""
    error = SeedreamConfigError(
        error_text("目录 {path} 无法解析", path=as_text("D:\\机密\\token=leak"))
    )
    assert str(error) == "目录 D:\\机密\\token=leak 无法解析"
    assert error.message == str(error)
    user_line = format_error_for_user(error)
    assert "leak" not in user_line
    assert user_line.startswith("配置错误: ")
    assert "请检查服务端配置后重试。" in user_line


def test_legacy_string_exception_keeps_legacy_path() -> None:
    """纯字符串构造走遗留净化路径，双构造形态并存期行为不回归。"""
    error = SeedreamConfigError("token=abc 泄漏文本")
    assert error.error_text_payload is None
    assert "abc" not in format_error_for_user(error)


def test_handle_api_error_parity_and_masking() -> None:
    """上游片段经 detail 参数进入：日志通道保原文拼接形态，用户通道掩码凭据。"""
    error = handle_api_error(
        400, {"error": {"message": "api_key=hunter2 invalid", "code": "InvalidApiKey"}}
    )
    assert isinstance(error, SeedreamAPIError)
    assert error.message == "请求参数错误: api_key=hunter2 invalid"
    user_line = format_error_for_user(error)
    assert "hunter2" not in user_line
    assert "[错误码: InvalidApiKey]" in user_line


def test_validation_error_user_message_carries_structured_params() -> None:
    """pydantic 校验消息以 plain 字段 + text 原因参数进入，safe 渲染可定位。"""

    class _Model(BaseModel):
        size: int

    with pytest.raises(ValidationError) as exc_info:
        _Model.model_validate({"size": "big"})
    message = validation_error_user_message(exc_info.value)
    assert isinstance(message, ErrorText)
    assert "size" in message.safe_text()
    assert message.safe_text().startswith("参数校验失败: ")


def test_assemble_error_segments_joins_non_empty_only() -> None:
    """段聚合原语以段分隔符拼接非空段，空段不产生连续分隔符。"""
    assert assemble_error_segments("a", "", "b") == "a；b"
    assert assemble_error_segments("唯一") == "唯一"


def test_assemble_error_segments_terminates_each_segment() -> None:
    """段终止在拼装原语内承担：空值尾形段末补终止，指引段在出口重放下存活。"""
    assembled = assemble_error_segments(
        error_text("失败 {value}", value=as_text("前置 token=")),
        "可配置 SEEDREAM_WORKSPACE_ROOT",
    )

    assert assembled == "失败 前置 token=***；可配置 SEEDREAM_WORKSPACE_ROOT"
    assert sanitize_assembled_text(assembled) == assembled


def test_boundary_render_does_not_truncate_long_error_text() -> None:
    """错误载荷边界不截断：超长 ErrorText 消息全量保留，与 str 臂同口径。"""
    long_detail = "x" * 900
    payload = build_error_dict(
        "api_error", error_text("失败 {detail}", detail=as_detail(long_detail))
    )

    assert payload["message"] == f"失败 {long_detail}"
    assert "<truncated" not in payload["message"]


def test_render_boundary_error_text_matches_legacy_str_arm() -> None:
    """边界渲染两臂同口径：同一文本的 ErrorText 形态与 str 形态产物相等。"""
    text = error_text("失败 {value}", value=as_text("api_key=zzz 尾部"))

    assert render_boundary_error_text(text) == render_boundary_error_text(text.raw_text())
