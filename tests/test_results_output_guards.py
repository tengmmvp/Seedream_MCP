"""results 输出格式化守护测试：自动保存摘要形态、路径折叠与上游 URL 脱敏。

自动保存段落折叠为 N/M 摘要、仅失败项保留明细且编号与图片列表同基准；保存路径
每张图仅出现一次；URL 为数据字段，净化剥离 userinfo 凭据与 CRLF 但不截断。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from seedream_mcp.tools.core import results as results_module
from seedream_mcp.tools.core._helpers import _extract_parallel_request_error
from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.results import (
    _build_generation_structured_result,
    _sanitize_image_errors,
    extract_images,
    format_generation_response,
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
        layer_decomposition=False,
        background=None,
        max_images=None,
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
    # 成功项不再有「已保存到」明细行，路径全文仅出现一次。
    assert "已保存到" not in text
    assert text.count("images/ok.png") == 1


def test_auto_save_section_failed_save_uses_original_image_index() -> None:
    """保存失败的条目按原始索引编号，与图片列表编号基准一致。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
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

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "AKID:SECRET@" not in text
    assert "SECRET" not in text
    assert "mirror.example.com/a.png" in text
    # URL 行内不得携带原始 CR/LF，逐行断言 URL 行形态。
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
        tool_name="text_to_image",
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


def test_structured_data_non_dict_error_sanitized() -> None:
    """data 项 error 为非 dict 形态时同样净化，不借形态绕过注入面。

    字符串与容器形态同为上游自由内容，与顶层 error 的非 dict 净化路径对称。
    """
    result = {
        "success": True,
        "status": "partial",
        "data": [
            {"error": "boom\r\nAuthorization: Bearer sk-leaked"},
            {"error": ["line\r\n", "api_key=leaked"]},
        ],
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    string_error = structured["data"][0]["error"]
    assert isinstance(string_error, str)
    assert "\r" not in string_error
    assert "\n" not in string_error
    assert "sk-leaked" not in string_error
    assert "Bearer" not in string_error

    container_error = structured["data"][1]["error"]
    assert isinstance(container_error, list)
    assert "\r" not in container_error[0]
    assert "leaked" not in container_error[1]
    assert "***" in container_error[1]


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
        "2K",
    )

    url_line = next(line for line in text.splitlines() if line.startswith("  URL: "))
    assert url_line == f"  URL: {_SIGNED_URL}"
    assert "truncated" not in url_line


def test_long_url_with_credentials_still_stripped_without_truncation() -> None:
    """超长 URL 的 userinfo 凭据剥离仍生效，剥离后的 URL 完整保留。"""
    long_url = "https://AKID:" + "p" * 600 + "@mirror.example.com/a.png?sig=abc"
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
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
    text = format_generation_response("文生图任务完成", _dirty_free_field_result(), "2K")

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
        tool_name="text_to_image",
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
    """提示词不在文本通道回显：调用方刚发送过，structuredContent.prompt 已携带。

    锁定完整文本形态，任何新增回显行都会使全等断言失败。
    """
    text = format_generation_response(
        "文生图任务完成",
        {"success": True, "status": "completed", "data": [{"url": "https://example.com/a.png"}]},
        "2K",
    )

    assert text == (
        "文生图任务完成\n" "尺寸: 2K\n" "\n" "图片 1:\n" "  URL: https://example.com/a.png\n"
    )
    assert "提示词" not in text


def test_url_line_kept_alongside_local_path() -> None:
    """保存成功条目 URL 行与本地路径行并存：URL 是模型展示图片的直接载体。"""
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

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "  URL: https://example.com/a.png" in text
    assert "Markdown 引用" not in text
    assert "  本地路径: images/a.png" in text


def test_url_line_kept_when_auto_save_disabled() -> None:
    """自动保存关闭时无本地路径，URL 是取回结果的唯一途径，保留输出。"""
    text = format_generation_response(
        "文生图任务完成",
        {"success": True, "status": "completed", "data": [{"url": "https://example.com/a.png"}]},
        "2K",
        auto_save_enabled=False,
    )

    assert "  URL: https://example.com/a.png" in text


