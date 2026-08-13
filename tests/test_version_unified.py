"""版本号单一来源守护：包常量与 server 常量均指向 version.py。"""

from pathlib import Path

from seedream_mcp import __version__ as package_version
from seedream_mcp.server import SERVER_VERSION
from seedream_mcp.version import __version__ as source_version


def test_runtime_version_is_single_source() -> None:
    """运行时版本常量应全部引用同一来源。"""
    assert source_version == package_version
    assert source_version == SERVER_VERSION


def test_pyproject_uses_dynamic_version_from_source_file() -> None:
    """打包版本应从 seedream_mcp/version.py 动态读取。"""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in content
    assert 'path = "seedream_mcp/version.py"' in content
