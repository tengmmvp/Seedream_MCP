"""results 输出格式化守护测试：自动保存摘要形态、路径折叠与上游 URL 脱敏。

锁定的输出契约：自动保存段落折叠为 N/M 摘要、仅失败项保留明细且编号与图片列表
同基准（取可保存图片在 extract_images 归一化列表中的原始索引）；保存路径在文本
中每张图仅出现一次；URL 为数据字段——净化剥离 userinfo 凭据与 CRLF 但不截断，
签名 URL 完整保留。
"""

from __future__ import annotations

from typing import Any

from seedream_mcp.tools.core import results as results_module
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.results import (
    _build_generation_structured_result,
    _sanitize_image_errors,
    extract_images,
    format_generation_response,
    reset_last_sanitized_images,
    update_result_with_auto_save,
)
from seedream_mcp.utils.io.io_save import AutoSaveResult

# 带 userinfo 凭据与 CRLF 的上游 URL：净化后凭据被剥离、换行被压平。
_DIRTY_URL = "https://AKID:SECRET@mirror.example.com/a.png\r\nFAKE-LINE api_key=leaked"

# 火山 TOS 签名 URL 量级（约 674 字符）：数据字段净化不得截断，否则 URL 不可用。
_SIGNED_URL = "https://tos.example.com/obj/a.png?X-Tos-Signature=" + "s" * 620


def _context(enable_auto_save: bool = True) -> GenerationExecutionContext:
    return GenerationExecutionContext(
        prompt="test",
        optimize_prompt_options=None,
        size="2K",
        watermark=False,
        response_format="url",
        output_format=None,
        stream=False,
        tools=None,
        request_count=1,
        parallelism=1,
        enable_auto_save=enable_auto_save,
        save_path=None,
        custom_name=None,
    )


def _mixed_result() -> dict[str, Any]:
    """失败占位项居首、可保存图片居次的归一化结果：原始索引 0 失败、1 可保存。"""
    return {
        "success": True,
        "status": "partial",
        "data": [
            {
                "type": "image_generation.partial_failed",
                "image_index": 1,
                "error": {"code": "blocked", "message": "content blocked"},
            },
            {
                "type": "image_generation.partial_succeeded",
                "image_index": 2,
                "url": "https://example.com/ok.png",
            },
        ],
        "usage": {"generated_images": 1},
    }


def _save_result(success: bool) -> AutoSaveResult:
    return AutoSaveResult(
        success=success,
        original_url="https://example.com/ok.png",
        local_path="images/ok.png" if success else None,
        markdown_ref="![ok](images/ok.png)" if success else None,
        error=None if success else "下载失败",
    )


# ==================== 自动保存摘要与编号基准 ====================


def test_auto_save_success_collapses_to_summary_line() -> None:
    """全部保存成功时折叠为一行 N/M 摘要，路径仅在图片条目行出现一次。"""
    # 与流水线一致：先经 update_result_with_auto_save 回填 local_path/markdown_ref，
    # 再交文本格式化消费。
    merged = update_result_with_auto_save(_mixed_result(), [_save_result(success=True)], [1])
    text = format_generation_response(
        "文生图任务完成",
        merged,
        "test",
        "2K",
        [_save_result(success=True)],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    # 图片列表：图片 1 为失败占位项，图片 2 为成功图且携带本地路径。
    assert "图片 1:" in text
    assert "状态: 失败" in text
    assert "自动保存: 1/1 成功" in text
    assert "  本地路径: images/ok.png" in text
    # 成功项不再有「已保存到」明细行；URL/Markdown 引用行收敛后路径全文仅一次。
    assert "已保存到" not in text
    assert text.count("images/ok.png") == 1


def test_auto_save_section_failed_save_uses_original_image_index() -> None:
    """保存失败的条目按原始索引编号，与图片列表编号基准一致。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=False)],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    assert "自动保存: 0/1 成功" in text
    assert "图片 2: 保存失败 - 下载失败" in text
    assert "图片 1: 保存失败" not in text


def test_auto_save_section_falls_back_to_save_ordinal_without_indices() -> None:
    """未提供索引时失败明细回退保存序号，直接调用方仍可得到连续编号。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=False)],
        auto_save_enabled=True,
    )

    assert "图片 1: 保存失败 - 下载失败" in text


