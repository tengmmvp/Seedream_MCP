"""FileManager.sanitize_filename 的表驱动契约测试。

覆盖不安全字符替换、控制字符剥离、长度截断、Windows 保留设备名规避与前导点处理，
这些规则直接影响跨平台落盘正确性与安全性。
"""

from __future__ import annotations

import pytest

from seedream_mcp.utils.io.io_storage import FileManager, _MAX_FILENAME_LENGTH


@pytest.fixture
def manager(tmp_path):
    """以临时目录构造 FileManager，测试不落盘只调纯函数。"""
    return FileManager(base_dir=tmp_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 不安全字符统一替换为下划线
        ('a<b>:c"/d\\e|f?g*', "a_b__c__d_e_f_g_"),
        # 控制字符（含 DEL）直接剥离
        ("bad\x00file\x1fname\x7f", "badfilename"),
        # 常规文件名原样保留
        ("photo_2024.png", "photo_2024.png"),
        # 中文字符保留
        ("猫咪照片.jpg", "猫咪照片.jpg"),
    ],
)
def test_sanitize_filename_character_rules(manager, raw, expected):
    assert manager.sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_long_name_keeping_extension(manager):
    """超长文件名截断词干并保留扩展名，总长不超过上限。"""
    raw = "x" * 300 + ".png"
    sanitized = manager.sanitize_filename(raw)
    assert len(sanitized) <= _MAX_FILENAME_LENGTH
    assert sanitized.endswith(".png")


def test_sanitize_filename_oversized_extension_clamps_to_zero(manager):
    """扩展名自身超长时词干长度下限取 0，词干截空并完整保留扩展名，不从尾部误截。"""
    raw = "y" * 50 + "." + "z" * 300
    sanitized = manager.sanitize_filename(raw)
    assert sanitized == "." + "z" * 300


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 保留设备名的词干末尾追加下划线，使词干不再匹配保留名
        ("CON.txt", "CON_.txt"),
        ("nul", "nul_"),
        ("Aux.png", "Aux_.png"),
        # 前导点先按 Windows 规则归一化词干再判定，.CON 不会因首段为空而漏检；
        # 词干为空时下划线追加在点之前，词干不再匹配保留名
        (".CON", "_.CON"),
        # 非保留名不受影响
        ("console.txt", "console.txt"),
        ("contact.png", "contact.png"),
    ],
)
def test_sanitize_filename_windows_reserved_names(manager, raw, expected):
    assert manager.sanitize_filename(raw) == expected


def test_sanitize_filename_all_unsafe_falls_back_to_unnamed(manager):
    """清理后为空白时回退 unnamed，保证落盘始终有可用文件名。"""
    assert manager.sanitize_filename("   ") == "unnamed"
    # 不安全字符替换为下划线而非剥离，??? 清理后为 ___ 而非空白
    assert manager.sanitize_filename("???") == "___"
