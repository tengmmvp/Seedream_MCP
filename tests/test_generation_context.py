"""生成执行上下文构建、并行结果聚合与响应格式化测试。"""

from dataclasses import fields
from pathlib import Path

import pytest
from pydantic import ValidationError

from seedream_mcp.config import SeedreamConfig
from seedream_mcp.tools.core._helpers import prevalidate_save_path
from seedream_mcp.tools.core.common import (
    GenerationExecutionContext,
    aggregate_parallel_generation_results,
    build_generation_context,
    format_generation_response,
    update_result_with_auto_save,
)
from seedream_mcp.tools.core.schemas import ImageToImageInput, TextToImageInput
from seedream_mcp.utils.io.io_save import AutoSaveResult
from seedream_mcp.utils.core.errors import SeedreamValidationError


def _build_config() -> SeedreamConfig:
    return SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-0-250828",
        default_size="2K",
    )


def test_build_generation_context_uses_default_size_when_omitted() -> None:
    config = _build_config()
    context = build_generation_context(TextToImageInput(prompt="test"), config)

    assert context.size == "2K"
    assert context.request_count == 1
    assert context.parallelism == 1


def _build_pro_config() -> SeedreamConfig:
    return SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-pro-260628",
        default_size="2K",
    )


def test_build_generation_context_layer_decomposition_defaults_size_auto() -> None:
    """图层拆分开启且未显式提供 size 时按官方默认取 auto，不取 config.default_size。"""
    config = _build_pro_config()
    context = build_generation_context(
        ImageToImageInput(
            prompt="拆分图层", image="https://example.com/a.png", layer_decomposition=True
        ),
        config,
    )

    assert context.layer_decomposition is True
    assert context.size == "auto"


def test_build_generation_context_layer_decomposition_keeps_explicit_size() -> None:
    config = _build_pro_config()
    context = build_generation_context(
        ImageToImageInput(
            prompt="拆分图层",
            image="https://example.com/a.png",
            layer_decomposition=True,
            size="1K",
        ),
        config,
    )

    assert context.size == "1K"


def test_build_generation_context_layer_decomposition_rejected_for_lite() -> None:
    config = SeedreamConfig(api_key="test_key", model_id="doubao-seedream-5-0-260128")

    with pytest.raises(SeedreamValidationError, match="不支持 layer_decomposition"):
        build_generation_context(
            ImageToImageInput(
                prompt="拆分图层", image="https://example.com/a.png", layer_decomposition=True
            ),
            config,
        )


def test_build_generation_context_background_carried_to_context() -> None:
    config = _build_pro_config()
    context = build_generation_context(
        ImageToImageInput(
            prompt="透明背景", image="https://example.com/a.png", background="transparent"
        ),
        config,
    )

    assert context.background == "transparent"
    assert context.layer_decomposition is False


def test_build_generation_context_layer_scenario_allows_missing_prompt() -> None:
    """图层拆分场景 prompt 可缺省，context 携带 None 且 size 默认 auto。"""
    config = _build_pro_config()
    context = build_generation_context(
        ImageToImageInput(image="https://example.com/a.png", layer_decomposition=True),
        config,
    )

    assert context.prompt is None
    assert context.size == "auto"


def test_image_to_image_input_rejects_missing_prompt_without_layer() -> None:
    with pytest.raises(ValidationError, match="prompt 不能为空"):
        ImageToImageInput(image="https://example.com/a.png")


def test_build_generation_context_rejects_transparent_with_jpeg() -> None:
    config = _build_pro_config()

    with pytest.raises(SeedreamValidationError, match="互斥"):
        build_generation_context(
            ImageToImageInput(
                prompt="透明背景",
                image="https://example.com/a.png",
                background="transparent",
                output_format="jpeg",
            ),
            config,
        )


def test_generation_execution_context_field_order_matches_mcp_order() -> None:
    assert [field.name for field in fields(GenerationExecutionContext)] == [
        "prompt",
        "optimize_prompt_options",
        "size",
        "watermark",
        "response_format",
        "output_format",
        "stream",
        "tools",
        "layer_decomposition",
        "background",
        "max_images",
        "request_count",
        "parallelism",
        "enable_auto_save",
        "save_path",
        "custom_name",
    ]