# ==================== 上游 URL 脱敏 ====================


def test_image_item_url_line_sanitized_in_text_output() -> None:
    """文本 URL 行净化：userinfo 凭据剥离、CRLF 压平，无换行注入。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": _DIRTY_URL}],
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")

    assert "AKID:SECRET@" not in text
    assert "SECRET" not in text
    assert "mirror.example.com/a.png" in text
    # URL 行内不得携带原始 CR/LF；行结构本身以换行分隔，逐行断言 URL 行形态。
    url_line = next(line for line in text.splitlines() if line.startswith("  URL: "))
    assert "\r" not in url_line
    assert url_line == "  URL: https://mirror.example.com/a.png  FAKE-LINE api_key=***"


def test_structured_data_url_field_sanitized() -> None:
    """structuredContent.data 项的 url 字段净化，与文本通道防护对称。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": _DIRTY_URL}],
    }

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    url = structured["data"][0]["url"]
    assert isinstance(url, str)
    assert "AKID:SECRET@" not in url
    assert "SECRET" not in url
    assert "\r" not in url
    assert "\n" not in url
    assert "mirror.example.com/a.png" in url


def test_structured_data_clean_url_passed_through_without_copy() -> None:
    """无凭据无控制字符的 URL 净化后值不变，data 项不产生多余拷贝。"""
    images = [{"url": "https://example.com/clean.png"}]

    sanitized = _sanitize_image_errors(images)

    assert sanitized[0] is images[0]


# ==================== URL 数据字段不截断（签名 URL 回归） ====================


def test_long_signed_url_preserved_intact_after_sanitization() -> None:
    """约 674 字符的签名 URL 净化后完整保留：数据字段不做错误文本的 500 字符截断。"""
    assert len(_SIGNED_URL) > 500

    text = format_generation_response(
        "文生图任务完成",
        {"success": True, "status": "completed", "data": [{"url": _SIGNED_URL}]},
        "test",
        "2K",
    )

    url_line = next(line for line in text.splitlines() if line.startswith("  URL: "))
    assert url_line == f"  URL: {_SIGNED_URL}"
    assert "truncated" not in url_line


def test_long_url_with_credentials_still_stripped_without_truncation() -> None:
    """超长 URL 的 userinfo 凭据剥离仍生效，剥离后的 URL 完整保留。"""
    long_url = "https://AKID:" + "p" * 600 + "@mirror.example.com/a.png?sig=abc"
    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result={"success": True, "status": "completed", "data": [{"url": long_url}]},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    url = structured["data"][0]["url"]
    assert url == "https://mirror.example.com/a.png?sig=abc"
    assert "AKID" not in url
    assert "truncated" not in url


def test_auto_save_result_to_dict_preserves_long_original_url() -> None:
    """to_dict 的 original_url 为数据字段：净化不截断，长签名 URL 完整保留。"""
    save_result = AutoSaveResult(success=False, original_url=_SIGNED_URL, error="下载失败")

    payload = save_result.to_dict()

    assert payload["original_url"] == _SIGNED_URL
    assert "truncated" not in payload["original_url"]


# ==================== 上游自由字段脱敏 ====================


def _dirty_free_field_result() -> dict[str, Any]:
    """size/output_format/model/type/error.code 均携带 CRLF 的结果，验证换行注入防护。"""
    return {
        "success": True,
        "status": "partial",
        "data": [
            {
                "size": "2K\r\nFAKE-SIZE: injected",
                "output_format": "png\r\nFAKE-FORMAT: injected",
                "model": "doubao-seedream-5.0\r\nFAKE-MODEL: injected",
                "type": "image_generation.completed\r\nFAKE-TYPE: injected",
                "error": {"code": "E-1\r\nFAKE-CODE: injected", "message": "boom"},
            }
        ],
    }


