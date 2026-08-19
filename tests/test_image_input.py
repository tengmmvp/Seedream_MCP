"""image_input 预处理测试：URL 与 Data URI 主干、本地文件读取与工作区边界校验。

越界与诊断消息区分会话 Roots 边界与 SEEDREAM_WORKSPACE_ROOT 回退边界，后者
不回显服务器根路径。
"""

import base64
import io
import os
from pathlib import Path
from typing import IO

import pytest
from PIL import Image

from seedream_mcp.utils.core.errors import (
    SeedreamValidationError,
    resolve_error_profile,
)
from seedream_mcp.utils.images import image_input as image_input_module
from seedream_mcp.utils.images.image_input import prepare_image_input
from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR


async def test_prepare_image_input_rejects_symlink_escape(
    workspace_root: Path, tmp_path: Path
) -> None:
    """指向工作区外的符号链接须被越界校验拒绝，防止经符号链接逃逸工作区边界。

    目标位于工作区内时 resolve 后为常规文件、O_NOFOLLOW 打开不抛错，测试将沦为
    空芯。以会话 Roots 声明边界，消息附调用方授权的根列表供自纠。
    """
    # 目标文件位于工作区（tmp_path）之外；resolve 跟随符号链接后路径越界被拒
    target = tmp_path.parent / "symlink_escape_target.png"
    Image.new("RGB", (32, 32), color="white").save(target)
    link = tmp_path / "link.png"
    try:
        os.symlink(target, link)
    except OSError:
        target.unlink(missing_ok=True)
        pytest.skip("当前环境不支持创建符号链接")

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        with pytest.raises(
            SeedreamValidationError, match="路径超出允许的工作区目录范围"
        ) as exc_info:
            await prepare_image_input(str(link))
        assert "允许的根:" in exc_info.value.message
        assert str(tmp_path.resolve()) in exc_info.value.message
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)
        target.unlink(missing_ok=True)


async def test_prepare_image_input_out_of_bounds_error_carries_roots(
    workspace_root: Path, tmp_path: Path
) -> None:
    """绝对路径落在工作区根之外时抛校验错误，消息携带允许的根列表供纠错。

    会话 Roots 声明的根列表属调用方自授权信息，回显不受回退边界遮蔽约束。
    """
    outside = tmp_path.parent / "outside_workspace_image.png"
    Image.new("RGB", (32, 32), color="white").save(outside)

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        with pytest.raises(
            SeedreamValidationError, match="路径超出允许的工作区目录范围"
        ) as exc_info:
            await prepare_image_input(str(outside))
        assert str(tmp_path.resolve()) in exc_info.value.message
        assert exc_info.value.field == "image"
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)
        outside.unlink(missing_ok=True)


async def test_prepare_image_input_out_of_bounds_masks_fallback_boundary(
    workspace_root: Path, tmp_path: Path
) -> None:
    """回退边界（无会话 Roots）下的越界消息不回显服务器环境根路径。

    与 browse_images 的回退遮蔽口径一致。
    """
    outside = tmp_path.parent / "outside_workspace_masked.png"
    Image.new("RGB", (32, 32), color="white").save(outside)

    try:
        with pytest.raises(
            SeedreamValidationError, match="仅允许服务器配置的工作区目录"
        ) as exc_info:
            await prepare_image_input(str(outside))
        assert "允许的根:" not in exc_info.value.message
        assert str(tmp_path.resolve()) not in exc_info.value.message
    finally:
        outside.unlink(missing_ok=True)


async def test_prepare_image_input_missing_in_bounds_keeps_diagnostics(
    workspace_root: Path, tmp_path: Path
) -> None:
    """会话 Roots 边界下界内不存在的路径仍走诊断分支：报文件不存在而非越界。

    失败原因与相似路径建议回显调用方授权的根下信息，不受回退遮蔽约束。
    """
    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        with pytest.raises(SeedreamValidationError) as exc_info:
            await prepare_image_input("missing.png")
        assert "路径超出允许的工作区目录范围" not in exc_info.value.message
        assert "文件不存在" in exc_info.value.message
        assert exc_info.value.field == "image"
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)


async def test_prepare_image_input_in_bounds_diagnostics_masks_fallback_boundary(
    workspace_root: Path, tmp_path: Path
) -> None:
    """回退边界下界内定位失败的诊断分支不泄露服务器根绝对路径与相似路径建议。

    根下放置名称相近的真实图片，确保遮蔽前建议分支确实可命中，测试不沦为空芯。
    """
    sibling = tmp_path / "missing_sibling.png"
    Image.new("RGB", (32, 32), color="white").save(sibling)

    try:
        with pytest.raises(SeedreamValidationError) as exc_info:
            await prepare_image_input("missing_sib.png")
        assert "路径超出允许的工作区目录范围" not in exc_info.value.message
        assert "建议的相似路径" not in exc_info.value.message
        assert str(tmp_path.resolve()) not in exc_info.value.message
        assert "missing_sib.png" in exc_info.value.message
        assert exc_info.value.field == "image"
    finally:
        sibling.unlink(missing_ok=True)