def test_build_generation_context_rejects_explicit_empty_size() -> None:
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="图像尺寸不能为空"):
        build_generation_context(TextToImageInput(prompt="test", size=""), config)


def test_build_generation_context_rejects_reference_images_over_pro_limit() -> None:
    """5.0 Pro 的参考图上限 10 在 context 层即时拒绝，与尺寸等能力校验同层。

    schema 上限只能表达全家族默认 14；若留给 client 层，进度已上报"参数校验完成"
    后才报错，错误呈现层次不一致。
    """
    from seedream_mcp.tools.core.schemas import MultiImageFusionInput

    config = SeedreamConfig(api_key="test_key", model_id="doubao-seedream-5-0-pro")
    params = MultiImageFusionInput(
        prompt="融合测试",
        image=[f"https://example.com/ref-{i}.png" for i in range(11)],
    )

    with pytest.raises(SeedreamValidationError, match="数量不能超过 10"):
        build_generation_context(params, config)


def test_build_generation_context_allows_default_model_reference_limit() -> None:
    """非 Pro 家族的默认上限 14 在 context 层放行，不误伤合法输入。"""
    from seedream_mcp.tools.core.schemas import MultiImageFusionInput

    config = SeedreamConfig(api_key="test_key", model_id="doubao-seedream-5-0")
    params = MultiImageFusionInput(
        prompt="融合测试",
        image=[f"https://example.com/ref-{i}.png" for i in range(14)],
    )

    context = build_generation_context(params, config)

    assert context.request_count == 1


def test_build_generation_context_sets_default_parallelism_by_request_count() -> None:
    config = _build_config()
    context = build_generation_context(TextToImageInput(prompt="test", request_count=3), config)

    assert context.request_count == 3
    assert context.parallelism == 3


def test_build_generation_context_uses_explicit_parallelism() -> None:
    config = _build_config()
    context = build_generation_context(
        TextToImageInput(prompt="test", request_count=4, parallelism=2),
        config,
    )
    assert context.request_count == 4
    assert context.parallelism == 2


def test_input_schema_rejects_zero_parallelism() -> None:
    """parallelism 越界属 schema Field 约束，构造输入模型时即被拒绝。"""
    with pytest.raises(ValidationError, match="parallelism"):
        TextToImageInput(prompt="test", request_count=2, parallelism=0)


def test_build_generation_context_accepts_seedream_50_output_format_and_tools() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-260128",
        default_size="2K",
    )

    context = build_generation_context(
        TextToImageInput(
            prompt="test",
            output_format="png",
            tools=[{"type": "web_search"}],
        ),
        config,
    )

    assert context.output_format == "png"
    assert context.tools == [{"type": "web_search"}]


def test_build_generation_context_rejects_output_format_for_seedream_45() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-4-5-251128",
        default_size="2K",
    )

    with pytest.raises(SeedreamValidationError, match="仅 doubao-seedream-5.0 系列"):
        build_generation_context(TextToImageInput(prompt="test", output_format="png"), config)


def test_build_generation_context_rejects_stream_for_seedream_50_pro() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-pro-260628",
        default_size="2K",
    )

    with pytest.raises(SeedreamValidationError, match="5.0-pro 不支持流式输出"):
        build_generation_context(TextToImageInput(prompt="test", stream=True), config)


def test_build_generation_context_rejects_fast_optimize_mode_for_seedream_50() -> None:
    config = SeedreamConfig(
        api_key="test_key",
        model_id="doubao-seedream-5-0-260128",
        default_size="2K",
    )

    with pytest.raises(
        SeedreamValidationError, match="仅支持 optimize_prompt_options.mode=standard"
    ):
        build_generation_context(
            TextToImageInput(prompt="test", optimize_prompt_options={"mode": "fast"}),
            config,
        )