def test_image_item_free_fields_sanitized_in_text_output() -> None:
    """文本通道 size/output_format/错误码行净化：CRLF 压平，无换行注入。"""
    text = format_generation_response("文生图任务完成", _dirty_free_field_result(), "test", "2K")

    # model/type 不在文本通道渲染，其 CRLF 注入防护经结构化通道断言覆盖。
    assert "\r" not in text
    size_line = next(line for line in text.splitlines() if line.startswith("  尺寸: "))
    format_line = next(line for line in text.splitlines() if line.startswith("  输出格式: "))
    code_line = next(line for line in text.splitlines() if line.startswith("  错误码: "))
    assert size_line == "  尺寸: 2K  FAKE-SIZE: injected"
    assert format_line == "  输出格式: png  FAKE-FORMAT: injected"
    assert code_line == "  错误码: E-1  FAKE-CODE: injected"


def test_structured_data_free_fields_sanitized() -> None:
    """structuredContent.data 项的 size/output_format/model/type/error.code 净化。"""
    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=_dirty_free_field_result(),
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    item = structured["data"][0]
    assert item["size"] == "2K  FAKE-SIZE: injected"
    assert item["output_format"] == "png  FAKE-FORMAT: injected"
    assert item["model"] == "doubao-seedream-5.0  FAKE-MODEL: injected"
    assert item["type"] == "image_generation.completed  FAKE-TYPE: injected"
    assert item["error"]["code"] == "E-1  FAKE-CODE: injected"


def test_auto_save_section_failure_error_sanitized_in_text_output() -> None:
    """保存失败行净化：error 携带 CRLF 与敏感键值时不注入换行、不泄露凭据。"""
    save_result = AutoSaveResult(
        success=False,
        original_url="https://example.com/x.png",
        error="下载失败\r\nFAKE api_key=sk-leaked",
    )

    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [save_result],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    # 摘要行「自动保存: 0/1 成功」不含破折号，用明细行特有的「保存失败 -」前缀定位。
    failure_line = next(line for line in text.splitlines() if "保存失败 -" in line)
    assert failure_line == "  图片 2: 保存失败 - 下载失败  FAKE api_key=***"
    assert "sk-leaked" not in text


def test_auto_save_result_to_dict_sanitizes_original_url() -> None:
    """to_dict 的 original_url 过净化管线：userinfo 剥离、CRLF 压平。"""
    save_result = AutoSaveResult(success=False, original_url=_DIRTY_URL, error="下载失败")

    payload = save_result.to_dict()

    url = payload["original_url"]
    assert isinstance(url, str)
    assert "AKID:SECRET@" not in url
    assert "SECRET" not in url
    assert "\r" not in url
    assert "\n" not in url
    assert "mirror.example.com/a.png" in url


# ==================== 文本通道收敛：提示词回显与路径折叠 ====================


def test_prompt_not_echoed_in_text_channel() -> None:
    """提示词不在文本通道回显：调用方刚发送过，structuredContent.prompt 已携带。"""
    text = format_generation_response(
        "文生图任务完成",
        {"success": True, "status": "completed", "data": [{"url": "https://example.com/a.png"}]},
        "a very long prompt about a cat",
        "2K",
    )

    assert "提示词" not in text
    assert "a very long prompt" not in text


def test_url_line_omitted_when_local_path_present() -> None:
    """保存成功条目省略 URL 行，取回结果以本地路径为准；Markdown 引用行不再输出。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": "https://example.com/a.png",
                "local_path": "images/a.png",
                "markdown_ref": "![a](images/a.png)",
            }
        ],
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")

    assert "URL:" not in text
    assert "Markdown 引用" not in text
    assert "  本地路径: images/a.png" in text


def test_url_line_kept_when_auto_save_disabled() -> None:
    """自动保存关闭时无本地路径，URL 是取回结果的唯一途径，保留输出。"""
    text = format_generation_response(
        "文生图任务完成",
        {"success": True, "status": "completed", "data": [{"url": "https://example.com/a.png"}]},
        "test",
        "2K",
        auto_save_enabled=False,
    )

    assert "  URL: https://example.com/a.png" in text


def test_url_line_kept_when_save_degraded_to_url() -> None:
    """保存失败降级保留 URL 的场景：无 local_path，URL 行仍输出。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=False)],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    assert "  URL: https://example.com/ok.png" in text


