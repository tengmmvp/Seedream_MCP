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