def test_update_result_with_auto_save_aligns_with_saveable_images_only() -> None:
    result = {
        "success": True,
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
    }
    auto_save_results = [
        AutoSaveResult(
            success=True,
            original_url="https://example.com/ok.png",
            local_path="images/ok.png",
            markdown_ref="![ok](images/ok.png)",
        )
    ]

    updated = update_result_with_auto_save(result, auto_save_results, [1])

    failed_item = updated["data"][0]
    success_item = updated["data"][1]

    assert "local_path" not in failed_item
    assert "markdown_ref" not in failed_item
    assert success_item["local_path"] == "images/ok.png"
    assert success_item["markdown_ref"] == "![ok](images/ok.png)"


def test_aggregate_parallel_generation_results_merges_data_usage_and_failures() -> None:
    request_results = [
        {
            "success": True,
            "data": [{"url": "https://example.com/1.png"}],
            "usage": {"generated_images": 1, "total_tokens": 10},
            "status": "completed",
        },
        None,
        {
            "success": True,
            "data": [{"url": "https://example.com/3.png"}],
            "usage": {"generated_images": 1, "total_tokens": 8},
            "status": "completed",
        },
    ]
    request_errors = {2: RuntimeError("请求超时")}

    result = aggregate_parallel_generation_results(
        request_results=request_results,
        request_errors=request_errors,
    )

    assert result["success"] is True
    assert result["status"] == "partial"
    assert result["batch"]["request_count"] == 3
    assert result["batch"]["success_requests"] == 2
    assert result["batch"]["failed_requests"] == 1
    assert result["usage"]["generated_images"] == 2
    assert result["usage"]["total_tokens"] == 18
    assert result["data"][0]["request_index"] == 1
    assert result["data"][1]["request_index"] == 2
    assert "请求超时" in result["data"][1]["error"]["message"]
    assert result["data"][2]["request_index"] == 3