def test_url_line_kept_when_save_degraded_to_url() -> None:
    """保存失败降级保留 URL 的场景：无 local_path，URL 行仍输出。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
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
        "2K",
        save_results,
        auto_save_enabled=True,
        saveable_indices=[0],
    )

    assert "自动保存: 1/1 成功" in text
    assert "  URL: https://example.com/1.png" in text
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
        "2K",
        save_results,
        auto_save_enabled=True,
        saveable_indices=list(range(15)),
    )

    assert "自动保存: 15/15 成功" in text
    assert "生成图片数" not in text
    for i in range(1, 16):
        # URL 行与本地路径行各自输出，markdown_ref 行不输出，路径文本仅出现一次。
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

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
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
        text = format_generation_response("文生图任务完成", result, "2K")
        assert "丢弃" not in text

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result={**base, "truncated_events": 0},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )
    assert structured["truncated_events"] is None


# ==================== 双重净化收敛 ====================


def test_pipeline_single_sanitization_shared_by_both_outlets() -> None:
    """流水线净化一次并共用同一列表：两出口的截断标记均恰有一次。

    重复净化非幂等，截断标记会逐次叠加、内容逐次缩水；已净化列表再入净化管线的
    对照行为由末条断言证明。
    """
    result = {
        "success": True,
        "status": "completed",
        "data": [{"error": {"message": "x" * 600}}],
    }
    sanitized_images = _sanitize_image_errors(extract_images(result))

    text = format_generation_response("文生图任务完成", result, "2K", images=sanitized_images)
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
        images=sanitized_images,
    )

    message = structured["data"][0]["error"]["message"]
    assert message.count("<truncated:") == 1
    assert "错误信息:" in text
    assert "<truncated:" in text
    # 已净化文本再次进入净化会二次截断，对照证明上方结果来自单次净化。
    re_sanitized = _sanitize_image_errors([{"error": {"message": message}}])
    assert re_sanitized[0]["error"]["message"] != message


def test_independent_structured_call_sanitizes_internally() -> None:
    """独立调用未传 images 时在结构化出口内部完成首次净化，截断标记恰一次。"""
    images = [{"error": {"code": "E", "message": "x" * 600}}]

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result={"success": True, "status": "completed", "data": images},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["data"][0]["error"]["message"].count("<truncated:") == 1


def test_sanitize_image_errors_does_not_mutate_input_list() -> None:
    """净化返回新列表，传入列表不被就地改写：调用方持有的原始数据不受出口净化影响。"""
    images = [{"url": "https://AKID:SECRET@mirror.example.com/a.png", "size": "2K\r\nFAKE"}]

    sanitized = _sanitize_image_errors(images)

    assert images[0]["url"] == "https://AKID:SECRET@mirror.example.com/a.png"
    assert images[0]["size"] == "2K\r\nFAKE"
    assert sanitized[0]["url"] == "https://mirror.example.com/a.png"
    assert sanitized[0]["size"] == "2K  FAKE"


def test_aggregated_failure_message_not_resanitized_in_both_outlets() -> None:
    """聚合格式结果的失败消息已在源头净化：两出口直接渲染，截断标记不叠加。

    消息总长超过截断上限且已携带截断标记，出口若重复净化会再次截断叠加第二个
    标记。
    """
    truncated_message = "<truncated:600 chars> " + "x" * 500
    result = {
        "success": False,
        "status": "failed",
        "error": {"type": "api_error", "message": truncated_message},
        "batch": {
            "request_count": 1,
            "success_requests": 0,
            "failed_requests": 1,
            "errors": [{"request_index": 1, "message": truncated_message}],
        },
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert f"图片生成失败: {truncated_message}" in text
    assert f"  请求 1: {truncated_message}" in text
    assert structured["error"]["message"] == truncated_message


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
        tool_name="text_to_image",
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

    text = format_generation_response("文生图任务完成", result, "2K")

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
        tool_name="text_to_image",
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
        tool_name="text_to_image",
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

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
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
        tool_name="text_to_image",
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


# ==================== 净化协调与模块状态移除 ====================


def test_module_level_sanitized_sentinel_state_removed() -> None:
    """模块级净化哨兵不复存在：净化状态随调用链显式传递，无跨调用模块状态可滞留。

    旧行为：哨兵依赖两条出口间无 await 的隐式时序，失败批次的图片列表会滞留槽位。
    """
    assert not hasattr(results_module, "_last_sanitized_images")
    assert not hasattr(results_module, "reset_last_sanitized_images")


def test_failure_path_structured_outlet_sanitizes_images() -> None:
    """失败路径文本出口经 _format_failure_section 提前返回，结构化出口完成净化。

    独立调用未传 images 时净化发生在结构化出口内部，凭据不借 data 项进入
    structuredContent。
    """
    result = {
        "success": False,
        "status": "failed",
        "error": {"message": "boom"},
        "data": [{"url": "https://AKID:SECRET@mirror.example.com/a.png"}],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["success"] is False
    assert "SECRET" not in text
    assert "SECRET" not in structured["data"][0]["url"]


def test_success_path_pipeline_sanitizes_each_outlet_content_once() -> None:
    """成功路径净化一次，文本与结构化出口共用同一净化列表。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
    }
    sanitized_images = _sanitize_image_errors(extract_images(result))

    text = format_generation_response("文生图任务完成", result, "2K", images=sanitized_images)
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
        images=sanitized_images,
    )

    assert "URL: https://example.com/a.png" in text
    assert structured["data"][0]["url"] == "https://example.com/a.png"


