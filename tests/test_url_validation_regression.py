"""validate_image_url 回归测试，重点守护 H1：校验异常不得被二次包装。

该回归源于 _validate_url 缺少 ``except SeedreamValidationError: raise`` 前置分支，
导致自身抛出的"无效的URL格式"被 ``except Exception`` 包装为"URL验证失败: 无效的URL格式"。
"""

import pytest

from seedream_mcp.utils.errors import SeedreamValidationError
from seedream_mcp.utils.validation import validate_image_url


def test_validate_image_url_rejects_missing_host_without_message_rewrap() -> None:
    """缺少 host 的 URL 必须直接报"无效的URL格式"，不得被二次包装。"""
    with pytest.raises(SeedreamValidationError, match="^无效的URL格式$") as exc_info:
        validate_image_url("http://")
    assert exc_info.value.message == "无效的URL格式"


def test_validate_image_url_rejects_empty_input() -> None:
    with pytest.raises(SeedreamValidationError, match="不能为空"):
        validate_image_url("")
    with pytest.raises(SeedreamValidationError, match="不能为空"):
        validate_image_url("   ")


def test_validate_image_url_accepts_valid_http_url() -> None:
    url = "https://example.com/path/image.png"
    assert validate_image_url(url) == url
