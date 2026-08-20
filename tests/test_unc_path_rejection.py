"""UNC 路径拒绝测试。

Windows UNC 路径的 resolve 会触发 SMB 认证，须在 resolve 前拦截。覆盖
is_unc_path、is_within_resolved、normalize_path、_file_uri_to_path 的拒绝语义，
以及 normalize_path 对 Windows 驱动器相对路径的同口径拒绝。
"""

import sys
from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_path import (
    _file_uri_to_path,
    is_unc_path,
    is_within_resolved,
    normalize_path,
)


def _patch_resolve_exploding_only_on_unc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Path.resolve 改为仅对 UNC 路径爆炸，非 UNC 路径回退真实 resolve。

    全量爆炸补丁会把合法根目录与候选的 resolve 一并误报，守卫范围收窄到
    UNC 前缀路径：UNC 进入 resolve 即时失败，其余路径保持真实解析语义。
    """
    original_resolve = Path.resolve

    def _resolve_guard(self: Path, strict: bool = False) -> Path:
        if is_unc_path(str(self)):
            raise AssertionError("UNC 路径不得进入 resolve（会触发 SMB 认证）")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", _resolve_guard)


# ==================== is_unc_path ====================


@pytest.mark.parametrize(
    "path",
    [
        "\\\\host\\share",
        "\\\\host\\share\\file.png",
        "\\\\host\\c$\\file.png",
        "//host/share",
        "//host/share/file.png",
    ],
)
def test_is_unc_path_detects_unc(path: str) -> None:
    """反斜杠与正斜杠形态的 UNC 路径均被识别。"""
    assert is_unc_path(path) is True


def test_is_unc_path_strips_leading_whitespace() -> None:
    """带前导空格的 UNC 路径仍被识别。"""
    assert is_unc_path("  \\\\host\\share") is True
    assert is_unc_path("  //host/share") is True


@pytest.mark.parametrize(
    "path",
    [
        "/home/user/file.png",
        "relative/path.png",
        "file.png",
        "",
    ],
)
def test_is_unc_path_rejects_non_unc(path: str) -> None:
    """绝对、相对与空字符串等非 UNC 输入不命中。"""
    assert is_unc_path(path) is False


def test_is_unc_path_rejects_single_leading_slash() -> None:
    """单个前导斜杠不是 UNC。"""
    assert is_unc_path("/home/user") is False


# ==================== is_within_resolved ====================


def test_is_within_resolved_accepts_inside(tmp_path: Path) -> None:
    """已 resolve 的路径与根直接比较，界内路径判 True。"""
    f = (tmp_path / "file.png").resolve()
    assert is_within_resolved(f, tmp_path.resolve()) is True


def test_is_within_resolved_rejects_outside(tmp_path: Path) -> None:
    """路径不在给定根内判 False。"""
    inside = (tmp_path / "sub" / "file.png").resolve()
    outside = (tmp_path.parent / "sibling").resolve()
    outside.mkdir(exist_ok=True)
    assert is_within_resolved(inside, outside) is False


def test_is_within_resolved_accepts_one_of_multiple_bases(tmp_path: Path) -> None:
    """多根场景下按根逐一比较：命中任一根即界内，全部未命中判越界。"""
    base_a = tmp_path / "a"
    base_b = tmp_path / "b"
    base_a.mkdir()
    base_b.mkdir()
    f = (base_b / "file.png").resolve()
    assert is_within_resolved(f, base_a.resolve()) is False
    assert is_within_resolved(f, base_b.resolve()) is True


# ==================== normalize_path ====================


def test_normalize_path_rejects_unc_backslash() -> None:
    """反斜杠 UNC 输入抛 ValueError。"""
    with pytest.raises(ValueError, match="UNC"):
        normalize_path("\\\\host\\share\\file.png")


def test_normalize_path_rejects_unc_forward_slash() -> None:
    """正斜杠 UNC 输入抛 ValueError。"""
    with pytest.raises(ValueError, match="UNC"):
        normalize_path("//host/share/file.png")


def test_normalize_path_accepts_normal_absolute(tmp_path: Path) -> None:
    """普通绝对路径正常规范化，结果与原路径 resolve 等价。"""
    f = tmp_path / "x.png"
    f.touch()
    result = normalize_path(str(f))
    assert result.resolve() == f.resolve()


def test_normalize_path_resolves_relative(tmp_path: Path) -> None:
    """相对路径按 base_dir 解析为绝对路径。"""
    result = normalize_path("sub/file.png", str(tmp_path))
    assert result == (tmp_path / "sub" / "file.png").resolve()


def test_normalize_path_oserror_preserves_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError 归一为 ValueError 时保留 errno 原因，不丢失为笼统的路径格式错误。"""
    import errno

    def _raise_enametoolong(self: Path, strict: bool = False) -> Path:
        del strict
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(Path, "resolve", _raise_enametoolong)

    with pytest.raises(ValueError, match="File name too long"):
        normalize_path(str(tmp_path / "x.png"))