def test_aggregate_parallel_generation_results_all_failed_keeps_error_details() -> None:
    result = aggregate_parallel_generation_results(
        request_results=[None, None],
        request_errors={1: RuntimeError("认证失败"), 2: RuntimeError("请求频率超限")},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "认证失败" in result["error"]["message"]
    assert result["batch"]["errors"][0]["request_index"] == 1
    assert "认证失败" in result["batch"]["errors"][0]["message"]
    assert result["batch"]["errors"][1]["request_index"] == 2
    assert "请求频率超限" in result["batch"]["errors"][1]["message"]


def test_aggregate_all_failed_result_dicts_carry_upstream_error_code() -> None:
    """全为失败 dict 且无异常时 error 载荷携带上游 code，与单发路径契约一致。

    200 加顶层 error 的请求级失败（如内容策略拒绝）经并行聚合后不再丢失上游错误码；
    提取取首个携带非空字符串 code 的结果，未提取到时维持 generation_failed 兜底。
    """
    result = aggregate_parallel_generation_results(
        request_results=[
            {
                "success": False,
                "status": "failed",
                "data": [],
                "error": {"code": "ContentFilterBlocked", "message": "内容不符合规范"},
            },
            {
                "success": False,
                "status": "failed",
                "data": [],
                "error": {"code": "ContentFilterBlocked", "message": "内容不符合规范"},
            },
        ],
        request_errors={},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ContentFilterBlocked"
    assert "内容不符合规范" in result["error"]["message"]


def test_aggregate_all_failed_result_dicts_without_code_keep_fallback() -> None:
    """失败 dict 无可用 code 时 error 载荷不含 code 键，type 维持兜底档。"""
    result = aggregate_parallel_generation_results(
        request_results=[
            {"success": False, "status": "failed", "data": [], "error": {"message": "boom"}},
        ],
        request_errors={},
    )

    assert result["error"]["type"] == "generation_failed"
    assert "code" not in result["error"]


def test_structured_outlet_carries_upstream_code_for_parallel_all_failed() -> None:
    """并发全失败的内容策略拒绝经结构化出口透传上游 code，且净化语义保持。

    聚合 error 携带的 code 为上游自由文本，_build_generation_structured_result 的
    dict error 分支净化后透传；携带 CRLF 与凭据样式片段的 code 不借该通道注入。
    """
    from seedream_mcp.tools.core.results import _build_generation_structured_result

    aggregated = aggregate_parallel_generation_results(
        request_results=[
            {
                "success": False,
                "status": "failed",
                "data": [],
                "error": {
                    "code": "E-1\r\nFAKE api_key=leaked",
                    "message": "并行请求全部失败。请求1: 内容不符合规范",
                },
            },
        ],
        request_errors={},
    )

    structured = _build_generation_structured_result(
        tool_name="text_to_image",
        result=aggregated,
        context=GenerationExecutionContext(
            prompt="t",
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
            enable_auto_save=False,
            save_path=None,
            custom_name=None,
        ),
        auto_save_results=[],
        auto_save_error=None,
    )

    assert structured["error"]["code"] == "E-1  FAKE api_key=***"
    assert "leaked" not in str(structured["error"])


def test_aggregate_parallel_generation_results_uses_result_error_when_success_false() -> None:
    result = aggregate_parallel_generation_results(
        request_results=[
            {"success": False, "error": "鉴权失败"},
            {"success": False, "error": {"message": "请求频率超限"}},
        ],
        request_errors={},
    )

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "鉴权失败" in result["error"]["message"]
    assert "请求频率超限" in result["error"]["message"]
    assert result["data"][0]["error"]["message"] == "鉴权失败"
    assert result["data"][1]["error"]["message"] == "请求频率超限"
    assert result["batch"]["errors"][0]["message"] == "鉴权失败"
    assert "请求频率超限" in result["batch"]["errors"][1]["message"]


def test_format_generation_response_reports_parallel_failure_details() -> None:
    text = format_generation_response(
        title="文生图任务完成",
        result={
            "success": False,
            "error": "并行请求全部失败。请求1: 认证失败",
            "batch": {
                "request_count": 2,
                "success_requests": 0,
                "failed_requests": 2,
                "errors": [
                    {"request_index": 1, "message": "认证失败"},
                    {"request_index": 2, "message": "请求频率超限"},
                ],
            },
        },
        prompt="test",
        size="2K",
    )

    assert "图片生成失败:" in text
    assert "并行失败详情:" in text
    assert "请求 1: 认证失败" in text
    assert "请求 2: 请求频率超限" in text


def test_format_failure_section_extracts_message_from_dict_error() -> None:
    """result['error'] 为 dict 形态时，用户可见文本应取其 message，不应输出字典 repr。"""
    text = format_generation_response(
        title="文生图任务完成",
        result={
            "success": False,
            "status": "failed",
            "error": {"type": "auth_error", "message": "并行请求全部失败。请求1: 认证失败"},
            "batch": {
                "request_count": 1,
                "success_requests": 0,
                "failed_requests": 1,
                "errors": [{"request_index": 1, "message": "认证失败"}],
            },
        },
        prompt="test",
        size="2K",
    )
    failure_line = text.splitlines()[0]
    assert "并行请求全部失败。请求1: 认证失败" in failure_line
    # 失败首行不应出现字典 repr 的花括号
    assert "{" not in failure_line
    assert "}" not in failure_line


def test_format_generation_response_shows_input_images_for_pro_usage() -> None:
    text = format_generation_response(
        title="图文生图任务完成",
        result={
            "success": True,
            "data": [{"url": "https://example.com/1.png", "size": "1024x1024"}],
            "usage": {"input_images": 1, "generated_images": 1, "output_tokens": 100},
        },
        prompt="test",
        size="1024x1024",
    )

    # 5.0 Pro 返回 usage.input_images 表示输入图数，应在文本统计中展示。
    # 生成图片数与自动保存摘要重复，文本通道收敛后由图片列表与结构化通道表达。
    assert "输入图片数: 1" in text
    assert "输出 tokens: 100" in text
    assert "生成图片数" not in text


def test_build_generation_context_auto_save_none_equals_omitted() -> None:
    """auto_save=None 与不传该参行为一致，均回落到 config.auto_save_enabled。"""
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=True)

    ctx_explicit_none = build_generation_context(
        TextToImageInput(prompt="t", auto_save=None), config
    )
    ctx_omitted = build_generation_context(TextToImageInput(prompt="t"), config)

    assert ctx_explicit_none.enable_auto_save is True
    assert ctx_omitted.enable_auto_save is True
    assert ctx_explicit_none.enable_auto_save == ctx_omitted.enable_auto_save


def test_build_generation_context_auto_save_none_passes_through_disabled_config() -> None:
    """config.auto_save_enabled=False 时，auto_save=None 穿透结果为 False。"""
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=False)

    ctx = build_generation_context(TextToImageInput(prompt="t", auto_save=None), config)

    assert ctx.enable_auto_save is False


