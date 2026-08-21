"""MCPServer 构造的 requestState 密钥环与静态列表缓存提示测试。

resources 在模块 import 期构造进程级 MCPServer 单例，requestState 密钥环经
config 的活动配置访问器取值，配置不可用时回退 None 保持 SDK 默认临时密钥，
import 链不因缺配置中断；静态列表缓存提示仅覆盖进程内不变的面。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import RequestStateSecurity
from mcp.server.request_state import RequestStateBoundary

import seedream_mcp.resources as resources_module
from seedream_mcp import config as config_module
from seedream_mcp.utils.core.errors import SeedreamConfigError


def test_active_request_state_keys_reads_active_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动配置就绪时返回其密钥环字段。"""
    keys = (b"\x01" * 32,)
    monkeypatch.setattr(
        config_module, "get_active_config", lambda: SimpleNamespace(request_state_secret_keys=keys)
    )

    assert config_module.active_request_state_keys() == keys


def test_active_request_state_keys_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活动配置未配置密钥环时返回 None。"""
    monkeypatch.setattr(
        config_module, "get_active_config", lambda: SimpleNamespace(request_state_secret_keys=None)
    )

    assert config_module.active_request_state_keys() is None


@pytest.mark.parametrize(
    "error",
    [SeedreamConfigError("缺 API 密钥"), OSError("配置文件不可读")],
)
def test_active_request_state_keys_falls_back_to_none_on_config_failure(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """配置构建失败或读取抛 OSError 时返回 None，模块导入不因缺配置而炸。"""

    def _raise() -> None:
        raise error

    monkeypatch.setattr(config_module, "get_active_config", _raise)

    assert config_module.active_request_state_keys() is None


def test_build_request_state_security_returns_none_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未配置密钥时不构造策略，MCPServer 保持 SDK 默认临时密钥。"""
    monkeypatch.setattr(resources_module, "active_request_state_keys", lambda: None)

    assert resources_module._build_request_state_security() is None


def test_build_request_state_security_builds_policy_from_key_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置密钥环后构造 RequestStateSecurity，密钥经 config 校验满足强度下限。"""
    keys = (b"\x02" * 32, b"\x03" * 32)
    monkeypatch.setattr(resources_module, "active_request_state_keys", lambda: keys)

    security = resources_module._build_request_state_security()

    assert isinstance(security, RequestStateSecurity)


@pytest.mark.parametrize("keys", [None, (b"\x02" * 32,)])
def test_create_mcp_server_constructs_in_both_key_forms(
    monkeypatch: pytest.MonkeyPatch, keys: tuple[bytes, ...] | None
) -> None:
    """密钥环两种形态下 MCPServer 构造成功，None 形态保持 SDK 默认。"""
    monkeypatch.setattr(resources_module, "active_request_state_keys", lambda: keys)

    server = resources_module._create_mcp_server()

    assert server.name == resources_module.SERVER_NAME


def test_static_list_cache_hints_cover_only_static_faces() -> None:
    """缓存提示仅覆盖五个静态面并保持默认 private 作用域，resources/read 不在内。"""
    hints = resources_module._STATIC_LIST_CACHE_HINTS

    assert set(hints) == {
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "server/discover",
    }
    assert all(hint.ttl_ms == resources_module._STATIC_LIST_CACHE_TTL_MS for hint in hints.values())
    assert all(hint.scope == "private" for hint in hints.values())


def _singleton_boundary() -> RequestStateBoundary:
    """返回单例 middleware 中的 requestState boundary，供重绑用例定位。"""
    return next(
        mw for mw in resources_module.mcp.middleware if isinstance(mw, RequestStateBoundary)
    )


@pytest.mark.parametrize("keys", [None, (b"\x02" * 32,)])
def test_rebind_request_state_security_updates_boundary(
    keys: tuple[bytes, ...] | None,
) -> None:
    """重绑以传入密钥环替换 boundary 的 security，None 形态重绑回 SDK 默认。"""
    boundary = _singleton_boundary()
    before = boundary._security

    assert resources_module.rebind_request_state_security(keys) is True

    after = boundary._security
    assert after is not before
    if keys:
        assert isinstance(after, RequestStateSecurity)
    boundary._security = before


def test_rebind_request_state_security_skips_when_boundary_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SDK 私有路径中找不到 boundary 时告警跳过，返回 False 且不抛异常。

    配置了密钥环的场景额外向 stderr 输出多副本解封退化告警，未配置时不输出。
    """
    monkeypatch.setattr(resources_module.mcp._lowlevel_server, "middleware", [], raising=False)

    assert resources_module.rebind_request_state_security((b"\x01" * 32,)) is False

    stderr = capsys.readouterr().err
    assert "多副本部署的 requestState 解封将失败" in stderr
    assert "进程临时密钥" in stderr

    assert resources_module.rebind_request_state_security(None) is False
    assert capsys.readouterr().err == ""


def test_rebind_request_state_security_survives_missing_public_attribute(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公开属性 mcp.middleware 缺失时告警返回 False，探测失败不阻断启动。"""
    monkeypatch.delattr(type(resources_module.mcp), "middleware")

    assert resources_module.rebind_request_state_security((b"\x01" * 32,)) is False

    assert "退化为进程临时密钥" in capsys.readouterr().err
