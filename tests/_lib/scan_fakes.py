"""scan 层扫描条目的共享测试工厂。

ScanEntry 工厂供 test_browse_tool_result 与 test_find_images_directory
复用，ScanEntry 增字段时仅此一份须同步。
"""

from __future__ import annotations

from pathlib import Path

from seedream_mcp.utils.io.path import normcase_parts
from seedream_mcp.utils.io.scan import ScanEntry


def _scan_entry(path: Path, resolved: Path | None = None) -> ScanEntry:
    """测试用扫描条目：resolved 缺省取 path，携带 normcase 分量。"""
    resolved_path = resolved if resolved is not None else path
    return ScanEntry(resolved_path, normcase_parts(resolved_path))
