"""Web 操作台生成 API 测试：四端点复用 runners、错误映射与 web_path 增强。

runner 经对象式 monkeypatch 替换，覆盖成功、校验失败与错误类型映射到 HTTP
状态码的分支；web_path 增强的防劣化分支独立单测；共享资源 shim 的构造行为
与端点级传递（含并发共享）分别独立覆盖。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp.types import CallToolResult

import seedream_mcp.resources as resources_module
from _web_fixtures import build_web_app, write_workspace_config
from seedream_mcp.webapp import _shared as _shared_module
from seedream_mcp.config import LIFESPAN_KEY_CLIENT, LIFESPAN_KEY_DOWNLOAD_MANAGER
from seedream_mcp.utils.core.errors import SeedreamValidationError
from seedream_mcp.webapp import generate as generate_module
from seedream_mcp.webapp.context import build_web_request_context


def _generation_result(payload: dict[str, Any], is_error: bool = False) -> CallToolResult:
    """构造生成 runner 的替身返回值。"""
    return CallToolResult(content=[], structured_content=payload, is_error=is_error)


def _make_fake_runner(
    *,
    result: CallToolResult | None = None,
    error: Exception | None = None,
    captured: list[dict[str, Any]] | None = None,
    yield_once: bool = False,
) -> Callable[..., Awaitable[CallToolResult]]:
    """构造生成 runner 替身：返回固定结果或抛指定异常，可选记录调用实参。

    captured 每次调用追加 ctx/workspace_roots/include_previews 三键字典；
    yield_once 在返回前让出事件循环一次，模拟并发共享的挂起点。
    """

    async def _runner(
        params: Any,
        config: Any,
        ctx: Any = None,
        workspace_roots: Any = None,
        include_previews: bool = True,
    ) -> CallToolResult:
        del params, config
        if captured is not None:
            captured.append(
                {
                    "ctx": ctx,
                    "workspace_roots": workspace_roots,
                    "include_previews": include_previews,
                }
            )
        if yield_once:
            await asyncio.sleep(0)
        if error is not None:
            raise error
        assert result is not None
        return result

    return _runner


def _install_runner(
    monkeypatch: pytest.MonkeyPatch, name: str, payload: dict[str, Any], is_error: bool = False
) -> None:
    """把 api 命名空间内的 runner 符号替换为返回固定结果的替身。"""
    monkeypatch.setattr(
        generate_module, name, _make_fake_runner(result=_generation_result(payload, is_error))
    )


async def _post_json(app: Any, path: str, body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post(path, json=body)


async def test_generate_returns_structured_payload_with_web_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """成功结果的 data 条目附 web_path 且 local_path 改写为同一相对形态。"""
    save_root = write_workspace_config(tmp_path)
    local_path = save_root / "2026-08-20" / "text_to_image" / "a.png"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"png")
    _install_runner(
        monkeypatch,
        "run_text_to_image",
        {
            "tool": "text_to_image",
            "success": True,
            "data": [{"url": "https://x/a.png", "local_path": str(local_path)}],
        },
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    entry = response.json()["data"][0]
    assert entry["web_path"] == "2026-08-20/text_to_image/a.png"
    assert entry["local_path"] == "2026-08-20/text_to_image/a.png"


async def test_generate_skips_web_path_outside_save_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """保存根之外的条目删除 local_path 键且不附 web_path，绝对路径不出端点。"""
    write_workspace_config(tmp_path)
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"png")
    _install_runner(
        monkeypatch,
        "run_image_to_image",
        {"tool": "image_to_image", "success": True, "data": [{"local_path": str(outside)}]},
    )
    app = build_web_app()

    response = await _post_json(
        app, "/web/api/generate/image-to-image", {"prompt": "改图", "image": "https://x/a.png"}
    )

    assert response.status_code == 200
    entry = response.json()["data"][0]
    assert "web_path" not in entry
    assert "local_path" not in entry


async def test_generate_multi_image_fusion_returns_structured_payload_with_web_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """融合端点复用 run_multi_image_fusion，保存根内条目附 web_path。"""
    save_root = write_workspace_config(tmp_path)
    local_path = save_root / "2026-08-20" / "multi_image_fusion" / "a.png"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"png")
    _install_runner(
        monkeypatch,
        "run_multi_image_fusion",
        {
            "tool": "multi_image_fusion",
            "success": True,
            "data": [{"url": "https://x/a.png", "local_path": str(local_path)}],
        },
    )
    app = build_web_app()

    response = await _post_json(
        app,
        "/web/api/generate/multi-image-fusion",
        {"prompt": "融合穿搭", "image": ["https://x/a.png", "https://x/b.png"]},
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["web_path"] == "2026-08-20/multi_image_fusion/a.png"


async def test_generate_multi_image_fusion_missing_images_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """融合请求缺 image 列表经 MultiImageFusionInput 校验拒绝，映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/multi-image-fusion", {"prompt": "融合穿搭"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_generate_sequential_generation_returns_structured_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """组图端点复用 run_sequential_generation，结构化结果原样返回。"""
    write_workspace_config(tmp_path)
    _install_runner(
        monkeypatch,
        "run_sequential_generation",
        {
            "tool": "sequential_generation",
            "success": True,
            "data": [{"url": "https://x/1.png"}, {"url": "https://x/2.png"}],
        },
    )
    app = build_web_app()

    response = await _post_json(
        app, "/web/api/generate/sequential-generation", {"prompt": "四格漫画", "max_images": 4}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool"] == "sequential_generation"
    assert [item["url"] for item in payload["data"]] == ["https://x/1.png", "https://x/2.png"]


async def test_generate_sequential_generation_empty_prompt_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """组图空提示词经 SequentialGenerationInput 校验拒绝，映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/sequential-generation", {"prompt": ""})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_generate_invalid_params_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """空提示词经 pydantic 校验拒绝，映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": ""})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