def test_single_image_text_form_is_compact() -> None:
    """单图成功保存的文本形态：标题/尺寸/图片条目/摘要，计数只保留摘要一处。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": "https://example.com/1.png",
                "local_path": "images/1.png",
                "markdown_ref": "![1](images/1.png)",
            }
        ],
        "usage": {"generated_images": 1},
    }
    save_results = [
        AutoSaveResult(
            success=True,
            original_url="https://example.com/1.png",
            local_path="images/1.png",
            markdown_ref="![1](images/1.png)",
        )
    ]

    text = format_generation_response(
        "文生图任务完成",
        result,
        "test",
        "2K",
        save_results,
        auto_save_enabled=True,
        saveable_indices=[0],
    )

    assert "自动保存: 1/1 成功" in text
    assert "总图片数" not in text
    assert "成功保存" not in text
    assert "生成图片数" not in text
    assert "已保存到" not in text
    assert text.count("images/1.png") == 1


def test_fifteen_image_batch_text_form_has_no_duplicate_path_lines() -> None:
    """15 张组图：每张路径在文本中仅出现一次，无重复路径行与重复计数。"""
    images = [
        {
            "url": f"https://example.com/{i}.png",
            "local_path": f"images/{i}.png",
            "markdown_ref": f"![image {i}](images/{i}.png)",
        }
        for i in range(1, 16)
    ]
    save_results = [
        AutoSaveResult(
            success=True,
            original_url=f"https://example.com/{i}.png",
            local_path=f"images/{i}.png",
            markdown_ref=f"![image {i}](images/{i}.png)",
        )
        for i in range(1, 16)
    ]

    text = format_generation_response(
        "组图任务完成",
        {"success": True, "status": "completed", "data": images, "usage": {"generated_images": 15}},
        "test",
        "2K",
        save_results,
        auto_save_enabled=True,
        saveable_indices=list(range(15)),
    )

    assert "自动保存: 15/15 成功" in text
    assert "生成图片数" not in text
    for i in range(1, 16):
        # URL 行与 Markdown 引用行均已收敛，路径仅在「本地路径」行出现一次。
        assert text.count(f"images/{i}.png") == 1


# ==================== truncated_events 双通道消费 ====================


def test_truncated_events_surfaced_in_both_channels() -> None:
    """result 含 truncated_events=2 时文本与结构化通道均体现丢弃提示。"""
    result = {
        "success": True,
        "status": "partial",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": {"generated_images": 1},
        "truncated_events": 2,
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")
    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "因单事件体积超限丢弃 2 个事件" in text
    assert structured["truncated_events"] == 2


def test_truncated_events_absent_or_zero_not_rendered() -> None:
    """未发生丢弃（缺键或 0）时两通道均不输出该信息。"""
    base = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": {"generated_images": 1},
    }

    for result in (base, {**base, "truncated_events": 0}):
        text = format_generation_response("文生图任务完成", result, "test", "2K")
        assert "丢弃" not in text

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result={**base, "truncated_events": 0},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )
    assert structured["truncated_events"] is None


# ==================== 双重净化收敛 ====================


def test_sanitize_image_errors_second_pass_is_noop() -> None:
    """同一列表重复净化只执行一次：超长片段的截断标记不叠加。"""
    images = [{"error": {"code": "E", "message": "x" * 600}}]

    first = _sanitize_image_errors(images)
    second = _sanitize_image_errors(images)

    assert second is first
    message = second[0]["error"]["message"]
    assert message.count("<truncated:") == 1


# ==================== usage 字段净化 ====================


def test_structured_usage_string_values_sanitized() -> None:
    """usage 的字符串值过净化管线：CRLF 压平、凭据剥离，数值键保持原值。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": {
            "generated_images": 1,
            "output_tokens": 100,
            "note": "echo api_key=leaked\r\nFAKE",
            "tool_usage": {"web_search": 2, "label": "Bearer sk-1"},
        },
    }

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    usage = structured["usage"]
    assert usage["generated_images"] == 1
    assert usage["output_tokens"] == 100
    assert usage["tool_usage"]["web_search"] == 2
    assert "leaked" not in str(usage)
    assert "sk-1" not in str(usage)
    assert "\r" not in str(usage)
    assert "\n" not in str(usage)


