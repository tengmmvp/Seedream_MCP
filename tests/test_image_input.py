"""image_input 预处理测试。

覆盖指向工作区外符号链接的越界拒绝（resolve 跟随后路径越界被拒）与本地图片
单次读取后的内存维度校验路径。
"""

import base64
import io
import os
from pathlib import Path

import pytest
from PIL import Image

from seedream_mcp.utils.core.errors import SeedreamAPIError, SeedreamValidationError
from seedream_mcp.utils.images.image_input import prepare_image_input
from seedream_mcp.utils.io.io_path import _WORKSPACE_ROOTS_VAR


async def test_prepare_image_input_rejects_symlink_escape(
    workspace_root: Path, tmp_path: Path
) -> None:
    """指向工作区外的符号链接须被越界校验拒绝，防止经符号链接逃逸工作区边界。

    normalize_path 的 resolve 会跟随符号链接，故链接目标须位于工作区之外才能触发越界
    拒绝；若目标位于工作区内，resolve 后得到常规文件路径，O_NOFOLLOW 打开该常规文件
    不抛错，测试将沦为空芯。越界属参数校验语义，抛 SeedreamValidationError；以会话
    Roots 声明边界，消息附调用方授权的根列表供自纠。
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

    越界属参数问题而非 API 调用失败，异常类型须为 SeedreamValidationError，
    错误码归约为 validation_error 而非 api_error。以会话 Roots 声明边界，
    根列表为调用方自授权信息，回显不受回退边界遮蔽约束。
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

    边界经 SEEDREAM_WORKSPACE_ROOT 回退取得时根路径属于服务器本地目录结构，
    消息改述为仅允许服务器配置的工作区目录，与 browse_images 的回退遮蔽
    标准一致。
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
    """界内不存在的路径仍走诊断分支：报文件不存在而非越界，不附允许根列表。"""
    with pytest.raises(SeedreamAPIError) as exc_info:
        await prepare_image_input(str(tmp_path / "missing.png"))
    assert "路径超出允许的工作区目录范围" not in exc_info.value.message
    assert "文件不存在" in exc_info.value.message


async def test_prepare_image_input_reads_local_file(workspace_root: Path, tmp_path: Path) -> None:
    """本地图片读取并编码为 data URI，维度校验在内存单次读取完成。"""
    image_path = tmp_path / "local.png"
    Image.new("RGB", (64, 64), color="black").save(image_path)

    result = await prepare_image_input(str(image_path))
    assert result.startswith("data:image/")


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

    守护 URL 分支接入统一校验，防止 userinfo 拒绝沦为仅 data_uri/本地分支可达的死代码。
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

    RFC 3986 scheme 大小写不敏感；此前大写前缀在 parse_data_uri 处拆分失败，
    报笼统的"格式无效"而非走精确校验分支。官方要求图片格式为小写，返回值经
    归一化为小写标准形态。
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

    与 _validate_file_path 的维度解析分支同口径：损坏内容是调用方输入问题，
    错误码归约为 validation_error，不误导调用方重试或排查上游 API。
    """
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"definitely-not-an-image-0123456789")

    with pytest.raises(SeedreamValidationError, match="图像维度解析失败"):
        await prepare_image_input(str(corrupt))


async def test_prepare_image_input_mime_follows_byte_signature(
    workspace_root: Path, tmp_path: Path
) -> None:
    """data URI 的 MIME 以字节签名为准：PNG 内容存为 .jpg 时标注 image/png。

    扩展名可伪造，与 auto_save 保存路径的 infer_extension_from_bytes 口径一致；
    签名不可识别时才回退扩展名映射。
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