async def test_generate_invalid_json_returns_400(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """非法 JSON 请求体映射 400。"""
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post(
            "/web/api/generate/text-to-image",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        ("validation_error", 400),
        ("payload_too_large", 400),
        ("rate_limited", 429),
        ("payment_required", 402),
        ("generation_failed", 502),
    ],
)
async def test_generate_maps_error_type_to_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
    error_type: str,
    expected_status: int,
) -> None:
    """失败结果按 error.type 映射状态码，响应体保留完整结构化结果。"""
    write_workspace_config(tmp_path)
    _install_runner(
        monkeypatch,
        "run_text_to_image",
        {
            "tool": "text_to_image",
            "success": False,
            "data": [],
            "error": {"type": error_type, "message": "x"},
        },
        is_error=True,
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == expected_status
    assert response.json()["error"]["type"] == error_type


async def test_generate_runner_validation_error_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """runner 抛 SeedreamValidationError 映射 400 validation_error，携带原因。"""

    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(error=SeedreamValidationError("参考图数量超出上限", field="image")),
    )
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "validation_error"
    assert "参考图数量超出上限" in payload["error_description"]


async def test_generate_runner_validation_error_masks_save_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """runner 校验错误消息中的保存根绝对路径替换为占位符后返回。"""
    save_root = write_workspace_config(tmp_path)

    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(
            error=SeedreamValidationError(f"保存路径须位于 {save_root} 之内", field="save_path")
        ),
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 400
    description = response.json()["error_description"]
    assert str(save_root) not in description
    assert "<保存根>" in description


async def test_generate_runner_unexpected_error_returns_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """runner 抛未归类异常兜底 500 internal_error，不向响应泄露异常细节。"""

    monkeypatch.setattr(
        generate_module, "run_text_to_image", _make_fake_runner(error=RuntimeError("boom"))
    )
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "internal_error"
    assert "boom" not in payload["error_description"]


async def test_generate_non_dict_structured_content_returns_empty_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """structured_content 非 dict 时回退空对象响应为 200，不抛异常。"""

    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(result=CallToolResult(content=[], structured_content="bad")),
    )
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    assert response.json() == {}


async def test_generate_runner_called_without_previews(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """端点以 include_previews=False 调 runner：Web 路径只消费结构化结果，跳过预览装配。"""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(
            result=_generation_result({"tool": "text_to_image", "success": True, "data": []}),
            captured=captured,
        ),
    )
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    assert [call["include_previews"] for call in captured] == [False]