def test_usage_text_renders_numeric_values_only() -> None:
    """文本统计仅渲染数值取值：字符串值经插值会把换行注入文本通道。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": {"output_tokens": "100\r\nFAKE: injected", "total_tokens": 50},
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")

    assert "FAKE" not in text
    assert "总 tokens: 50" in text
    assert "输出 tokens" not in text


# ==================== usage 净化遍历健壮性 ====================


def test_structured_usage_deeply_nested_sanitized_without_recursion_error() -> None:
    """600 层嵌套 dict 的 usage 净化正常完成：迭代实现不触发解释器递归上限。"""
    nested: dict[str, Any] = {"value": "echo\r\nFAKE"}
    for _ in range(600):
        nested = {"nested": nested, "label": "x"}

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result={
            "success": True,
            "status": "completed",
            "data": [{"url": "https://example.com/a.png"}],
            "usage": nested,
        },
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    usage = structured["usage"]
    assert usage["label"] == "x"
    # 逐层下潜 600 层取到内层字符串，全程无 RecursionError。
    current: Any = usage
    for _ in range(600):
        current = current["nested"]
    assert current["value"] == "echo  FAKE"


def test_structured_usage_cyclic_reference_terminated_with_placeholder() -> None:
    """usage 含循环引用时以 <truncated:cyclic> 摘要终止展开，不无限循环。"""
    cyclic: dict[str, Any] = {"note": "echo"}
    cyclic["self"] = cyclic

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result={
            "success": True,
            "status": "completed",
            "data": [{"url": "https://example.com/a.png"}],
            "usage": cyclic,
        },
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    usage = structured["usage"]
    assert usage["note"] == "echo"
    assert usage["self"]["self"] == "<truncated:cyclic>"


# ==================== local_path/markdown_ref 与未知键净化 ====================


def test_forged_local_path_and_markdown_ref_sanitized_in_both_channels() -> None:
    """上游伪造 local_path/markdown_ref 携带 CRLF：文本与结构化通道均无换行注入。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": "https://example.com/a.png",
                "local_path": "images/a.png\r\nFAKE-PATH: injected",
                "markdown_ref": "![a](images/a.png)\r\nFAKE-REF: injected",
            }
        ],
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")
    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "\r" not in text
    path_line = next(line for line in text.splitlines() if line.startswith("  本地路径: "))
    assert path_line == "  本地路径: images/a.png  FAKE-PATH: injected"
    item = structured["data"][0]
    assert item["local_path"] == "images/a.png  FAKE-PATH: injected"
    assert item["markdown_ref"] == "![a](images/a.png)  FAKE-REF: injected"


