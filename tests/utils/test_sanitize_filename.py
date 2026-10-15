"""FileManager.sanitize_filename 与唯一文件名长度预算的表驱动契约测试。

覆盖不安全字符替换、控制字符剥离、长度截断与超长扩展名按纯词干处理、Windows
保留设备名规避与前导点处理，以及 generate_unique_filename 的词干预算守护；
长度预算按 UTF-8 字节计量，另覆盖 generate_name_from_prompt 的截断与回退分支。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from seedream_mcp.utils.core import sanitizers
from seedream_mcp.utils.io import storage as io_storage
from seedream_mcp.utils.io.storage import (
    FileManager,
    _MAX_EXTENSION_LENGTH,
    _MAX_FILENAME_LENGTH,
    _MAX_UNIQUE_BASE_LENGTH,
)


@pytest.fixture
def manager(tmp_path: Path) -> FileManager:
    """以临时目录构造 FileManager，测试不写文件内容，仅覆盖文件名与保存路径生成。"""
    return FileManager(base_dir=tmp_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 不安全字符统一替换为下划线
        ('a<b>:c"/d\\e|f?g*', "a_b__c__d_e_f_g_"),
        # 控制字符连同 DEL 一并剥离
        ("bad\x00file\x1fname\x7f", "badfilename"),
        # 常规文件名原样保留
        ("photo_2024.png", "photo_2024.png"),
        # 中文字符保留
        ("猫咪照片.jpg", "猫咪照片.jpg"),
    ],
)
def test_sanitize_filename_character_rules(manager: FileManager, raw: str, expected: str) -> None:
    """不安全字符替换为下划线，控制字符剥离，常规与中文名保留。"""
    assert manager.sanitize_filename(raw) == expected


def test_sanitize_filename_strips_unpaired_surrogates(manager: FileManager) -> None:
    """未配对代理在入口剥离，字节预算截断不因 UTF-8 编码失败翻为不透明错误。"""
    assert manager.sanitize_filename("\ud800pic.png") == "pic.png"
    long_name = "\ud800" + "图" * 300 + ".png"
    result = manager.sanitize_filename(long_name)
    assert len(result.encode("utf-8")) <= _MAX_FILENAME_LENGTH


def test_sanitize_filename_truncates_long_name_keeping_extension(manager: FileManager) -> None:
    """超长文件名截断词干并保留扩展名，总长不超过上限。"""
    raw = "x" * 300 + ".png"
    sanitized = manager.sanitize_filename(raw)
    assert len(sanitized) <= _MAX_FILENAME_LENGTH
    assert sanitized.endswith(".png")


def test_sanitize_filename_keeps_extension_at_length_cap(manager: FileManager) -> None:
    """扩展名长度恰在上限内时截断词干并完整保留扩展名，总长精确落在上限。"""
    ext = "." + "e" * (_MAX_EXTENSION_LENGTH - 1)
    raw = "x" * 300 + ext
    sanitized = manager.sanitize_filename(raw)
    assert len(sanitized) == _MAX_FILENAME_LENGTH
    assert sanitized.endswith(ext)


def test_sanitize_filename_oversized_extension_treated_as_stem(manager: FileManager) -> None:
    """超长扩展名不可能是合法图片后缀，按纯词干截断，总长不超过上限。

    旧行为按 name[:0] + ext 原样保留 300 字符扩展名，截断失效使结果突破上限。
    """
    raw = "y" * 50 + "." + "z" * 300
    sanitized = manager.sanitize_filename(raw)
    assert sanitized == raw[:_MAX_FILENAME_LENGTH]
    assert len(sanitized) == _MAX_FILENAME_LENGTH


def test_sanitize_filename_extension_gate_counts_multibyte_bytes(
    manager: FileManager,
) -> None:
    """扩展名长度门按 UTF-8 字节计量：恰 16 字节的多字节扩展名保留，17 字节按纯词干截断。

    旧行为按字符计数，多字节扩展名字符数不多而字节数超限，
    越过长度门后挤占词干预算，与文件系统目录项的字节限额口径漂移。
    """
    # 点号 1 字节 + 5 个汉字各 3 字节 = 16 字节，恰在上限内，截断词干并保留扩展名。
    at_cap = "x" * 300 + "." + "图" * 5
    sanitized = manager.sanitize_filename(at_cap)
    assert sanitized.endswith("." + "图" * 5)
    assert len(sanitized.encode("utf-8")) == _MAX_FILENAME_LENGTH

    # 追加 1 字节越过上限，超长扩展名按纯词干整体截断，不再保留扩展名。
    over_cap = "x" * 300 + "." + "图" * 5 + "a"
    truncated = manager.sanitize_filename(over_cap)
    assert truncated == over_cap[:_MAX_FILENAME_LENGTH]
    assert len(truncated.encode("utf-8")) == _MAX_FILENAME_LENGTH


def test_sanitize_filename_truncates_multibyte_name_by_utf8_bytes(
    manager: FileManager,
) -> None:
    """多字节文件名的截断按 UTF-8 字节计量，词干保持完整字符且字节数不超上限。

    旧行为按字符计数，150 个汉字（450 字节）原样通过，ext4/APFS 上必然
    ENAMETOOLONG 落盘失败。
    """
    raw = "猫" * 150 + ".png"
    sanitized = manager.sanitize_filename(raw)
    assert len(sanitized.encode("utf-8")) <= _MAX_FILENAME_LENGTH
    # 词干预算 196 字节容纳 65 个汉字，剩余 1 字节不足以再容纳一个汉字，残缺尾部被丢弃。
    assert sanitized == "猫" * 65 + ".png"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 恰在上限的带扩展名名：词干预算恰好容纳，截断为恒等变换。
        (
            "a" * (_MAX_FILENAME_LENGTH - 4) + ".png",
            "a" * (_MAX_FILENAME_LENGTH - 4) + ".png",
        ),
        # 恰在上限的无扩展名名：splitext 扩展名为空，整名恒等。
        ("n" * _MAX_FILENAME_LENGTH, "n" * _MAX_FILENAME_LENGTH),
        # 超长扩展名越过长度门但总长在预算内，纯词干截断同为恒等。
        ("y" * 50 + "." + "z" * 100, "y" * 50 + "." + "z" * 100),
        # 多段点名的词干/扩展名拆分不改变输出。
        ("a.b.c", "a.b.c"),
        # 快路径仍执行共享的 Windows 保留设备名规避。
        ("CON.txt", "CON_.txt"),
    ],
)
def test_sanitize_filename_ascii_within_budget_fast_path_parity(
    manager: FileManager, raw: str, expected: str
) -> None:
    """ASCII 名字符数即 UTF-8 字节数，预算内快路径与逐段截断路径输出一致。"""
    assert manager.sanitize_filename(raw) == expected


def test_sanitize_filename_ascii_within_budget_skips_budget_helpers(
    manager: FileManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASCII 预算内名走快路径不触发整名编码，超预算名仍经截断收口。"""
    budget_inputs: list[str] = []
    # 截断原语定义于 sanitizers，探针替换 io_storage 命名空间内的绑定。
    original = sanitizers.fit_utf8_budget

    def spy(text: str, max_bytes: int) -> str:
        budget_inputs.append(text)
        return original(text, max_bytes)

    monkeypatch.setattr(io_storage, "fit_utf8_budget", spy)
    assert manager.sanitize_filename("photo_2024.png") == "photo_2024.png"
    assert manager.sanitize_filename("n" * _MAX_FILENAME_LENGTH) == "n" * _MAX_FILENAME_LENGTH
    assert budget_inputs == []
    assert manager.sanitize_filename("x" * 300 + ".png").endswith(".png")
    assert budget_inputs