async def test_generate_runner_receives_no_forged_workspace_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """端点不向 runner 伪造会话 Roots，文件边界走环境变量回退链。

    伪造 Roots 有两重回归：UNC 工作区根经 file URI 转换层丢失使回退边界失效、
    本地路径错误回显分支解锁泄露服务器路径；保存根作边界还会造成保存目录
    双重嵌套。
    """
    write_workspace_config(tmp_path)
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(
            result=_generation_result({"tool": "text_to_image", "success": True, "data": []}),
            captured=captured,
        ),
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    assert [call["workspace_roots"] for call in captured] == [None]


async def test_generate_rejects_when_save_root_unresolvable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """保存根不可解析时与图库端点同口径返回 400 配置指引，不以宽边界降级执行。"""

    def _unresolvable(config: Any) -> Any:
        del config
        raise SeedreamValidationError("无法确定自动保存基础目录", field="auto_save_base_dir")

    monkeypatch.setattr(_shared_module, "resolve_default_base_dir", _unresolvable)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "save_root_unavailable"
    assert "SEEDREAM_AUTO_SAVE_BASE_DIR" in body["error_description"]


async def test_generate_masks_save_root_in_error_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """auto_save.results[].error、data[].error 嵌套 message 与顶层 error.message 中的
    保存根绝对路径统一替换为占位符，响应体不再包含保存根字样。"""
    save_root = write_workspace_config(tmp_path)
    _install_runner(
        monkeypatch,
        "run_text_to_image",
        {
            "tool": "text_to_image",
            "success": False,
            "data": [
                {"url": "https://x/a.png", "error": {"message": f"下载失败于 {save_root}\\a.png"}}
            ],
            "error": {"type": "generation_failed", "message": f"保存到 {save_root} 失败"},
            "auto_save": {
                "results": [{"success": False, "error": f"写入 {save_root}\\b.png 被拒绝"}]
            },
        },
        is_error=True,
    )
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 502
    assert str(save_root) not in response.text
    payload = response.json()
    assert payload["error"]["message"] == "保存到 <保存根> 失败"
    assert payload["data"][0]["error"]["message"] == "下载失败于 <保存根>\\a.png"
    assert payload["auto_save"]["results"][0]["error"] == "写入 <保存根>\\b.png 被拒绝"


