"""structuredContent 的 JSON 文本回传守护测试。

规范建议返回 structuredContent 的工具在 content 数组同时回传序列化 JSON；用例
覆盖生成工具成功（含预览块序）与异常失败、浏览工具成功、空结果与失败出口，
锁定追加块可经 json.loads 还原且净化无变化的干净内容与 structuredContent 等值；
含凭据样式或超长的字符串值在镜像中经数据通道掩码或截断，b64_json 载荷替换为
长度占位，structuredContent 字段本身均不受影响；镜像视图的 JSON 等值、键序与
原树不可变由直连用例锁定；非有限浮点使严格序列化失败并降级为无镜像结果，
不可见字符与 userinfo URL 的既有净化口径不被恒等快路绕过；镜像构建按载荷尺寸
估算分流：达到卸载阈值下沉 seedream-cpu-offload 专用线程池、小载荷内联构建，
b64_json 载荷按占位长度参与估算、普通长串仍按全长计量，两路径的降级语义一致，
由 spy 用例与阈值边界用例锁定。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.tools.core import outputs as outputs_module
from seedream_mcp.tools.core.outputs import (
    _mirror_node,
    build_structured_json_text,
    build_structured_tool_result,
    format_base64_placeholder,
)
from seedream_mcp.tools.core.schemas import BrowseImagesInput, ResponseFormat, TextToImageInput
from seedream_mcp.utils.core.executors import CPU_OFFLOAD_SIZE_THRESHOLD
from seedream_mcp.utils.core.sanitizers import estimate_output_length
from seedream_mcp.tools.impl.browse_images import handle_browse_images
from seedream_mcp.tools.runners import run_text_to_image
from seedream_mcp.utils.io import io_save

from _cpu_offload_spy import CpuOffloadSpy
from _generation_fixtures import _patch_client_success, _patch_save_real_file
from _log_fakes import capture_loguru_messages


def _text_blocks(result: CallToolResult) -> list[TextContent]:
    """提取结果的全部 TextContent 块，供形态计数与 JSON 回传块定位。"""
    return [content for content in result.content if isinstance(content, TextContent)]


async def test_generation_success_appends_json_text_before_previews(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """成功结果 content 为摘要与 JSON 回传块在前、预览缩略图在后，JSON 可还原等值。

    紧凑分隔符与非 ASCII 原文输出一并锁定：缩进与转义会使回传块成倍膨胀。
    """
    _patch_client_success(monkeypatch)
    _patch_save_real_file(monkeypatch, tmp_path)
    config = SeedreamConfig(api_key="test_key", data_root=str(tmp_path))
    # 预览的缓存根经环境链取活动配置，设为活动配置防止缓存落到仓库目录。
    set_active_config(config)

    result = await run_text_to_image(TextToImageInput(prompt="一只猫"), config)

    assert result.is_error is False
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert "文生图任务完成" in blocks[0].text
    # JSON 回传块位于摘要之后、预览 ImageContent 之前。
    assert all(isinstance(content, ImageContent) for content in result.content[2:])
    assert result.structured_content is not None
    assert json.loads(blocks[1].text) == result.structured_content
    assert "\n" not in blocks[1].text
    assert "一只猫" in blocks[1].text


async def test_generation_exception_error_appends_json_mirror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """异常失败结果 content 为错误摘要与 JSON 回传块，后者与 structuredContent 等值。"""

    async def failing_method(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        raise RuntimeError("upstream boom")

    monkeypatch.setattr(SeedreamClient, "text_to_image", failing_method)
    config = SeedreamConfig(api_key="test_key", data_root=str(tmp_path))

    result = await run_text_to_image(TextToImageInput(prompt="a cat"), config)

    assert result.is_error is True
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert "文生图生成失败" in blocks[0].text
    assert not any(isinstance(content, ImageContent) for content in result.content)
    assert result.structured_content is not None
    assert json.loads(blocks[1].text) == result.structured_content
    assert json.loads(blocks[1].text)["success"] is False


async def test_browse_success_appends_json_mirror(workspace_root: Path) -> None:
    """浏览成功结果 content 为清单文本与 JSON 回传块，后者与 structuredContent 等值。"""
    images_root = workspace_root / ".seedream" / "images"
    images_root.mkdir(parents=True)
    (images_root / "demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert "demo.png" in blocks[0].text
    assert result.structured_content is not None
    mirror = json.loads(blocks[1].text)
    assert mirror == result.structured_content
    assert mirror["count"] == 1


async def test_browse_empty_result_appends_json_mirror(workspace_root: Path) -> None:
    """空结果分支同样回传 JSON 块，与 structuredContent 等值。"""
    result = await handle_browse_images(BrowseImagesInput(directory=".", recursive=False))

    assert result.is_error is False
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert "未找到图片文件" in blocks[0].text
    assert result.structured_content is not None
    assert json.loads(blocks[1].text) == result.structured_content


async def test_browse_error_appends_json_mirror(workspace_root: Path) -> None:
    """浏览失败分支同样回传 JSON 块，与 structuredContent 等值。"""
    result = await handle_browse_images(BrowseImagesInput(directory=".", format_filter=[".svg"]))

    assert result.is_error is True
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert "均不在支持列表" in blocks[0].text
    assert result.structured_content is not None
    mirror = json.loads(blocks[1].text)
    assert mirror == result.structured_content
    assert mirror["error"]["type"] == "validation_error"


async def test_generation_b64_payload_mirror_replaced_by_length_placeholder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """b64_json 大载荷的镜像块只含长度占位，structuredContent 字段保留完整原文。

    占位口径与摘要通道一致（同为「Base64 数据: N 字符」），local_path 等定位字段在
    镜像中保持完整。
    """
    payload = "QUJD" * 50_000

    async def fake_text_to_image_b64(self: Any, **kwargs: Any) -> dict[str, Any]:
        del self, kwargs
        return {
            "success": True,
            "data": [{"b64_json": payload}],
            "usage": {"generated_images": 1},
            "status": "completed",
        }

    async def fake_save_multiple_base64(
        self: Any, images: list[dict[str, Any]], tool_name: str
    ) -> list[Any]:
        del self, images, tool_name
        return [
            io_save.AutoSaveResult(
                success=True,
                original_url="base64",
                local_path="/saved/decoded.png",
                markdown_ref="![Generated Image](decoded.png)",
            )
        ]

    monkeypatch.setattr(SeedreamClient, "text_to_image", fake_text_to_image_b64)
    monkeypatch.setattr(
        io_save.AutoSaveManager, "save_multiple_base64_images", fake_save_multiple_base64
    )
    config = SeedreamConfig(api_key="test_key", data_root=str(tmp_path))
    set_active_config(config)

    result = await run_text_to_image(
        TextToImageInput(prompt="a cat", response_format=ResponseFormat.B64_JSON), config
    )

    assert result.is_error is False
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    placeholder = f"Base64 数据: {len(payload)} 字符"
    # 摘要与镜像两通道的占位口径一致。
    assert placeholder in blocks[0].text
    assert placeholder in blocks[1].text
    # 镜像不携带 base64 原文，定位字段保持完整。
    assert payload not in blocks[1].text
    assert "/saved/decoded.png" in blocks[1].text
    mirror = json.loads(blocks[1].text)
    assert mirror["data"][0]["b64_json"] == placeholder
    assert mirror["data"][0]["local_path"] == "/saved/decoded.png"
    # structuredContent 字段本身不受占位替换影响，保留完整载荷。
    structured = result.structured_content
    assert structured is not None
    assert structured["data"][0]["b64_json"] == payload
    assert structured["data"][0]["local_path"] == "/saved/decoded.png"


def test_structured_json_text_replaces_b64_at_any_depth_and_size() -> None:
    """小载荷与嵌套形态的 b64_json 同样替换为长度占位，url/local_path 保持完整，原树不变。"""
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": True,
        "data": [
            {
                "url": "https://example.com/x.png",
                "b64_json": "SMALLB64",
                "local_path": "D:/tmp/x.png",
                "extra": {"b64_json": "NESTED"},
                "b64_json_none": None,
            }
        ],
    }

    mirror_text = build_structured_json_text(structured).text

    mirror = json.loads(mirror_text)
    entry = mirror["data"][0]
    # 不限阈值：小载荷同样替换；嵌套形态不放大上下文。
    assert entry["b64_json"] == "Base64 数据: 8 字符"
    assert entry["extra"]["b64_json"] == "Base64 数据: 6 字符"
    assert "SMALLB64" not in mirror_text
    # 定位字段与非字符串取值不受占位替换影响。
    assert entry["url"] == "https://example.com/x.png"
    assert entry["local_path"] == "D:/tmp/x.png"
    assert entry["b64_json_none"] is None
    # 占位替换只作用于镜像视图，原树不被修改。
    assert structured["data"][0]["b64_json"] == "SMALLB64"
    assert structured["data"][0]["extra"]["b64_json"] == "NESTED"


def test_structured_json_text_sanitizes_sensitive_echo_values() -> None:
    """含凭据样式的回显值在镜像中被掩码，structuredContent 原树不受影响。

    browse 的 directory/format_filter 为原始回显字段，未经出口净化即进入
    structuredContent；敏感键值吸收到词边界整体掩码，长串不借镜像进入模型可见
    通道。
    """
    directory = "//server/share/api_key=secret" + "a" * 900
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": False,
        "directory": directory,
        "format_filter": ["api_key=secret"],
    }

    mirror_text = build_structured_json_text(structured).text

    assert "secret" not in mirror_text
    mirror = json.loads(mirror_text)
    assert mirror["directory"] == "//server/share/api_key=***"
    assert mirror["format_filter"] == ["api_key=***"]
    # 镜像净化不回写原树，structuredContent 字段保留原始回显。
    assert structured["directory"] == directory
    assert structured["format_filter"] == ["api_key=secret"]


def test_structured_json_text_truncates_overlong_values() -> None:
    """超过数据通道防御上限的字符串值在镜像中截断并标注原长度，结构保持不变。"""
    overlong = "x" * 20_000
    structured: dict[str, Any] = {"tool": "browse_images", "success": True, "prompt": overlong}

    mirror = json.loads(build_structured_json_text(structured).text)

    assert len(mirror["prompt"]) <= 16_384
    assert mirror["prompt"].startswith("<truncated:20000 chars> ")
    # 超长原文不整体进入镜像。
    assert overlong not in mirror["prompt"]
    assert structured["prompt"] == overlong


def test_mirror_view_rebuilds_clean_tree_to_equal_json() -> None:
    """无占位且净化无变化的树重建后 JSON 与原树等值，键序逐层一致。"""
    clean: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "status": "completed",
        "directory": ".",
        "nested": {"url": "https://example.com/a.png", "size": "2K"},
        "images": [{"path": "D:/tmp/a.png", "index": 1}],
        "count": 1,
        "ok": True,
        "none": None,
    }

    mirror = _mirror_node(None, clean)

    assert json.dumps(mirror, ensure_ascii=False) == json.dumps(clean, ensure_ascii=False)
    assert list(mirror) == list(clean)
    assert list(mirror["nested"]) == list(clean["nested"])
    assert list(mirror["images"][0]) == list(clean["images"][0])


def test_mirror_view_preserves_key_order_and_does_not_mutate_source() -> None:
    """有占位或净化变化时全树按键序重建，未变化项取值保持，原树不被修改。"""
    structured: dict[str, Any] = {
        "z_field": "ok",
        "data": [
            {"url": "https://example.com/x.png", "b64_json": "SMALLB64"},
            {"local_path": "D:/tmp/y.png", "note": "api_key=secret"},
            {"path": "D:/tmp/z.png", "index": 3},
        ],
    }
    expected: dict[str, Any] = {
        "z_field": "ok",
        "data": [
            {"url": "https://example.com/x.png", "b64_json": "Base64 数据: 8 字符"},
            {"local_path": "D:/tmp/y.png", "note": "api_key=***"},
            {"path": "D:/tmp/z.png", "index": 3},
        ],
    }

    mirror = _mirror_node(None, structured)

    assert json.dumps(mirror, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)
    assert list(mirror) == ["z_field", "data"]
    assert list(mirror["data"][0]) == ["url", "b64_json"]
    assert mirror["data"][2] == structured["data"][2]
    # 原树不被修改。
    assert structured["data"][0]["b64_json"] == "SMALLB64"
    assert structured["data"][1]["note"] == "api_key=secret"


def test_structured_json_text_strict_serialization_rejects_non_finite_floats() -> None:
    """镜像序列化为严格 JSON：非有限浮点直接抛 ValueError，不产出裸 NaN 字面量。"""
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": True,
        "usage": {"output_tokens": float("nan")},
    }

    with pytest.raises(ValueError):
        build_structured_json_text(structured)

    structured["usage"] = {"total": float("inf")}
    with pytest.raises(ValueError):
        build_structured_json_text(structured)


@pytest.mark.parametrize("is_error", [False, True])
async def test_structured_tool_result_degrades_on_non_finite_floats(is_error: bool) -> None:
    """usage 含 NaN/Infinity 时镜像走降级：无镜像块、is_error 不翻转、记录告警。

    structuredContent 字段不受镜像降级影响，非有限取值原样保留。
    """
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": not is_error,
        "usage": {
            "output_tokens": float("nan"),
            "total_tokens": float("inf"),
            "generated_images": 1,
        },
    }
    warnings: list[str] = []

    with capture_loguru_messages(warnings):
        result = await build_structured_tool_result("摘要文本", structured, is_error=is_error)

    assert result.is_error is is_error
    assert result.content == [TextContent(type="text", text="摘要文本")]
    assert any("结构化镜像构建失败" in message for message in warnings)
    mirror_usage = result.structured_content["usage"]
    assert math.isnan(mirror_usage["output_tokens"])
    assert math.isinf(mirror_usage["total_tokens"])
    assert mirror_usage["generated_images"] == 1


async def test_structured_tool_result_builds_mirror_in_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """载荷尺寸估算超过卸载阈值时镜像构建在专用 CPU 线程池执行，事件循环不承载该工作量。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "directory": ".",
        "images": [
            {"index": i, "path": "D:/tmp/" + "x" * 300 + f"/img_{i:04d}.png"} for i in range(200)
        ],
    }

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    assert result.structured_content == structured
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert json.loads(blocks[1].text) == structured
    spy.assert_ran_in_cpu_pool()


