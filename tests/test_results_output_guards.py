"""results 输出格式化守护测试：自动保存编号基准与上游 URL 脱敏。

锁定两条输出契约：自动保存段落的图片编号与图片列表段落同基准，取可保存图片在
extract_images 归一化列表中的原始索引；文本与结构化输出的 URL 统一过
sanitize_error_text，userinfo 凭据与 CRLF 不进入任一通道。
"""

from __future__ import annotations

from typing import Any

from seedream_mcp.tools.core.context import GenerationExecutionContext
from seedream_mcp.tools.core.results import (
    _build_generation_structured_result,
    format_generation_response,
)
from seedream_mcp.utils.io.io_save import AutoSaveResult

# 带 userinfo 凭据与 CRLF 的上游 URL：净化后凭据被剥离、换行被压平。
_DIRTY_URL = "https://AKID:SECRET@mirror.example.com/a.png\r\nFAKE-LINE api_key=leaked"


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


# ==================== 自动保存编号基准 ====================


def test_auto_save_section_numbers_use_original_image_indices() -> None:
    """混合成败时保存段落编号与图片列表同基准：保存的是图片 2 而非图片 1。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=True)],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    # 图片列表：图片 1 为失败占位项，图片 2 为成功图。
    assert "图片 1:" in text
    assert "状态: 失败" in text
    assert "图片 2: 已保存到 images/ok.png" in text
    # 旧的紧凑序号形态不应出现：保存项不得再被编为图片 1。
    assert "图片 1: 已保存到" not in text


def test_auto_save_section_failed_save_uses_original_image_index() -> None:
    """保存失败的条目同样按原始索引编号，与成功条目编号基准一致。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=False)],
        auto_save_enabled=True,
        saveable_indices=[1],
    )

    assert "图片 2: 保存失败 - 下载失败" in text
    assert "图片 1: 保存失败" not in text


def test_auto_save_section_falls_back_to_save_ordinal_without_indices() -> None:
    """未提供索引时回退保存序号，直接调用方仍可得到连续编号。"""
    text = format_generation_response(
        "文生图任务完成",
        _mixed_result(),
        "test",
        "2K",
        [_save_result(success=True)],
        auto_save_enabled=True,
    )

    assert "图片 1: 已保存到 images/ok.png" in text


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
    from seedream_mcp.tools.core.results import _sanitize_image_errors

    images = [{"url": "https://example.com/clean.png"}]

    sanitized = _sanitize_image_errors(images)

    assert sanitized[0] is images[0]


# ==================== 上游自由字段脱敏 ====================


def _dirty_free_field_result() -> dict[str, Any]:
    """size/output_format/error.code 均携带 CRLF 的图片结果，验证换行注入防护。"""
    return {
        "success": True,
        "status": "partial",
        "data": [
            {
                "size": "2K\r\nFAKE-SIZE: injected",
                "output_format": "png\r\nFAKE-FORMAT: injected",
                "error": {"code": "E-1\r\nFAKE-CODE: injected", "message": "boom"},
            }
        ],
    }


def test_image_item_free_fields_sanitized_in_text_output() -> None:
    """文本通道 size/output_format/错误码行净化：CRLF 压平，无换行注入。"""
    text = format_generation_response("文生图任务完成", _dirty_free_field_result(), "test", "2K")

    size_line = next(line for line in text.splitlines() if line.startswith("  尺寸: "))
    format_line = next(line for line in text.splitlines() if line.startswith("  输出格式: "))
    code_line = next(line for line in text.splitlines() if line.startswith("  错误码: "))
    assert size_line == "  尺寸: 2K  FAKE-SIZE: injected"
    assert format_line == "  输出格式: png  FAKE-FORMAT: injected"
    assert code_line == "  错误码: E-1  FAKE-CODE: injected"


def test_structured_data_free_fields_sanitized() -> None:
    """structuredContent.data 项的 size/output_format/error.code 净化，与文本通道对称。"""
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

    # 统计行「保存失败: 1」不含破折号，用条目行特有的「保存失败 -」前缀定位。
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