async def test_generate_endpoint_requires_token(
    tmp_path: Path,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """令牌部署下生成端点未携带令牌时被 Bearer 中间件拒绝。"""
    write_workspace_config(tmp_path)
    app = build_web_app(auth_token="secret")

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 401


def test_build_web_request_context_returns_stub_when_resource_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享资源在堂时 shim 的 lifespan 字典携带共享 client 与下载管理器。"""

    class _FakeClient:
        pass

    class _FakeDownloadManager:
        pass

    client = _FakeClient()
    download_manager = _FakeDownloadManager()
    monkeypatch.setattr(
        resources_module,
        "_active_resource",
        type("_R", (), {"client": client, "download_manager": download_manager})(),
    )

    stub = build_web_request_context()

    assert stub is not None
    assert stub.request_context.lifespan_context["client"] is client
    assert stub.request_context.lifespan_context["download_manager"] is download_manager


def test_build_web_request_context_returns_none_when_resource_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共享资源不在堂时返回 None，调用方回退每请求新建 client 路径。"""
    monkeypatch.setattr(resources_module, "_active_resource", None)

    assert build_web_request_context() is None


def test_augment_generation_payload_ignores_non_list_data(tmp_path: Path) -> None:
    """data 非列表时整体跳过，原字典不被改写。"""
    structured = {"data": "not-a-list", "success": True}

    generate_module.augment_generation_payload(structured, tmp_path)

    assert structured == {"data": "not-a-list", "success": True}


def test_augment_generation_payload_skips_non_dict_items(tmp_path: Path) -> None:
    """data 条目非 dict 时跳过该条目，列表与其他键保持原样。"""
    structured = {"data": [42, "x", None], "count": 3}

    generate_module.augment_generation_payload(structured, tmp_path)

    assert structured == {"data": [42, "x", None], "count": 3}


def test_augment_generation_payload_skips_non_string_local_path(tmp_path: Path) -> None:
    """local_path 非字符串时跳过该条目，不附 web_path 且既有键不变。"""
    structured = {"data": [{"local_path": 123, "keep": "v"}, {"local_path": None, "keep": 2}]}

    generate_module.augment_generation_payload(structured, tmp_path)

    assert structured["data"] == [{"local_path": 123, "keep": "v"}, {"local_path": None, "keep": 2}]


def test_augment_generation_payload_tolerates_resolve_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """条目路径解析抛 OSError 时删除 local_path 键，其余键与顶层键不被改写。"""

    class _ExplodingPath:
        def __init__(self, _raw: object) -> None:
            pass

        def resolve(self) -> "_ExplodingPath":
            raise OSError("illegal path")

    monkeypatch.setattr(generate_module, "Path", _ExplodingPath)
    structured = {"data": [{"local_path": "x.png", "keep": 1}], "success": True}

    generate_module.augment_generation_payload(structured, tmp_path)

    assert structured == {"data": [{"keep": 1}], "success": True}


def test_sanitize_save_root_text_replaces_nested_string_values(tmp_path: Path) -> None:
    """递归净化覆盖 dict 嵌套 message 与 list 字符串元素，非字符串叶子保持原样。"""
    save_root = tmp_path / ".seedream" / "images"
    structured: dict[str, object] = {
        "error": {"message": f"根为 {save_root}"},
        "data": [{"error": {"message": f"{save_root}\\a.png"}}, {"keep": 42}],
        "urls": [f"{save_root}/b.png", "https://x/c.png"],
    }

    generate_module.sanitize_save_root_text(structured, save_root)

    assert structured["error"] == {"message": "根为 <保存根>"}
    data = structured["data"]
    assert isinstance(data, list)
    assert data[0] == {"error": {"message": "<保存根>\\a.png"}}
    assert data[1] == {"keep": 42}
    assert structured["urls"] == ["<保存根>/b.png", "https://x/c.png"]


def test_sanitize_save_root_text_keeps_non_string_leaves(tmp_path: Path) -> None:
    """int、bool、None 叶子不参与替换，顶层非容器值调用为无操作。"""
    structured: dict[str, object] = {"count": 3, "ok": True, "empty": None}

    generate_module.sanitize_save_root_text(structured, tmp_path)
    generate_module.sanitize_save_root_text(42, tmp_path)

    assert structured == {"count": 3, "ok": True, "empty": None}


def _make_stub_resource() -> SimpleNamespace:
    """构造共享资源替身，close 为异步桩以兼容 lifespan 复位 fixture 的收尾关闭。"""
    client = MagicMock()
    client.close = AsyncMock()
    download_manager = MagicMock()
    download_manager.close = AsyncMock()
    return SimpleNamespace(client=client, download_manager=download_manager)


async def test_generate_endpoint_receives_shared_client_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """端点把 shim 上下文传入 runner，lifespan 字典携带共享资源同一实例。"""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(
            result=_generation_result({"tool": "text_to_image", "success": True, "data": []}),
            captured=captured,
        ),
    )
    stub = _make_stub_resource()
    monkeypatch.setattr(resources_module, "_active_resource", stub)
    write_workspace_config(tmp_path)
    app = build_web_app()

    response = await _post_json(app, "/web/api/generate/text-to-image", {"prompt": "一只猫"})

    assert response.status_code == 200
    assert len(captured) == 1
    ctx = captured[0]["ctx"]
    assert ctx is not None
    lifespan = ctx.request_context.lifespan_context
    assert lifespan[LIFESPAN_KEY_CLIENT] is stub.client
    assert lifespan[LIFESPAN_KEY_DOWNLOAD_MANAGER] is stub.download_manager


async def test_generate_concurrent_requests_share_active_resource_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_web_routes: None,
    reset_http_app_state: None,
) -> None:
    """并发请求各自收到 shim 上下文且共享同一 client，全部成功无异常。"""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generate_module,
        "run_text_to_image",
        _make_fake_runner(
            result=_generation_result({"tool": "text_to_image", "success": True, "data": []}),
            captured=captured,
            yield_once=True,
        ),
    )
    stub = _make_stub_resource()
    monkeypatch.setattr(resources_module, "_active_resource", stub)
    write_workspace_config(tmp_path)
    app = build_web_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.post("/web/api/generate/text-to-image", json={"prompt": f"一只猫{i}"})
                for i in range(3)
            )
        )

    assert all(response.status_code == 200 for response in responses)
    assert len(captured) == 3
    assert all(call["ctx"] is not None for call in captured)
    assert all(
        call["ctx"].request_context.lifespan_context[LIFESPAN_KEY_CLIENT] is stub.client
        for call in captured
    )