# ==================== 唯一文件名长度预算 ====================


def test_generate_unique_filename_caps_base_within_budget(manager: FileManager) -> None:
    """255 字符合法 custom_name 的词干按预算截断，拼接时间戳与哈希后总长有界。"""
    filename = manager.generate_unique_filename("c" * 255, ".png", content_hash="a" * 64)
    # 词干截到预算上限，其后缀时间戳 19 位、分隔符与 8 位哈希、扩展名长度合计封顶
    assert len(filename) <= _MAX_UNIQUE_BASE_LENGTH + 1 + 19 + 1 + 8 + len(".png")
    assert filename.startswith("c" * _MAX_UNIQUE_BASE_LENGTH + "_")


def test_generate_unique_filename_cjk_base_within_name_max_bytes(
    manager: FileManager,
) -> None:
    """255 个汉字的 custom_name 经字节预算截断后，完整文件名不超 255 字节。

    旧行为按字符计数保留 120 个汉字（360 字节），ext4/APFS 的 255 字节 NAME_MAX
    上 rename 必然 ENAMETOOLONG，保存退化为仅保留 24 小时过期 URL。
    """
    filename = manager.generate_unique_filename("猫" * 255, ".png", content_hash="a" * 64)
    assert len(filename.encode("utf-8")) <= 255
    # 词干截到 120 字节 = 40 个汉字，其后时间戳与哈希均为 ASCII。
    assert filename.startswith("猫" * 40 + "_")