def test_structured_data_unknown_string_keys_sanitized() -> None:
    """extra='allow' 直通的未知字符串键统一净化：CRLF 压平、凭据剥离后保留。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": "https://example.com/a.png",
                "custom_note": "hi\r\nFAKE api_key=leaked",
                "custom_count": 7,
            }
        ],
    }

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    item = structured["data"][0]
    note = item["custom_note"]
    assert "\r" not in note
    assert "\n" not in note
    assert "leaked" not in note
    assert "hi" in note
    # 非字符串未知值不属字符串净化范围，原样保留。
    assert item["custom_count"] == 7


# ==================== 净化哨兵复位协议 ====================


def test_reset_last_sanitized_images_clears_sentinel_slot() -> None:
    """复位函数清空哨兵槽位：清空后槽位不再持有图片列表引用。"""
    images = [{"url": "https://example.com/a.png"}]
    _sanitize_image_errors(images)
    assert results_module._last_sanitized_images is images

    reset_last_sanitized_images()

    assert results_module._last_sanitized_images is None


def test_failure_path_structured_result_resets_sanitized_sentinel() -> None:
    """失败路径结构化出口用后复位哨兵：失败批次的图片列表不滞留槽位至下一次调用。

    失败路径的文本出口经 _format_failure_section 提前返回、不经净化，结构化出口是
    首个也是末个消费者；不复位时哨兵会持有失败批次图片直至下一次生成调用覆盖。
    """
    # 先净化一份无关列表，模拟上一次调用在哨兵槽位的滞留。
    _sanitize_image_errors([{"url": "https://example.com/prev.png"}])
    result = {
        "success": False,
        "status": "failed",
        "error": {"message": "boom"},
        "data": [{"url": "https://example.com/a.png"}],
    }

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["success"] is False
    assert results_module._last_sanitized_images is None


def test_success_path_pipeline_sanitization_ends_with_cleared_sentinel() -> None:
    """成功路径文本与结构化先后净化同一列表，流水线结束后哨兵为空、无引用滞留。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
    }
    images = extract_images(result)

    text = format_generation_response("文生图任务完成", result, "test", "2K", images=images)
    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
        images=images,
    )

    assert "URL: https://example.com/a.png" in text
    assert structured["data"][0]["url"] == "https://example.com/a.png"
    assert results_module._last_sanitized_images is None


# ==================== 请求级失败错误渲染 ====================


def test_failure_text_renders_top_level_error_message_not_unknown() -> None:
    """请求级软失败经结果结构透传 error 时，失败文案渲染真实原因而非未知错误。"""
    result = {
        "success": False,
        "status": "failed",
        "data": [],
        "error": {"code": "StreamRejected", "message": "流式请求被拒绝\r\nFAKE api_key=leaked"},
    }

    text = format_generation_response("文生图任务完成", result, "test", "2K")

    assert "图片生成失败: 流式请求被拒绝" in text
    assert "未知错误" not in text
    assert "leaked" not in text
    assert "\r" not in text


def test_structured_failure_error_code_sanitized() -> None:
    """失败分支 error.code 为上游自由文本：净化后进入 structuredContent，无换行注入。"""
    result = {
        "success": False,
        "status": "failed",
        "error": {"code": "E\r\nFAKE api_key=leaked", "message": "boom"},
    }

    structured = _build_generation_structured_result(
        tool_name="seedream_text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["error"]["code"] == "E  FAKE api_key=***"
    assert "leaked" not in str(structured["error"])


# ==================== 未知键净化遍历健壮性 ====================


def test_unknown_value_deeply_nested_sanitized_without_recursion_error() -> None:
    """950 层嵌套 list 的未知键值净化正常完成：迭代展开不触发解释器递归上限。

    深处的字符串仍被净化，凭据片段与 CRLF 不进入 structuredContent。
    """
    deep: Any = "echo\r\nFAKE api_key=leaked"
    for _ in range(950):
        deep = [deep]
    images = [{"url": "https://example.com/a.png", "custom_meta": deep}]

    sanitized = _sanitize_image_errors(images)

    current: Any = sanitized[0]["custom_meta"]
    for _ in range(950):
        current = current[0]
    assert current == "echo  FAKE api_key=***"


def test_unknown_value_cyclic_reference_terminated_with_placeholder() -> None:
    """未知键值含循环引用时以 <truncated:cyclic> 占位终止展开，不无限循环。"""
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    images = [{"url": "https://example.com/a.png", "custom_meta": cyclic}]

    sanitized = _sanitize_image_errors(images)

    assert sanitized[0]["custom_meta"][0][0] == "<truncated:cyclic>"