def test_build_generation_context_explicit_auto_save_overrides_config() -> None:
    """显式 auto_save 非 None 时覆盖 config.auto_save_enabled。

    确保穿透仅在 None 时发生。
    """
    config = SeedreamConfig(api_key="test_key", auto_save_enabled=True)

    ctx = build_generation_context(TextToImageInput(prompt="t", auto_save=False), config)

    assert ctx.enable_auto_save is False


def test_input_schema_rejects_non_bool_auto_save() -> None:
    """auto_save 类型约束属 schema 字段声明，不可解析的值在构造输入模型时即被拒绝。

    可解析的布尔字符串如 yes/true 经 pydantic 宽松模式归一化为 bool，与 MCP 客户端
    传 JSON 布尔的路径行为一致。
    """
    with pytest.raises(ValidationError):
        TextToImageInput(prompt="t", auto_save="maybe")


# ==================== save_path 生成前预检 ====================


def test_build_generation_context_rejects_out_of_bounds_save_path(
    tmp_path: Path,
) -> None:
    """越界 save_path 在预检阶段即拒绝，早于计费的生成请求分发。

    此前越界路径在自动保存阶段才抛校验异常并被降级为软警告，图片已按默认目录之外
    的目标无法落盘；预检与 _resolve_base_dir 共用同一判定入口，错误档位为
    validation_error。
    """
    base = tmp_path / "save_root"
    base.mkdir()
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(base))

    with pytest.raises(SeedreamValidationError, match="超出允许范围") as exc_info:
        prevalidate_save_path(config, "../../outside")

    assert exc_info.value.field == "save_path"


def test_prevalidate_save_path_rejects_invalid_format() -> None:
    """无法规范化的 save_path 同属校验档，在预检阶段即拒绝。"""
    config = _build_config()

    with pytest.raises(SeedreamValidationError, match="保存路径无效"):
        prevalidate_save_path(config, "a" + "\x00" + "b")


def test_prevalidate_save_path_accepts_in_bounds_path(tmp_path: Path) -> None:
    """界内 save_path 预检放行，上下文照常携带原始值交由保存阶段完整解析。"""
    base = tmp_path / "save_root"
    base.mkdir()
    config = SeedreamConfig(api_key="test_key", auto_save_base_dir=str(base))

    prevalidate_save_path(config, "sub/dir")
    context = build_generation_context(TextToImageInput(prompt="test", save_path="sub/dir"), config)

    assert context.save_path == "sub/dir"


def test_prevalidate_save_path_skips_without_save_path() -> None:
    """未提供 save_path 时不做预检：默认目录解析失败留待自动保存阶段处理。"""
    from seedream_mcp.utils.io import io_path as io_path_module

    config = SeedreamConfig(api_key="test_key")
    token = io_path_module._WORKSPACE_ROOTS_VAR.set(())
    try:
        # 空 Roots 且未配置 auto_save_base_dir 时预检直接跳过，不在此抛出
        # 无法确定自动保存基础目录的校验异常。
        prevalidate_save_path(config, None)
        context = build_generation_context(TextToImageInput(prompt="test"), config)
    finally:
        io_path_module._WORKSPACE_ROOTS_VAR.reset(token)

    assert context.save_path is None
