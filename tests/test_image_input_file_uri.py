"""_file_uri_to_path 的 file:// URI 解析与主机拒绝测试。

函数位于 ``utils/io/io_path``，将 MCP Roots 声明的 file:// URI 转为本地路径。
安全契约：拒绝非 localhost 主机的 file://host/share，避免 Windows 下触发 SMB
连接泄露凭据；放行标准本地绝对路径 file:///abs/path。

注：file://localhost//server/share 形式的 UNC 是否被拒绝取决于 Path.resolve 对
不可达 UNC 主机是否抛错，属非确定行为，依赖网络/SMB 状态，故不纳入断言；
实际的 SMB 防护由拒绝非 localhost 主机的 netloc 守卫保证，跨平台稳定。
"""

from __future__ import annotations

from pathlib import Path

from seedream_mcp.utils.io.io_path import _file_uri_to_path


def test_file_uri_to_path_rejects_non_localhost_host() -> None:
    """file://host/share 的 netloc 非空且非 localhost → 拒绝并返回 None，跨平台一致。"""
    assert _file_uri_to_path("file://host/share") is None
    assert _file_uri_to_path("file://server/share/path.png") is None


def test_file_uri_to_path_accepts_absolute_local_path() -> None:
    """file:///abs/path.png 放行为本地 Path，跨平台一致。"""
    resolved = _file_uri_to_path("file:///abs/path.png")

    assert resolved is not None
    assert isinstance(resolved, Path)


def test_file_uri_to_path_rejects_non_file_scheme() -> None:
    """非 file scheme 一律拒绝。"""
    assert _file_uri_to_path("http://example.com/x.png") is None
    assert _file_uri_to_path("https://example.com/x.png") is None


def test_file_uri_to_path_accepts_localhost_with_drive() -> None:
    """file://localhost/C:/path.png 等同本地路径，放行。"""
    resolved = _file_uri_to_path("file://localhost/C:/path.png")

    assert resolved is not None
