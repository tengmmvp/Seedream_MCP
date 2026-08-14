"""UNC 路径拒绝测试。

Windows UNC 路径（\\\\host\\share 或 //host/share）的 resolve 会触发 SMB 认证，
须在 resolve 前由 _is_unc_path 拦截。覆盖 _is_unc_path、is_path_within_base、
is_path_within_any_base、normalize_path、_file_uri_to_path 对 UNC 的拒绝行为。
"""

from pathlib import Path

import pytest

from seedream_mcp.utils.io.io_path import (
    _file_uri_to_path,
    _is_unc_path,
    is_path_within_any_base,
    is_path_within_base,
    normalize_path,
)

# ==================== _is_unc_path ====================


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
    assert _is_unc_path(path) is True


def test_is_unc_path_strips_leading_whitespace() -> None:
    """带前导空格的 UNC 路径仍被识别。"""
    assert _is_unc_path("  \\\\host\\share") is True
    assert _is_unc_path("  //host/share") is True


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
    assert _is_unc_path(path) is False


def test_is_unc_path_rejects_single_leading_slash() -> None:
    """单个前导斜杠不是 UNC。"""
    assert _is_unc_path("/home/user") is False


# ==================== is_path_within_base ====================


def test_is_path_within_base_rejects_unc() -> None:
    """UNC 路径直接判为越界，不进入 resolve 以免触发 SMB。"""
    assert is_path_within_base(Path("\\\\host\\share\\file.png"), Path("C:/base")) is False


def test_is_path_within_base_rejects_unc_forward_slash() -> None:
    assert is_path_within_base(Path("//host/share/file.png"), Path("/base")) is False


def test_is_path_within_base_accepts_inside(tmp_path: Path) -> None:
    f = tmp_path / "file.png"
    f.touch()
    assert is_path_within_base(f, tmp_path) is True


def test_is_path_within_base_rejects_outside(tmp_path: Path) -> None:
    inside = tmp_path / "sub" / "file.png"
    outside = tmp_path.parent / "sibling"
    outside.mkdir(exist_ok=True)
    assert is_path_within_base(inside, outside) is False


# ==================== is_path_within_any_base ====================


def test_is_path_within_any_base_rejects_unc(tmp_path: Path) -> None:
    """UNC 路径在多根场景下同样直接判为越界。"""
    bases = [tmp_path, Path("C:/other")]
    assert is_path_within_any_base(Path("\\\\host\\share\\file.png"), bases) is False


def test_is_path_within_any_base_accepts_inside_one(tmp_path: Path) -> None:
    base_a = tmp_path / "a"
    base_b = tmp_path / "b"
    base_a.mkdir()
    base_b.mkdir()
    f = base_b / "file.png"
    f.touch()
    assert is_path_within_any_base(f, [base_a, base_b]) is True


def test_is_path_within_any_base_rejects_all_outside(tmp_path: Path) -> None:
    base_a = tmp_path / "a"
    base_b = tmp_path / "b"
    base_a.mkdir()
    base_b.mkdir()
    outside = tmp_path / "c" / "file.png"
    assert is_path_within_any_base(outside, [base_a, base_b]) is False


# ==================== normalize_path ====================


def test_normalize_path_rejects_unc_backslash() -> None:
    with pytest.raises(ValueError, match="UNC"):
        normalize_path("\\\\host\\share\\file.png")


def test_normalize_path_rejects_unc_forward_slash() -> None:
    with pytest.raises(ValueError, match="UNC"):
        normalize_path("//host/share/file.png")


def test_normalize_path_accepts_normal_absolute(tmp_path: Path) -> None:
    f = tmp_path / "x.png"
    f.touch()
    result = normalize_path(str(f))
    assert result.resolve() == f.resolve()


def test_normalize_path_resolves_relative(tmp_path: Path) -> None:
    result = normalize_path("sub/file.png", str(tmp_path))
    assert result == (tmp_path / "sub" / "file.png").resolve()


# ==================== _file_uri_to_path ====================


def test_file_uri_to_path_rejects_non_file_scheme() -> None:
    assert _file_uri_to_path("http://example.com/x.png") is None


def test_file_uri_to_path_rejects_unc_netloc() -> None:
    """file://host/share 形式的 netloc 非 localhost 直接拒绝。"""
    assert _file_uri_to_path("file://host/share/file.png") is None


def test_file_uri_to_path_rejects_unc_path_form() -> None:
    """file://localhost//server/share 的 path 为 UNC 形式也拒绝。"""
    assert _file_uri_to_path("file://localhost//server/share") is None


def test_file_uri_to_path_accepts_localhost(tmp_path: Path) -> None:
    f = tmp_path / "x.png"
    f.touch()
    # file://localhost/path 形式应被接受
    uri = f.as_uri().replace("file:///", "file://localhost/", 1)
    result = _file_uri_to_path(uri)
    assert result is not None
    assert result.resolve() == f.resolve()


def test_file_uri_to_path_accepts_local_file(tmp_path: Path) -> None:
    f = tmp_path / "img.png"
    f.touch()
    result = _file_uri_to_path(f.as_uri())
    assert result is not None
    assert result.resolve() == f.resolve()


def test_file_uri_to_path_rejects_malformed_uri() -> None:
    assert _file_uri_to_path("file://") is None
