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


@pytest.mark.skipif(
    sys.platform != "win32", reason="冒号分量拒绝仅 win32 生效，POSIX 冒号是合法文件名字符"
)
@pytest.mark.parametrize("base_dir", [None, str(Path.cwd())])
def test_normalize_path_rejects_ads_colon_in_final_component(
    tmp_path: Path, base_dir: str | None
) -> None:
    """多字符名含冒号的 NTFS 备用数据流形态被拒绝，单字母形态已由驱动器相对检查拦截。

    photo.png:hidden 的冒号后是流名，resolve 不剥离，路径会带着流名通过越界校验，
    打开时命中的是同文件的另一数据流而非校验时判定的文件。
    """
    with pytest.raises(ValueError, match="备用数据流"):
        normalize_path("photo.png:hidden", base_dir)
    with pytest.raises(ValueError, match="备用数据流"):
        normalize_path(str(tmp_path / "photo.png:hidden"))


@pytest.mark.skipif(
    sys.platform != "win32", reason="冒号分量拒绝仅 win32 生效，POSIX 冒号是合法文件名字符"
)
def test_normalize_path_rejects_ads_colon_in_intermediate_component(tmp_path: Path) -> None:
    """中间分量含冒号的 NTFS 备用数据流形态被逐分量拒绝。

    仅查最终分量时 foo:hidden/bar.png 通过校验，后续 open 才以误导性 OSError 深层
    爆出；win32 对除驱动器符外的任意分量含冒号一律拒绝，相对形态与绝对形态同口径。
    """
    with pytest.raises(ValueError, match="备用数据流"):
        normalize_path("foo:hidden/bar.png", str(tmp_path))
    with pytest.raises(ValueError, match="备用数据流"):
        normalize_path("foo:hidden\\bar.png", str(tmp_path))
    with pytest.raises(ValueError, match="备用数据流"):
        normalize_path(str(tmp_path / "foo:hidden" / "bar.png"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 冒号是合法文件名字符，不经拒绝分支")
def test_normalize_path_posix_treats_ads_name_as_relative(tmp_path: Path) -> None:
    """POSIX 上多字符名含冒号是普通相对路径，不受 win32 拒绝分支影响。"""
    result = normalize_path("photo.png:hidden", str(tmp_path))
    assert result == (tmp_path / "photo.png:hidden").resolve()


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


# ==================== 参考图读取链的 ADS 冒号分量拒绝 ====================


@pytest.mark.skipif(
    sys.platform != "win32", reason="冒号分量拒绝仅 win32 生效，POSIX 冒号是合法文件名字符"
)
def test_resolve_local_image_candidate_rejects_ads_colon_reference(
    tmp_path: Path,
) -> None:
    """候选定位入口对 ADS 形态输入直接抛校验错误，先于候选构造与 stat。

    名字形态即可触发拒绝，无需构造真实 NTFS 流。
    """
    from seedream_mcp.utils.core.errors import SeedreamValidationError
    from seedream_mcp.utils.images.image_validation import resolve_local_image_candidate

    with pytest.raises(SeedreamValidationError, match="备用数据流") as exc_info:
        resolve_local_image_candidate("photo.jpg:ads.png", [tmp_path.resolve()])

    assert exc_info.value.field == "image"
    assert exc_info.value.value == "photo.jpg:ads.png"


@pytest.mark.skipif(
    sys.platform != "win32", reason="冒号分量拒绝仅 win32 生效，POSIX 冒号是合法文件名字符"
)
async def test_prepare_image_input_rejects_ads_colon_reference_before_read(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """参考图输入呈 ADS 流形态时在候选定位即拒绝，不进入文件读取。

    后缀过白名单、界内 resolve 均可通过对越界校验单独免疫，拒绝须先于读取，
    防止流内容被编码上传；读取入口置爆炸守卫，锁定拒绝时序。
    """
    from typing import IO

    from PIL import Image

    from seedream_mcp.utils.core.errors import SeedreamValidationError
    from seedream_mcp.utils.images import image_input as image_input_module
    from seedream_mcp.utils.images.image_input import prepare_image_input

    host = tmp_path / "photo.jpg"
    Image.new("RGB", (32, 32), color="white").save(host, format="JPEG")

    def _explode_read(path: Path) -> IO[bytes]:
        raise AssertionError("ADS 形态参考图不得进入文件读取")

    monkeypatch.setattr(image_input_module, "open_no_follow_read", _explode_read)

    with pytest.raises(SeedreamValidationError, match="拒绝参考图路径分量含冒号") as exc_info:
        await prepare_image_input("photo.jpg:ads.png")

    assert "备用数据流" in exc_info.value.message
    assert exc_info.value.field == "image"
    assert exc_info.value.value == "photo.jpg:ads.png"


@pytest.mark.skipif(
    sys.platform != "win32", reason="冒号分量拒绝仅 win32 生效，POSIX 冒号是合法文件名字符"
)
async def test_image_preparer_rejects_ads_colon_reference_at_signature(
    workspace_root: Path,
) -> None:
    """ImagePreparer 入口对 ADS 形态输入在缓存签名阶段即拒绝。

    工具链与 webapp 生成链共用本入口，签名与读取都经由候选定位，拒绝先于
    并发槽位获取与文件读取。
    """
    from seedream_mcp.utils.core.errors import SeedreamValidationError
    from seedream_mcp.utils.images.image_prepare import ImagePreparer

    preparer = ImagePreparer(4, 64 * 1024 * 1024, 2)

    with pytest.raises(SeedreamValidationError, match="备用数据流") as exc_info:
        await preparer.prepare_image_input("photo.jpg:ads.png")

    assert exc_info.value.field == "image"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 冒号是合法文件名字符，不经拒绝分支")
async def test_prepare_image_input_posix_reads_colon_named_reference(
    workspace_root: Path, tmp_path: Path
) -> None:
    """POSIX 上含冒号的参考图名是普通文件名，正常读取编码为 data URI。

    与 normalize_path 的 POSIX 镜像用例同口径，锁定冒号拒绝仅 win32 生效。
    """
    from PIL import Image

    from seedream_mcp.utils.images.image_input import prepare_image_input

    colon_named = tmp_path / "photo.jpg:ads.png"
    Image.new("RGB", (32, 32), color="white").save(colon_named, format="PNG")

    result = await prepare_image_input("photo.jpg:ads.png")

    assert result.startswith("data:image/")


async def test_prepare_image_input_rejects_non_http_scheme_with_form_diagnosis(
    workspace_root: Path,
) -> None:
    """file:// 等其余 scheme 形态按输入形态拒绝，诊断不落入 ADS 冒号分支。

    classify 仅特判 http(s) 与 data，file:///C:/x.png 落入本地分支，win32 下
    会先命中冒号分量拒绝而误报为 NTFS 备用数据流。
    """
    from seedream_mcp.utils.core.errors import SeedreamValidationError
    from seedream_mcp.utils.images.image_input import prepare_image_input

    with pytest.raises(SeedreamValidationError, match="仅支持图像 URL") as exc_info:
        await prepare_image_input("file:///C:/users/me/ref.png")

    assert exc_info.value.field == "image"
