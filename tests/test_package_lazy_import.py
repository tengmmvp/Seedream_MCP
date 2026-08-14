"""包级延迟导入测试：导入 seedream_mcp 不立即加载 client 与 server。"""

import importlib
import sys


def _reload_seedream_package():
    for module_name in [
        "seedream_mcp",
        "seedream_mcp.client",
        "seedream_mcp.config",
        "seedream_mcp.server",
    ]:
        sys.modules.pop(module_name, None)
    return importlib.import_module("seedream_mcp")


def test_import_seedream_package_does_not_eager_import_client_or_server() -> None:
    package = _reload_seedream_package()

    assert package.__name__ == "seedream_mcp"
    assert "seedream_mcp.client" not in sys.modules
    assert "seedream_mcp.server" not in sys.modules


def test_accessing_export_triggers_lazy_import() -> None:
    package = _reload_seedream_package()

    _ = package.SeedreamClient
    _ = package.mcp

    assert "seedream_mcp.client" in sys.modules
    assert "seedream_mcp.server" in sys.modules


def test_all_derived_from_version_and_lazy_exports() -> None:
    """__all__ 须派生自 __version__ 与 _LAZY_EXPORTS，避免手动同步漂移。"""
    import seedream_mcp

    assert seedream_mcp.__all__ == ["__version__"] + list(seedream_mcp._LAZY_EXPORTS)


def test_every_public_export_resolves_via_getattr() -> None:
    """__all__ 中除 __version__ 外的每个导出名须经 __getattr__ 成功解析且非 None。"""
    import seedream_mcp

    for name in seedream_mcp.__all__:
        if name == "__version__":
            continue
        value = getattr(seedream_mcp, name)
        assert value is not None, f"导出名 {name!r} 解析为 None"


def test_unknown_attribute_raises_attribute_error() -> None:
    """访问未声明的属性应抛出 AttributeError，而非静默返回 None。"""
    import pytest

    import seedream_mcp

    with pytest.raises(AttributeError):
        getattr(seedream_mcp, "definitely_not_an_export")
