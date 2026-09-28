"""FileManager 快速失败、进程级缓存与保存路径扩展名收敛测试。

构造阶段拒绝非法 base_dir；get_file_manager 按原始 base_dir 复用实例并按 LRU
驱逐；save_bytes 覆盖符号链接替换不写穿、原子落盘不留临时文件、冲突改名与
失败清理。
"""

import os
import sys
from pathlib import Path

import pytest

from seedream_mcp.config import SeedreamConfig, set_active_config
from seedream_mcp.utils.core.errors import SeedreamConfigError
from seedream_mcp.utils.io.io_storage import FileManager, FileManagerError


def test_file_manager_rejects_non_directory_base_dir(tmp_path: Path) -> None:
    """指向文件的 base_dir 应被拒绝。"""
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("oops", encoding="utf-8")

    with pytest.raises(FileManagerError, match="不是目录"):
        FileManager(base_dir=file_path)


def test_file_manager_rejects_unresolvable_base_dir() -> None:
    """含嵌入 null 字符的 base_dir 被拒绝为 FileManagerError，不向调用方穿透。

    Python 3.12 在 resolve 阶段抛 OSError，3.13 起迟至 mkdir 才抛 ValueError，
    两版本统一归一且不限定文案以保持跨版本稳定。
    """
    with pytest.raises(FileManagerError):
        FileManager(base_dir=Path("\0invalid"))


def _make_cycle_dir(parent: Path, name: str = "cycle") -> Path | None:
    """构造互指链接环并返回环入口路径，resolve 必抛 RuntimeError。

    Windows 经 NTFS junction 普通用户即可创建，POSIX 经符号链接；平台或卷不
    支持时返回 None 由调用方跳过。
    """
    link = parent / name
    partner = parent / f"{name}-partner"
    try:
        if sys.platform == "win32":
            import _winapi

            # junction 目标须先存在：先立实体中转再对调，双 junction 互指成环。
            partner.mkdir()
            _winapi.CreateJunction(str(partner), str(link))
            partner.rmdir()
            _winapi.CreateJunction(str(link), str(partner))
        else:
            os.symlink(str(partner), str(link))
            os.symlink(str(link), str(partner))
    except OSError:
        return None
    return link


def test_file_manager_rejects_cycle_base_dir(tmp_path: Path) -> None:
    """base_dir 链接成环时归一为 FileManagerError，不向调用方穿透裸 RuntimeError。"""
    cycle = _make_cycle_dir(tmp_path)
    if cycle is None:
        pytest.skip("当前平台或卷无法构造链接环")

    with pytest.raises(FileManagerError, match="解析保存路径"):
        FileManager(base_dir=cycle)


def test_file_manager_default_images_root_cycle_raises_config_error(tmp_path: Path) -> None:
    """默认图片目录成环时归配置错误档案上抛，不穿透裸 RuntimeError。"""
    workspace = tmp_path / "workspace"
    (workspace / ".seedream").mkdir(parents=True)
    cycle = _make_cycle_dir(workspace / ".seedream", name="images")
    if cycle is None:
        pytest.skip("当前平台或卷无法构造链接环")
    set_active_config(SeedreamConfig(api_key="test_key", workspace_root=str(workspace)))

    with pytest.raises(SeedreamConfigError, match="工作区图片目录无法解析"):
        FileManager()


def test_file_manager_rejects_unc_base_dir_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNC 形式的 base_dir 在 resolve 前被拒绝，直连构造不触发 SMB 连接。

    调用方 tools/core/_pipeline 已有拒绝，本入口拦直连构造作为防御纵深；断言 UNC
    未进入 resolve 而非仅断言抛错，防止回归为先解析后拒绝。
    """
    from seedream_mcp.utils.io.io_path import is_unc_path

    original_resolve = Path.resolve

    def _resolve_guard(self: Path, strict: bool = False) -> Path:
        if is_unc_path(str(self)):
            raise AssertionError("UNC 路径不得进入 resolve（会触发 SMB 认证）")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _resolve_guard)

    with pytest.raises(FileManagerError, match="UNC"):
        FileManager(base_dir=Path("//host/share"))
    with pytest.raises(FileManagerError, match="UNC"):
        FileManager(base_dir=Path(r"\\host\share"))


def test_get_file_manager_caches_by_base_dir_and_evicts_lru(tmp_path: Path) -> None:
    """同 base_dir 复用同一实例，超出条目上限按最旧驱逐。"""
    from seedream_mcp.utils.io.io_storage import (
        _FILE_MANAGER_CACHE_MAX_ENTRIES,
        _file_manager_cache,
        get_file_manager,
    )

    first = get_file_manager(tmp_path)
    assert get_file_manager(tmp_path) is first

    for idx in range(_FILE_MANAGER_CACHE_MAX_ENTRIES):
        get_file_manager(tmp_path / f"dir{idx}")

    assert len(_file_manager_cache) == _FILE_MANAGER_CACHE_MAX_ENTRIES
    # 首个条目最旧被驱逐，最近插入的仍在缓存。
    assert tmp_path not in _file_manager_cache
    recent = tmp_path / f"dir{_FILE_MANAGER_CACHE_MAX_ENTRIES - 1}"
    assert recent in _file_manager_cache


def test_get_file_manager_concurrent_access_never_raises(tmp_path: Path) -> None:
    """多线程持续取缓存实例不抛 KeyError，锁消除 get 与 move_to_end 间的驱逐竞态。

    保存请求经 to_thread 构造 manager，并发键数远超缓存上限使驱逐高频发生，
    无锁实现下 get 命中后键被并发驱逐会使 move_to_end 抛错。
    """
    import threading

    from seedream_mcp.utils.io.io_storage import get_file_manager

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            barrier.wait()
            for round_idx in range(200):
                get_file_manager(tmp_path / f"d{index}_{round_idx % 8}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(idx,)) for idx in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []


def test_file_manager_accepts_valid_base_dir(tmp_path: Path) -> None:
    """合法 base_dir 接受并完成创建。"""
    base_dir = tmp_path / "images"
    manager = FileManager(base_dir=base_dir)

    assert manager.base_dir == base_dir.resolve()
    assert manager.base_dir.exists()


def test_create_save_path_normalizes_non_image_extension(tmp_path: Path) -> None:
    """URL 派生的非图片扩展名应收敛到白名单默认 .jpeg，防止任意后缀落盘。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(
        prompt="test", url="https://example.com/img.aspx", tool_name="t"
    )
    assert path.suffix.lower() == ".jpeg"