async def test_structured_tool_result_builds_small_mirror_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """小载荷镜像内联构建：估算低于卸载阈值时不占专用池线程，免线程往返与池槽位。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": False,
        "error": {"type": "validation_error", "message": "尺寸参数超出允许范围"},
    }

    result = await build_structured_tool_result("摘要文本", structured, is_error=True)

    assert result.is_error is True
    assert result.structured_content == structured
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    assert json.loads(blocks[1].text) == structured
    spy.assert_ran_outside_cpu_pool()


def _payload_estimated_at(total: int) -> dict[str, Any]:
    """构造长度估算恰等于 total 的单键载荷：键与字符串值各按长度加元素开销计入。"""
    key = "payload"
    return {key: "x" * (total - len(key) - 12)}


async def test_structured_tool_result_at_offload_threshold_routes_to_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """估算恰达卸载阈值的载荷下沉专用池构建，阈值边界不落在内联一侧。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured = _payload_estimated_at(CPU_OFFLOAD_SIZE_THRESHOLD)
    assert (
        estimate_output_length(structured, CPU_OFFLOAD_SIZE_THRESHOLD) == CPU_OFFLOAD_SIZE_THRESHOLD
    )

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_in_cpu_pool()


async def test_structured_tool_result_just_below_offload_threshold_builds_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """估算低于卸载阈值一格的载荷内联构建，边界内侧不走线程往返。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured = _payload_estimated_at(CPU_OFFLOAD_SIZE_THRESHOLD - 1)
    assert (
        estimate_output_length(structured, CPU_OFFLOAD_SIZE_THRESHOLD)
        == CPU_OFFLOAD_SIZE_THRESHOLD - 1
    )

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_outside_cpu_pool()


async def test_structured_tool_result_b64_payload_estimates_placeholder_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """200KB 级 b64_json 载荷按占位长度估算，低于阈值内联构建不占专用池线程。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": True,
        "data": [{"url": "https://example.com/1.png", "b64_json": "QUJD" * 60_000}],
    }

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    assert result.structured_content == structured
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_outside_cpu_pool()


