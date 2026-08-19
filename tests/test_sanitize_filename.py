"""FileManager.sanitize_filename 与唯一文件名长度预算的表驱动契约测试。

覆盖不安全字符替换、控制字符剥离、长度截断与超长扩展名按纯词干处理、Windows
保留设备名规避与前导点处理，以及 generate_unique_filename 的词干预算守护。
"""

from __future__ import annotations

import pytest

from seedream_mcp.utils.io.io_storage import (
    FileManager,
    _MAX_EXTENSION_LENGTH,
    _MAX_FILENAME_LENGTH,
    _MAX_UNIQUE_BASE_LENGTH,
)


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


def test_sanitize_filename_keeps_extension_at_length_cap(manager):
    """扩展名长度恰在上限内时截断词干并完整保留扩展名，总长精确落在上限。"""
    ext = "." + "e" * (_MAX_EXTENSION_LENGTH - 1)
    raw = "x" * 300 + ext
    sanitized = manager.sanitize_filename(raw)
    assert len(sanitized) == _MAX_FILENAME_LENGTH
    assert sanitized.endswith(ext)


def test_sanitize_filename_oversized_extension_treated_as_stem(manager):
    """超长扩展名不可能是合法图片后缀，按纯词干截断，总长不超过上限。

    旧行为按 name[:0] + ext 原样保留 300 字符扩展名，截断失效使结果突破上限。
    """
    raw = "y" * 50 + "." + "z" * 300
    sanitized = manager.sanitize_filename(raw)
    assert sanitized == raw[:_MAX_FILENAME_LENGTH]
    assert len(sanitized) == _MAX_FILENAME_LENGTH


# ==================== 唯一文件名长度预算 ====================


def test_generate_unique_filename_caps_base_within_budget(manager):
    """255 字符合法 custom_name 的词干按预算截断，拼接时间戳与哈希后总长有界。"""
    filename = manager.generate_unique_filename("c" * 255, ".png", content_hash="a" * 64)
    # 词干截到预算上限，其后缀时间戳 19 位、分隔符与 8 位哈希、扩展名长度合计封顶
    assert len(filename) <= _MAX_UNIQUE_BASE_LENGTH + 1 + 19 + 1 + 8 + len(".png")
    assert filename.startswith("c" * _MAX_UNIQUE_BASE_LENGTH + "_")


def test_generate_unique_filename_short_base_not_truncated(manager):
    """短词干不受预算影响，原样保留。"""
    filename = manager.generate_unique_filename("cat", ".png")
    assert filename.startswith("cat_")


def test_create_save_path_long_custom_name_stays_within_max_path(manager, tmp_path):
    """长 custom_name 生成的完整保存路径不超 Windows 默认 MAX_PATH 260。

    旧行为：词干无预算时路径必然超出 MAX_PATH 使自动保存失败。断言基于本仓库
    测试协议的短 basetemp 前提。
    """
    path = manager.create_save_path(
        prompt="p",
        url="https://example.com/img.png",
        tool_name="seedream",
        custom_name="c" * 255,
    )
    assert len(str(path)) < 260
    # 相对 base_dir 的增量部分由日期目录、工具目录与预算内文件名构成，机器无关封顶
    relative = str(path.relative_to(manager.base_dir))
    assert len(relative) <= len("2026-08-17") + 1 + len("seedream") + 1 + (
        _MAX_UNIQUE_BASE_LENGTH + 1 + 19 + 1 + 8 + len(".png")
    )


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
