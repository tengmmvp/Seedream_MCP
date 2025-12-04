import pytest
import base64

from seedream_mcp.utils.validation import validate_image_url, SeedreamValidationError


def _data_uri_for_png_1x1():
    png_bytes = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAObV6c8AAAAASUVORK5CYII="
    )
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("utf-8")


def test_data_uri_lowercase_prefix_required():
    uri = _data_uri_for_png_1x1()
    with pytest.raises(SeedreamValidationError):
        validate_image_url(uri)

    bad = uri.replace("data:image/png;base64,", "data:image/PNG;base64,")
    with pytest.raises(SeedreamValidationError):
        validate_image_url(bad)


def test_data_uri_format_extension_supports_webp_bmp_tiff_gif():
    payload = base64.b64encode(b"test").decode("utf-8")
    for fmt in ["webp", "bmp", "tiff", "gif"]:
        uri = f"data:image/{fmt};base64,{payload}"
        with pytest.raises(SeedreamValidationError):
            validate_image_url(uri)