async def test_structured_tool_result_same_size_plain_string_still_offloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同尺寸普通字符串按全长估算，超过阈值仍下沉专用池构建。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": True,
        "data": [{"url": "https://example.com/1.png", "payload": "Q" * 240_000}],
    }

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    assert result.structured_content == structured
    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_in_cpu_pool()


def _b64_mixed_payload_estimated_at(total: int) -> dict[str, Any]:
    """构造镜像估算恰等于 total 的混合载荷：b64_json 按占位长度、补齐串按全长计入。"""
    placeholder = len(format_base64_placeholder(len("QUJD")))
    # 两个键与两个字符串值各计 6 的元素标点开销。
    fixed = len("b64_json") + len("pad") + placeholder + 4 * 6
    return {"b64_json": "QUJD", "pad": "x" * (total - fixed)}


async def test_structured_tool_result_b64_mixed_at_offload_threshold_routes_to_cpu_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """含 b64 占位的混合载荷估算恰达卸载阈值仍下沉，阈值边界不因占位口径左移。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured = _b64_mixed_payload_estimated_at(CPU_OFFLOAD_SIZE_THRESHOLD)

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_in_cpu_pool()


async def test_structured_tool_result_b64_mixed_just_below_threshold_builds_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """含 b64 占位的混合载荷估算低于阈值一格内联构建，边界内侧不走线程往返。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured = _b64_mixed_payload_estimated_at(CPU_OFFLOAD_SIZE_THRESHOLD - 1)

    result = await build_structured_tool_result("摘要文本", structured, is_error=False)

    blocks = _text_blocks(result)
    assert len(blocks) == 2
    spy.assert_ran_outside_cpu_pool()