def test_create_save_path_keeps_whitelisted_extension(tmp_path: Path) -> None:
    """白名单内的图片扩展名原样保留。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path(prompt="test", url="https://example.com/img.png", tool_name="t")
    assert path.suffix.lower() == ".png"


def test_create_save_path_from_extension_normalizes_non_image_extension(tmp_path: Path) -> None:
    """入参扩展名不在白名单时收敛到默认图片扩展名，与 create_save_path 同口径。

    字节签名嗅探入口不接受任意后缀落盘，.html 等非图片扩展名回退 .jpeg。
    """
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path_from_extension(prompt="test", extension=".html", tool_name="t")
    assert path.suffix.lower() == ".jpeg"


def test_create_save_path_from_extension_keeps_whitelisted_extension(tmp_path: Path) -> None:
    """入参扩展名在白名单内时原样保留，不触发默认回退。"""
    manager = FileManager(base_dir=tmp_path)
    path = manager.create_save_path_from_extension(prompt="test", extension=".png", tool_name="t")
    assert path.suffix.lower() == ".png"


def test_save_bytes_replaces_symlink_itself_without_write_through(tmp_path: Path) -> None:
    """save_bytes 落向符号链接路径时替换链接本身为常规文件，不写穿到其指向。

    原子落盘经随机名临时文件 + os.replace，rename 对符号链接是替换而非跟随，
    断言数据落在链接路径且原指向目标未被创建。
    """
    victim_target = tmp_path / "nonexistent_target"
    link = tmp_path / "link"
    try:
        os.symlink(victim_target, link)
    except OSError:
        pytest.skip("当前进程无法创建符号链接（Windows 可能需要开发者模式或管理员）")
    manager = FileManager(base_dir=tmp_path)

    manager.save_bytes(link, b"data")

    assert link.is_symlink() is False
    assert link.read_bytes() == b"data"
    assert victim_target.exists() is False


def test_save_bytes_atomic_write_leaves_no_temp(tmp_path: Path) -> None:
    """save_bytes 经随机名临时文件原子 replace 落盘，成功后不留临时文件残留。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"

    result = manager.save_bytes(path, b"payload")

    assert path.read_bytes() == b"payload"
    # 成功落盘后目录内仅最终文件，无随机名临时文件残留
    assert list(tmp_path.iterdir()) == [path]
    assert result["file_size"] == len(b"payload")
    assert result["file_path"] == str(path)


def test_save_bytes_overwrite_replaces_existing_file(tmp_path: Path) -> None:
    """overwrite=True 时原子 replace 覆盖已有文件。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"
    path.write_bytes(b"old-content")

    manager.save_bytes(path, b"new", overwrite=True)

    assert path.read_bytes() == b"new"


def test_save_bytes_no_overwrite_renames_on_conflict(tmp_path: Path) -> None:
    """overwrite=False 且文件已存在时，追加内容短哈希生成不冲突的新文件名。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"
    path.write_bytes(b"old-content")

    manager.save_bytes(path, b"new-content", overwrite=False)

    # 原文件保留旧内容
    assert path.read_bytes() == b"old-content"
    # 新文件以内容哈希后缀生成
    png_files = [f for f in tmp_path.iterdir() if f.suffix == ".png"]
    assert len(png_files) == 2


def test_save_bytes_cleans_random_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """replace 失败时随机名临时文件被 finally 清理，目录内不留残留。"""
    manager = FileManager(base_dir=tmp_path)
    path = tmp_path / "out.png"

    def _raise_on_replace(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", _raise_on_replace)

    with pytest.raises(FileManagerError, match="写入文件失败"):
        manager.save_bytes(path, b"data")

    # 失败路径清理随机名临时文件，目录内无残留
    assert list(tmp_path.iterdir()) == []