async def test_prepare_image_input_reads_local_file(workspace_root: Path, tmp_path: Path) -> None:
    """本地图片读取并编码为 data URI，维度校验在内存单次读取完成。"""
    image_path = tmp_path / "local.png"
    Image.new("RGB", (64, 64), color="black").save(image_path)

    result = await prepare_image_input(str(image_path))
    assert result.startswith("data:image/")


async def test_prepare_image_input_read_failure_masks_fallback_boundary(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回退边界下本地文件打开失败的错误消息不泄露服务器侧绝对路径。

    OSError 原文嵌有解析后的绝对路径，遮蔽后仅回显系统错误语义与调用方输入
    原样串。
    """
    locked = tmp_path / "locked.png"
    Image.new("RGB", (32, 32), color="white").save(locked)

    def _deny_open(path: Path) -> IO[bytes]:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(image_input_module, "open_no_follow_read", _deny_open)

    with pytest.raises(SeedreamValidationError) as exc_info:
        await prepare_image_input("locked.png")

    message = exc_info.value.message
    # OSError 原文的 filename 经 repr 渲染，反斜杠以转义形态出现，两种形态都不
    # 得进入面向调用方的消息
    resolved_root = str(tmp_path.resolve())
    escaped_root = repr(resolved_root)[1:-1]
    assert resolved_root not in message
    assert escaped_root not in message
    assert "Permission denied" in message
    assert "locked.png" in message
    assert exc_info.value.field == "image"
    assert exc_info.value.value == "locked.png"


async def test_prepare_image_input_read_failure_profiles_as_validation_error(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地文件读取失败归入 validation_error 档，不给凭据与网络排查建议。

    PermissionError 一类读取失败是本地输入问题；此前归入 api_error 档会给用户
    「请确认 API Key 和网络可用后重试」的误导建议。
    """
    locked = tmp_path / "locked2.png"
    Image.new("RGB", (32, 32), color="white").save(locked)

    def _deny_open(path: Path) -> IO[bytes]:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(image_input_module, "open_no_follow_read", _deny_open)

    with pytest.raises(SeedreamValidationError) as exc_info:
        await prepare_image_input("locked2.png")

    profile = resolve_error_profile(exc_info.value)
    assert profile.error_code == "validation_error"
    assert "API Key" not in profile.user_hint


async def test_prepare_image_input_read_failure_echoes_session_roots_boundary(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话 Roots 边界下打开失败回显异常原文，解析后的绝对路径属调用方授权信息。

    与越界、诊断两条定位失败路径的遮蔽口径一致：仅回退边界遮蔽路径。
    """
    locked = tmp_path / "locked.png"
    Image.new("RGB", (32, 32), color="white").save(locked)

    def _deny_open(path: Path) -> IO[bytes]:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(image_input_module, "open_no_follow_read", _deny_open)

    token = _WORKSPACE_ROOTS_VAR.set((workspace_root.resolve(),))
    try:
        with pytest.raises(SeedreamValidationError) as exc_info:
            await prepare_image_input("locked.png")
        # OSError 原文的 filename 经 repr 渲染，以转义形态回显在消息中
        assert repr(str((tmp_path / "locked.png").resolve())) in exc_info.value.message
    finally:
        _WORKSPACE_ROOTS_VAR.reset(token)


async def test_prepare_image_input_in_bounds_failure_profiles_as_validation_error(
    workspace_root: Path, tmp_path: Path
) -> None:
    """界内定位失败归入 validation_error 档，不给凭据与网络排查建议。

    文件不存在、格式不支持属调用方输入问题；此前归入 api_error 档会给用户
    「请确认 API Key 和网络可用后重试」的误导建议。
    """
    with pytest.raises(SeedreamValidationError) as exc_info:
        await prepare_image_input("missing.png")

    profile = resolve_error_profile(exc_info.value)
    assert profile.error_code == "validation_error"
    assert "API Key" not in profile.user_hint


# ---- URL 与 Data URI 主干路径 ----


async def test_prepare_image_input_returns_https_url_unchanged() -> None:
    """HTTP/HTTPS URL 经主机校验后原样返回，不触网也不改写。"""
    url = "https://example.com/path/img.png"
    assert await prepare_image_input(url) == url


async def test_prepare_image_input_returns_http_url_unchanged() -> None:
    """http 协议同样原样返回。"""
    url = "http://example.com/img.png"
    assert await prepare_image_input(url) == url


async def test_prepare_image_input_validates_and_returns_data_uri() -> None:
    """合法 Data URI 经格式与维度校验后原样返回。"""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color="white").save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    assert await prepare_image_input(data_uri) == data_uri


async def test_prepare_image_input_rejects_url_without_netloc() -> None:
    """以 http:// 开头但缺少主机名的 URL 须在入参校验即拒绝。"""
    with pytest.raises(SeedreamValidationError, match="无效的URL格式"):
        await prepare_image_input("http://")


async def test_prepare_image_input_rejects_url_userinfo_credentials() -> None:
    """携带 userinfo 凭据的参考图 URL 须在主管道即拒绝，凭据不得随请求体送往上游 API。

    守护 URL 分支接入统一校验，防止拒绝仅 data_uri/本地分支可达。
    """
    with pytest.raises(SeedreamValidationError, match="用户名密码"):
        await prepare_image_input("https://AKID:SECRET@mirror.example.com/ref.png")


async def test_prepare_image_input_rejects_url_userinfo_username_only() -> None:
    """仅用户名、无密码的 userinfo 形态同样拒绝。"""
    with pytest.raises(SeedreamValidationError, match="用户名密码"):
        await prepare_image_input("https://user@mirror.example.com/ref.png")


async def test_prepare_image_input_rejects_invalid_data_uri() -> None:
    """非法 base64 负载的 Data URI 须被校验透传拒绝，校验错误原样上抛不被吞掉。"""
    with pytest.raises(SeedreamValidationError, match="Base64 解码失败|Data URI"):
        await prepare_image_input("data:image/png;base64,@@not_base64@@")


async def test_prepare_image_input_accepts_uppercase_data_scheme() -> None:
    """scheme 大小写不敏感：大写 DATA: 前缀的合法 Data URI 正常校验通过。

    此前大写前缀在 parse_data_uri 拆分失败而报笼统的「格式无效」；返回值归一化
    为小写标准形态。
    """
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color="white").save(buffer, format="PNG")
    data_uri = "DATA:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    normalized = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    assert await prepare_image_input(data_uri) == normalized


async def test_prepare_image_input_corrupt_content_raises_validation_error(
    workspace_root: Path, tmp_path: Path
) -> None:
    """扩展名合法但内容损坏属参数校验语义，抛 SeedreamValidationError 而非 API 错误。

    用户可见消息为固定文案，PIL 异常原文中的 BytesIO 对象地址不进入消息。
    """
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"definitely-not-an-image-0123456789")

    with pytest.raises(SeedreamValidationError) as exc_info:
        await prepare_image_input(str(corrupt))

    assert "无法识别的图像内容" in exc_info.value.message
    assert "_io.BytesIO" not in exc_info.value.message