# ==================== 请求级失败错误渲染 ====================


def test_failure_text_renders_top_level_error_message_not_unknown() -> None:
    """请求级软失败经结果结构透传 error 时，失败文案渲染真实原因而非未知错误。"""
    result = {
        "success": False,
        "status": "failed",
        "data": [],
        "error": {"code": "StreamRejected", "message": "流式请求被拒绝\r\nFAKE api_key=leaked"},
    }

    text = format_generation_response("文生图任务完成", result, "2K")

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
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["error"]["code"] == "E  FAKE api_key=***"
    assert "leaked" not in str(structured["error"])


# ==================== 非 str error.message 归一化 ====================


def test_failure_text_dict_message_normalized_and_sanitized() -> None:
    """请求级 error.message 为 dict 形态时归一化为文本后脱敏，凭据不进入文本通道。"""
    result = {
        "success": False,
        "status": "failed",
        "error": {"message": {"authorization": "Bearer sk-text-leaked"}},
    }

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "图片生成失败:" in text
    assert "sk-text-leaked" not in text
    assert "***" in text


def test_failure_text_list_message_normalized_and_sanitized() -> None:
    """error.message 为 list 形态同样归一化，键值凭据不借 repr 插值穿透文本通道。"""
    result = {
        "success": False,
        "status": "failed",
        "error": {"message": ["api_key=SK-LIST-LEAK"]},
    }

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "SK-LIST-LEAK" not in text
    assert "api_key=***" in text


def test_failure_text_non_dict_error_normalized_and_sanitized() -> None:
    """顶层 error 为非 dict 形态时整体归一化，凭据不借 list 形态穿透文本通道。"""
    result = {
        "success": False,
        "status": "failed",
        "error": ["Authorization: Bearer sk-top-leaked"],
    }

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "sk-top-leaked" not in text
    assert "***" in text