def test_mirror_offload_estimator_delegates_to_shared_estimate() -> None:
    """守护：镜像卸载估算复用通用估算器加占位钩子，遍历骨架不在本模块复制。"""
    import inspect

    source = inspect.getsource(outputs_module)
    assert "_estimate_mirror_output_length" not in source
    assert "_CONTAINER_" not in source


async def test_structured_tool_result_pool_path_degrades_on_non_finite_floats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """池路径的镜像失败降级与内联路径一致：无镜像块、is_error 不翻转、记录告警。"""
    spy = CpuOffloadSpy(build_structured_json_text)
    monkeypatch.setattr(outputs_module, "build_structured_json_text", spy)
    structured: dict[str, Any] = {
        "tool": "text_to_image",
        "success": False,
        "usage": {"total_tokens": float("nan")},
        "notes": ["x" * 15_000] * 5,
    }
    warnings: list[str] = []

    with capture_loguru_messages(warnings):
        result = await build_structured_tool_result("摘要文本", structured, is_error=True)

    spy.assert_ran_in_cpu_pool()
    assert result.is_error is True
    assert result.content == [TextContent(type="text", text="摘要文本")]
    assert any("结构化镜像构建失败" in message for message in warnings)
    assert math.isnan(result.structured_content["usage"]["total_tokens"])


def test_mirror_keeps_sanitizing_invisible_chars_without_trigger_words() -> None:
    """不含敏感触发词的字符串仍净化：控制字符压平、零宽字符移除不被恒等快路绕过。"""
    zwsp = chr(0x200B)
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "note": "bad\x00name",
        "extra": f"zero{zwsp}width",
    }

    mirror = json.loads(build_structured_json_text(structured).text)

    assert mirror["note"] == "bad name"
    assert mirror["extra"] == "zero" + "width"
    assert structured["note"] == "bad\x00name"


def test_mirror_strips_edge_whitespace_and_userinfo_on_url_forms() -> None:
    """URL 形态回退完整净化：首尾空白与 userinfo 剥离的既有口径不被恒等快路绕过。"""
    structured: dict[str, Any] = {
        "tool": "browse_images",
        "success": True,
        "padded": " https://example.com/x.png ",
        "credentialed": "https://user:pass@example.com/x.png",
    }

    mirror = json.loads(build_structured_json_text(structured).text)

    assert mirror["padded"] == "https://example.com/x.png"
    assert mirror["credentialed"] == "https://example.com/x.png"
    assert structured["padded"] == " https://example.com/x.png "
