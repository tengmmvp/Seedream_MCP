"""像素上限常量一致性守护测试。"""

from seedream_mcp.utils.images.image_validation import MAX_IMAGE_PIXELS
from seedream_mcp.utils.io.io_save import _DOWNLOAD_MAX_PIXELS


def test_download_pixel_limit_matches_input_validation() -> None:
    """下载落盘侧像素上限必须与输入参考图侧上限相等。

    输入侧 MAX_IMAGE_PIXELS 拒绝超大参考图，落盘侧 _DOWNLOAD_MAX_PIXELS 拦截
    恶意上游返回的伪造尺寸头内容。两处口径一旦分叉，较松一侧会静默产生绕过面，
    本断言使分叉在测试期即暴露。
    """
    assert _DOWNLOAD_MAX_PIXELS == MAX_IMAGE_PIXELS