async def test_prepare_image_input_corrupt_data_uri_message_masks_bytesio_address() -> None:
    """Data URI 负载损坏时用户可见消息为固定文案，不含 BytesIO 对象地址。"""
    payload = base64.b64encode(b"definitely-not-an-image-0123456789").decode("ascii")

    with pytest.raises(SeedreamValidationError) as exc_info:
        await prepare_image_input(f"data:image/png;base64,{payload}")

    assert "无法识别的图像内容" in exc_info.value.message
    assert "_io.BytesIO" not in exc_info.value.message


async def test_prepare_image_input_rejects_file_replaced_with_oversized_content(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """定位 stat 与读取之间文件被替换为超大内容时，读取量复核以文件过大拒绝。

    读取阶段限制读取量为上限加一并复核，防 TOCTOU 窗口内的巨型文件撑爆内存。
    """

    class _OversizedFile:
        def read(self, limit: int) -> bytes:
            return b"\x00" * limit

        def __enter__(self) -> "_OversizedFile":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    oversized = tmp_path / "oversized.png"
    Image.new("RGB", (16, 16), color="white").save(oversized)
    monkeypatch.setattr(image_input_module, "open_no_follow_read", lambda _path: _OversizedFile())

    with pytest.raises(SeedreamValidationError, match="文件过大") as exc_info:
        await prepare_image_input("oversized.png")

    assert exc_info.value.field == "image"
    assert exc_info.value.value == "oversized.png"


async def test_prepare_image_input_mime_follows_byte_signature(
    workspace_root: Path, tmp_path: Path
) -> None:
    """data URI 的 MIME 以字节签名为准：PNG 内容存为 .jpg 时标注 image/png。

    扩展名可伪造，签名不可识别时才回退扩展名映射。
    """
    mismatched = tmp_path / "actually-png.jpg"
    Image.new("RGB", (32, 32), color="white").save(mismatched, format="PNG")

    result = await prepare_image_input(str(mismatched))

    assert result.startswith("data:image/png;base64,")


def test_validate_image_input_keeps_home_prefix_literal(workspace_root: Path) -> None:
    """~ 前缀不做用户目录展开，按字面相对路径参与解析，与候选定位口径一致。

    展开会使实际读取目标脱离调用方已按字面值完成的工作区边界判定。
    """
    from seedream_mcp.utils.images.image_validation import _resolve_local_image_path

    resolved = _resolve_local_image_path("~/x.png")

    assert "~" in str(resolved)