def test_structured_failure_dict_message_normalized_and_sanitized() -> None:
    """结构化出口的非 str error.message 归一化为文本后脱敏，凭据不进入 structuredContent。"""
    result = {
        "success": False,
        "status": "failed",
        "error": {"code": "E", "message": {"authorization": "Bearer sk-struct-leaked"}},
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    message = structured["error"]["message"]
    assert isinstance(message, str)
    assert "sk-struct-leaked" not in message
    assert "***" in message


def test_structured_failure_list_message_normalized_and_sanitized() -> None:
    """结构化出口对 list 形态 message 同样归一化，凭据样式片段被剥离。"""
    result = {
        "success": False,
        "status": "failed",
        "error": {"message": ["token=SK-LIST-STRUCT"]},
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    message = structured["error"]["message"]
    assert isinstance(message, str)
    assert "SK-LIST-STRUCT" not in message
    assert "token=***" in message


def test_structured_failure_non_dict_error_normalized_and_sanitized() -> None:
    """顶层 error 为 list 形态走归一化兜底分支，凭据不进入 structuredContent.error。"""
    result = {
        "success": False,
        "status": "failed",
        "error": ["api_key=SK-NONDICT-STRUCT"],
    }

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    rendered = str(structured["error"])
    assert "SK-NONDICT-STRUCT" not in rendered
    assert "***" in rendered


# ==================== 未知键净化遍历健壮性 ====================


def test_unknown_value_deeply_nested_sanitized_without_recursion_error() -> None:
    """950 层嵌套 list 的未知键值净化正常完成：迭代展开不触发递归上限，深处字符串仍被净化。"""
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


def test_parallel_error_code_fallback_branch_is_sanitized() -> None:
    """code 回退分支与 message 同口径脱敏，被劫持上游无法经 code 注入换行与凭据。

    上游 200 响应顶层 error 仅含 code 键时其自由文本直达 structuredContent，
    该分支漏脱敏会原样透传。
    """
    message = _extract_parallel_request_error(
        {"error": {"code": "InjectedHeader\r\nAuthorization: Bearer leak"}}, None
    )

    assert "\r" not in message and "\n" not in message
    assert "leak" not in message


def test_extract_images_handles_deeply_nested_data_without_recursion_error() -> None:
    """深嵌套 {"data": ...} 链经迭代下钻提取，不因 RecursionError 使成功生成翻错。

    json.loads 的 C 层栈开销低于 Python 帧，千级嵌套可成功解析；递归实现会抛
    RecursionError 并被降级为错误结果。
    """
    inner: dict[str, Any] = {"url": "https://example.com/deep.png"}
    result: dict[str, Any] = inner
    for _ in range(1500):
        result = {"data": result}

    images = extract_images(result)

    assert images == [inner]


def test_structured_status_sanitized_and_max_images_surfaced() -> None:
    """status 上游原文经净化进入 structuredContent，max_images 生效值原样回显。"""
    context = dataclasses.replace(_context(), max_images=4)
    structured = _build_generation_structured_result(
        tool_name="sequential_generation",
        result={"success": True, "status": "ok\r\ninjected", "data": [], "usage": {}},
        context=context,
        auto_save_results=None,
        auto_save_error=None,
    )

    assert "\r" not in structured["status"]
    assert "\n" not in structured["status"]
    assert structured["max_images"] == 4


# ==================== 伪造序号字段净化 ====================


def test_forged_string_request_and_image_index_sanitized_in_both_channels() -> None:
    """单请求路径伪造 request_index/image_index 为 CRLF 自由文本：两通道均净化。

    跳过净化的前提是本侧聚合写入的整数序号，非 int 形态按错误文本净化。
    """
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": "https://example.com/a.png",
                "request_index": "1\r\nFAKE-REQ api_key=sk-idx-leaked",
                "image_index": "2\r\nFAKE-IDX api_key=sk-idx2-leaked",
            },
            {
                "url": "https://example.com/b.png",
                "request_index": 2,
                "image_index": 3,
            },
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "\r" not in text
    assert "sk-idx-leaked" not in text
    assert "sk-idx2-leaked" not in text
    request_line = next(line for line in text.splitlines() if line.startswith("  请求序号: "))
    index_line = next(line for line in text.splitlines() if line.startswith("  序号: "))
    assert request_line == "  请求序号: 1  FAKE-REQ api_key=***"
    assert index_line == "  序号: 2  FAKE-IDX api_key=***"
    assert structured["data"][0]["request_index"] == "1  FAKE-REQ api_key=***"
    assert structured["data"][0]["image_index"] == "2  FAKE-IDX api_key=***"
    # int 实例为本侧聚合写入的整数序号，保持原值直接渲染。
    assert "  请求序号: 2" in text
    assert "  序号: 3" in text
    assert structured["data"][1]["request_index"] == 2
    assert structured["data"][1]["image_index"] == 3


# ==================== per-image error 非字符串分量归一净化 ====================


def test_per_image_dict_error_message_normalized_and_sanitized() -> None:
    """per-image error.message 为 dict 形态：归一化为文本后脱敏，两通道不泄露凭据。"""
    result = {
        "success": True,
        "status": "partial",
        "data": [
            {
                "type": "image_generation.partial_failed",
                "error": {"message": {"authorization": "Bearer sk-perimage-leaked"}},
            }
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    message = structured["data"][0]["error"]["message"]
    assert isinstance(message, str)
    assert "sk-perimage-leaked" not in message
    assert "***" in message
    assert "sk-perimage-leaked" not in text
    message_line = next(line for line in text.splitlines() if line.startswith("  错误信息: "))
    assert "***" in message_line


def test_per_image_list_error_code_normalized_and_sanitized() -> None:
    """per-image error.code 为 list 形态：归一化净化后凭据片段不进入两通道。"""
    result = {
        "success": True,
        "status": "partial",
        "data": [
            {
                "type": "image_generation.partial_failed",
                "error": {"code": ["E\r\nFAKE api_key=leaked"]},
            }
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    code = structured["data"][0]["error"]["code"]
    assert isinstance(code, str)
    assert "leaked" not in code
    assert "api_key=***" in code
    assert "leaked" not in text


# ==================== b64_json 异形防翻错 ====================


def test_b64_json_non_sized_form_renders_absent_without_error() -> None:
    """伪造 b64_json 为不可计长度形态时不抛 TypeError，已计费结果保留成功输出。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {"url": "https://example.com/a.png", "b64_json": 12345},
            {"b64_json": "abcd"},
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "  URL: https://example.com/a.png" in text
    assert "  Base64 数据: 无" in text
    # 可计长度形态保持字符数输出。
    assert "  Base64 数据: 4 字符" in text


# ==================== error 键空值回落 ====================


def test_failure_text_none_error_value_falls_back_to_unknown() -> None:
    """error 键存在但值为 None 时回落未知错误，不渲染字面量 None。"""
    result = {"success": False, "status": "failed", "data": [], "error": None}

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "图片生成失败: 未知错误" in text
    assert "None" not in text


def test_structured_failure_none_error_value_falls_back_to_unknown() -> None:
    """结构化出口对 error=None 与文本通道同口径回落未知错误，message 不为字面 None。"""
    result = {"success": False, "status": "failed", "data": [], "error": None}

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )
    text = format_generation_response("文生图任务完成", result, "2K")

    assert structured["error"]["message"] == "未知错误"
    assert "None" not in str(structured["error"])
    assert "图片生成失败: 未知错误" in text
    assert "None" not in text


# ==================== dict error 缺键与空 message 的阶梯提取 ====================


def test_failure_text_dict_error_without_message_extracts_code_via_ladder() -> None:
    """dict error 缺 message 键时经五级阶梯落到 code，dict repr 不进入文本。"""
    result = {"success": False, "status": "failed", "data": [], "error": {"code": "E"}}

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "图片生成失败: E" in text
    assert "{'code'" not in text


def test_failure_text_dict_error_none_message_falls_back_to_unknown() -> None:
    """dict error 的 message 为 None 时回落未知错误，字面 None 不进入文本。"""
    result = {"success": False, "status": "failed", "data": [], "error": {"message": None}}

    text = format_generation_response("文生图任务完成", result, "2K")

    assert "图片生成失败: 未知错误" in text
    assert "None" not in text


# ==================== 非 str 数据字段净化 ====================


def test_non_str_url_size_local_path_sanitized_in_both_channels() -> None:
    """非 str 形态的 url/size/local_path 经容器逐层净化，凭据与 CRLF 不进入两通道。

    文本通道渲染归一化文本而非 Python repr；正常 str 形态与 int 标量保持原值。
    """
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {
                "url": {"Authorization": "Bearer sk-nonstr-leaked"},
                "size": ["2K\r\nFAKE api_key=leaked"],
                "local_path": {"p": "images/a.png\r\nFAKE-PATH: injected"},
            },
            {
                "url": "https://example.com/ok.png",
                "size": "2K",
                "local_path": 7,
            },
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    # 凭据与 CRLF 不进入文本通道，非 str 形态以归一化文本渲染。
    assert "sk-nonstr-leaked" not in text
    assert "api_key=leaked" not in text
    assert "\r" not in text
    url_line = next(line for line in text.splitlines() if line.startswith("  URL: "))
    size_line = next(line for line in text.splitlines() if line.startswith("  尺寸: "))
    path_line = next(line for line in text.splitlines() if line.startswith("  本地路径: "))
    assert url_line == '  URL: {"Authorization": "Bearer ***"}'
    assert size_line == '  尺寸: ["2K  FAKE api_key=***"]'
    assert path_line == '  本地路径: {"p": "images/a.png  FAKE-PATH: injected"}'
    # 结构化通道保留容器形态，嵌套字符串逐层净化。
    assert structured["data"][0]["url"] == {"Authorization": "Bearer ***"}
    assert structured["data"][0]["size"] == ["2K  FAKE api_key=***"]
    assert structured["data"][0]["local_path"] == {"p": "images/a.png  FAKE-PATH: injected"}
    # 正常 str 形态与 int 标量保持原值。
    assert "  URL: https://example.com/ok.png" in text
    assert "  尺寸: 2K" in text
    assert "  本地路径: 7" in text
    assert structured["data"][1]["url"] == "https://example.com/ok.png"
    assert structured["data"][1]["size"] == "2K"
    assert structured["data"][1]["local_path"] == 7


# ==================== bool 序号形态 ====================


def test_forged_bool_index_form_routed_through_sanitization_path() -> None:
    """bool 序号不占 int 快速通道：bool 子类排除口径与序号提取守卫一致。

    bool 保持布尔取值进入结构化通道，文本通道渲染归一化文本；int 实例仍直接保留。
    """
    result = {
        "success": True,
        "status": "completed",
        "data": [
            {"url": "https://example.com/a.png", "request_index": True},
            {"url": "https://example.com/b.png", "request_index": 2},
        ],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "  请求序号: True" in text
    assert structured["data"][0]["request_index"] is True
    # int 实例为本侧聚合写入的整数序号，保持原值直接渲染。
    assert "  请求序号: 2" in text
    assert structured["data"][1]["request_index"] == 2


# ==================== 顶层 status/usage/batch 形态自守 ====================


def test_malformed_top_level_shapes_do_not_flip_billed_success() -> None:
    """畸形顶层形态不使已计费成功生成翻错：文本与结构化两出口均正常产出。

    status 为 int、usage 为 str、batch 为 list 时，结构化出口按声明 schema 收敛，
    model 构造不抛校验异常。
    """
    result = {
        "success": True,
        "status": 200,
        "data": [{"url": "https://example.com/a.png"}],
        "usage": "not-a-dict",
        "batch": [1, 2],
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "URL: https://example.com/a.png" in text
    assert "使用统计" not in text
    assert "并行请求信息" not in text
    assert structured["status"] is None
    assert structured["usage"] == {}
    assert structured["batch"] is None
    assert structured["success"] is True
    assert structured["data"][0]["url"] == "https://example.com/a.png"


def test_malformed_status_shape_falls_back_to_none_in_structured_output() -> None:
    """非 str 的 status 归 None 后净化分支不触达，str 形态保持净化语义不变。"""
    structured_int = _build_generation_structured_result(
        tool_name="text_to_image",
        result={"success": True, "status": 200, "data": []},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )
    structured_str = _build_generation_structured_result(
        tool_name="text_to_image",
        result={"success": True, "status": "ok\r\ninjected", "data": []},
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured_int["status"] is None
    assert structured_str["status"] == "ok  injected"


def test_falsy_malformed_usage_batch_shapes_converge_quietly() -> None:
    """usage 与 batch 的空值畸形形态同样收敛：None/空 str 归空 dict 与 None。"""
    result = {
        "success": True,
        "status": "completed",
        "data": [{"url": "https://example.com/a.png"}],
        "usage": None,
        "batch": "",
    }

    text = format_generation_response("文生图任务完成", result, "2K")
    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=result,
        context=_context(),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert "使用统计" not in text
    assert structured["usage"] == {}
    assert structured["batch"] is None