def test_generate_unique_filename_short_base_not_truncated(manager: FileManager) -> None:
    """短词干不受预算影响，原样保留。"""
    filename = manager.generate_unique_filename("cat", ".png")
    assert filename.startswith("cat_")


@pytest.mark.parametrize(
    "base",
    [
        # 提示词派生的常规 ASCII 词干原样保留。
        "photo_2024",
        # 恰在词干预算上限：字符数即字节数，截断为恒等变换。
        "n" * _MAX_UNIQUE_BASE_LENGTH,
    ],
)
def test_generate_unique_filename_ascii_base_within_budget_fast_path_parity(
    manager: FileManager, base: str
) -> None:
    """ASCII 词干字符数即 UTF-8 字节数，预算内快路径与字节截断路径输出一致。"""
    assert manager.generate_unique_filename(base, ".png").startswith(base + "_")


def test_generate_unique_filename_ascii_base_within_budget_skips_budget_helper(
    manager: FileManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASCII 预算内词干走快路径不触发整词干编码，超预算词干仍经截断收口。"""
    budget_inputs: list[str] = []
    original = sanitizers.fit_utf8_budget

    def spy(text: str, max_bytes: int) -> str:
        budget_inputs.append(text)
        return original(text, max_bytes)

    monkeypatch.setattr(io_storage, "fit_utf8_budget", spy)
    assert manager.generate_unique_filename("cat", ".png").startswith("cat_")
    at_cap = "n" * _MAX_UNIQUE_BASE_LENGTH
    assert manager.generate_unique_filename(at_cap, ".png").startswith(at_cap + "_")
    assert budget_inputs == []
    # 词干预算与文件名上限之间的长度不经 sanitize 截断，仅词干预算处触发一次。
    over_budget = "u" * (_MAX_UNIQUE_BASE_LENGTH + 30)
    assert manager.generate_unique_filename(over_budget, ".png").startswith(
        "u" * _MAX_UNIQUE_BASE_LENGTH + "_"
    )
    assert budget_inputs == [over_budget]


def test_create_save_path_long_custom_name_stays_within_max_path(manager: FileManager) -> None:
    """长 custom_name 生成的路径增量由日期目录、工具目录与预算内文件名封顶。

    旧行为：词干无预算时文件名必然超出 MAX_PATH 使自动保存失败。完整路径长度
    依赖 basetemp 所在位置，仅以机器无关的相对 base_dir 增量断言封顶。
    """
    path = manager.create_save_path(
        prompt="p",
        url="https://example.com/img.png",
        tool_name="seedream",
        custom_name="c" * 255,
    )
    relative = str(path.relative_to(manager.base_dir))
    assert len(relative) <= len("2026-08-17") + 1 + len("seedream") + 1 + (
        _MAX_UNIQUE_BASE_LENGTH + 1 + 19 + 1 + 8 + len(".png")
    )


# ==================== 提示词派生文件名 ====================


@pytest.mark.parametrize(
    ("prompt", "max_length", "expected"),
    [
        # 超长 prompt 清洗后截断到 max_length。
        ("a" * 80, 50, "a" * 50),
        # 截断点落在折叠空格的下划线上，尾部下划线由 strip 去除。
        ("abcd efgh", 5, "abcd"),
        # 全标点 prompt 清洗后为空，回退 image。
        ("？？？", 50, "image"),
        # 空 prompt 回退 image。
        ("", 50, "image"),
        # 汉字属 \w 保留，空白折叠为下划线，lower 仅作用于拉丁字母。
        ("一只 猫咪 Photo", 50, "一只_猫咪_photo"),
        # 前后空白同样折叠为下划线，strip 仅修剪两端下划线。
        ("  Cat  PHOTO ", 50, "cat_photo"),
    ],
)
def test_generate_name_from_prompt_truncation_and_fallback(
    manager: FileManager, prompt: str, max_length: int, expected: str
) -> None:
    """超长 prompt 截断、清洗后为空的回退与空白折叠派生行为。"""
    assert manager.generate_name_from_prompt(prompt, max_length=max_length) == expected


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
def test_sanitize_filename_windows_reserved_names(
    manager: FileManager, raw: str, expected: str
) -> None:
    """Windows 保留设备名追加下划线规避，非保留名不受影响。"""
    assert manager.sanitize_filename(raw) == expected


def test_sanitize_filename_all_unsafe_falls_back_to_unnamed(manager: FileManager) -> None:
    """清理后为空白时回退 unnamed，保证落盘始终有可用文件名。"""
    assert manager.sanitize_filename("   ") == "unnamed"
    # 不安全字符替换为下划线而非剥离，??? 清理后为 ___ 而非空白
    assert manager.sanitize_filename("???") == "___"