@pytest.mark.skipif(sys.platform != "win32", reason="驱动器相对路径仅 Windows 有 drive 语义")
@pytest.mark.parametrize("base_dir", [None, str(Path.cwd())])
def test_normalize_path_rejects_drive_relative_path(base_dir: str | None) -> None:
    """Windows 驱动器相对路径 C:foo 有 drive 无 root，与 UNC 同口径拒绝。

    pathlib 的 / 拼接对该形态会丢弃 base_dir，resolve 落到该盘进程 CWD，静默绕开
    指定的基础目录。
    """
    with pytest.raises(ValueError, match="驱动器相对"):
        normalize_path("C:foo.png", base_dir)


@pytest.mark.skipif(sys.platform != "win32", reason="驱动器相对路径仅 Windows 有 drive 语义")
def test_normalize_path_accepts_drive_absolute_path(tmp_path: Path) -> None:
    """带根分隔符的驱动器绝对路径 C:\\foo 不受驱动器相对拒绝影响。"""
    result = normalize_path(str(tmp_path / "x.png"))
    assert result == (tmp_path / "x.png").resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 无 drive 语义，C:foo 为普通相对路径")
def test_normalize_path_posix_treats_colon_name_as_relative(tmp_path: Path) -> None:
    """POSIX 上含冒号的输入是普通相对路径，正常按 base_dir 解析，不受拒绝分支影响。"""
    result = normalize_path("C:foo.png", str(tmp_path))
    assert result == (tmp_path / "C:foo.png").resolve()


# ==================== _file_uri_to_path ====================


def test_file_uri_to_path_rejects_non_file_scheme() -> None:
    """非 file scheme 的 URI 返回 None。"""
    assert _file_uri_to_path("http://example.com/x.png") is None


def test_file_uri_to_path_rejects_unc_netloc() -> None:
    """file://host/share 形式的 netloc 非 localhost 直接拒绝。"""
    assert _file_uri_to_path("file://host/share/file.png") is None


def test_file_uri_to_path_rejects_unc_path_form() -> None:
    """file://localhost//server/share 的 path 为 UNC 形式也拒绝。"""
    assert _file_uri_to_path("file://localhost//server/share") is None


def test_file_uri_to_path_accepts_localhost(tmp_path: Path) -> None:
    """file://localhost/path 形式接受并解析为本地路径。"""
    f = tmp_path / "x.png"
    f.touch()
    uri = f.as_uri().replace("file:///", "file://localhost/", 1)
    result = _file_uri_to_path(uri)
    assert result is not None
    assert result.resolve() == f.resolve()


def test_file_uri_to_path_accepts_local_file(tmp_path: Path) -> None:
    """标准本地 file URI 接受并解析为原路径。"""
    f = tmp_path / "img.png"
    f.touch()
    result = _file_uri_to_path(f.as_uri())
    assert result is not None
    assert result.resolve() == f.resolve()


def test_file_uri_to_path_rejects_malformed_uri() -> None:
    """畸形 file URI 返回 None 而非抛异常。"""
    assert _file_uri_to_path("file://") is None


def test_resolve_local_image_candidate_skips_unc_without_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNC 输入在候选定位中于 resolve 前被拦截，不触发 SMB 连接。

    断言 UNC 路径未进入 resolve 而非仅断言返回 None，防止回归为先 resolve 后
    拒绝；合法根目录的 resolve 不在守卫范围。
    """
    from seedream_mcp.utils.images.image_validation import resolve_local_image_candidate

    _patch_resolve_exploding_only_on_unc(monkeypatch)

    assert resolve_local_image_candidate("\\\\attacker\\share\\x.png", [tmp_path]) is None
    assert resolve_local_image_candidate("//attacker/share/x.png", [tmp_path]) is None


def test_resolves_outside_workspace_skips_unc_candidates_without_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNC 根下相对路径拼接出的候选在 resolve 前被逐候选守卫拦截。

    输入级检查只覆盖 UNC 直接输入；UNC 根拼出的候选同样以 UNC 前缀开头，resolve
    会触发 SMB 认证。断言 UNC 候选未进入 resolve，合法路径的 resolve 不误报。
    """
    from seedream_mcp.utils.images.image_input import _resolves_outside_workspace

    _patch_resolve_exploding_only_on_unc(monkeypatch)

    unc_root = Path("\\\\attacker\\share")
    assert _resolves_outside_workspace("relative/x.png", [unc_root]) is True


def test_validate_image_input_rejects_unc_before_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公开导出的 validate_image_input 对 UNC 输入在 resolve 前拒绝，不触发 SMB。

    断言 UNC 路径未进入 resolve 而非仅断言抛错，防止回归为先解析后拒绝。
    """
    from seedream_mcp.utils.core.errors import SeedreamValidationError
    from seedream_mcp.utils.images.image_validation import validate_image_input

    _patch_resolve_exploding_only_on_unc(monkeypatch)

    with pytest.raises(SeedreamValidationError, match="UNC"):
        validate_image_input("\\\\attacker\\share\\x.png")
    with pytest.raises(SeedreamValidationError, match="UNC"):
        validate_image_input("//attacker/share/x.png")
