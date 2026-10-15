"""SeedreamClient 请求日志脱敏与 image 字段摘要的单元测试。

覆盖 _summarize_image_field 的 URL / data URI / 本地路径 / list 截断 / 非字符串摘要，
与 _sanitize_request_for_logging 的 prompt 脱敏、image 替换、其余字段引用不变、
不 mutate 原始 dict。
"""

import pytest

from seedream_mcp.client import SeedreamClient
from seedream_mcp.config import SeedreamConfig


@pytest.fixture
def client() -> SeedreamClient:
    """构造未建连的 SeedreamClient，仅用于同步脱敏方法测试。"""
    config = SeedreamConfig(api_key="test_key")
    return SeedreamClient(config)


# ==================== _summarize_image_field ====================


def test_summarize_https_url(client: SeedreamClient) -> None:
    """https URL 摘要为 <image_url>。"""
    assert client._summarize_image_field("https://example.com/x.png") == "<image_url>"


def test_summarize_http_url(client: SeedreamClient) -> None:
    """http URL 同样摘要为 <image_url>。"""
    assert client._summarize_image_field("http://example.com/x.png") == "<image_url>"


def test_summarize_data_uri(client: SeedreamClient) -> None:
    """data URI 摘要为带字符数的占位标记。"""
    uri = "data:image/png;base64,iVBORw0KGgo="
    result = client._summarize_image_field(uri)
    assert result == f"<data_uri:{len(uri)} chars>"


def test_summarize_data_uri_case_insensitive(client: SeedreamClient) -> None:
    """大写 DATA:IMAGE/ 仍被识别为 data URI。"""
    uri = "DATA:IMAGE/JPEG;base64,abc"
    result = client._summarize_image_field(uri)
    assert result.startswith("<data_uri:")


def test_summarize_local_path(client: SeedreamClient) -> None:
    """POSIX 绝对路径摘要为 <local_image_path>。"""
    assert client._summarize_image_field("/tmp/images/x.png") == "<local_image_path>"


def test_summarize_local_path_windows(client: SeedreamClient) -> None:
    """Windows 绝对路径同样摘要为 <local_image_path>。"""
    assert client._summarize_image_field("C:\\Users\\test\\x.png") == "<local_image_path>"


def test_summarize_relative_path(client: SeedreamClient) -> None:
    """相对路径同样摘要为 <local_image_path>。"""
    assert client._summarize_image_field("images/x.png") == "<local_image_path>"


def test_summarize_non_string_int(client: SeedreamClient) -> None:
    """非字符串 int 摘要为类型占位标记。"""
    assert client._summarize_image_field(123) == "<int>"


def test_summarize_non_string_none(client: SeedreamClient) -> None:
    """None 摘要为类型占位标记。"""
    assert client._summarize_image_field(None) == "<NoneType>"


def test_summarize_list_with_truncation(client: SeedreamClient) -> None:
    """list 超过 3 项时仅取前 3 采样并标记 truncated=True。"""
    images = [
        "http://x/1.png",
        "http://x/2.png",
        "http://x/3.png",
        "http://x/4.png",
        "http://x/5.png",
    ]
    result = client._summarize_image_field(images)
    assert result["type"] == "list"
    assert result["count"] == 5
    assert len(result["samples"]) == 3
    assert result["truncated"] is True
    # 每个采样项本身也经过摘要
    assert result["samples"][0] == "<image_url>"


def test_summarize_list_without_truncation(client: SeedreamClient) -> None:
    """list 不超过 3 项时 truncated=False。"""
    images = ["http://x/1.png", "http://x/2.png"]
    result = client._summarize_image_field(images)
    assert result["type"] == "list"
    assert result["count"] == 2
    assert result["truncated"] is False


def test_summarize_empty_list(client: SeedreamClient) -> None:
    """空列表摘要 count=0 且不截断。"""
    result = client._summarize_image_field([])
    assert result["type"] == "list"
    assert result["count"] == 0
    assert result["samples"] == []
    assert result["truncated"] is False


def test_summarize_list_samples_recursively(client: SeedreamClient) -> None:
    """list 采样项包含混合类型时各自走对应摘要分支。"""
    images = ["https://x/1.png", "data:image/png;base64,abc", "/local/x.png"]
    result = client._summarize_image_field(images)
    assert result["samples"][0] == "<image_url>"
    assert result["samples"][1].startswith("<data_uri:")
    assert result["samples"][2] == "<local_image_path>"


# ==================== _sanitize_request_for_logging ====================


def test_sanitize_redacts_prompt(client: SeedreamClient) -> None:
    """prompt 替换为长度占位标记，其余字段保留。"""
    safe = client._sanitize_request_for_logging({"prompt": "secret prompt", "size": "2K"})
    assert safe["prompt"] == "<redacted:13 chars>"
    assert safe["size"] == "2K"


def test_sanitize_redacts_image_url(client: SeedreamClient) -> None:
    """image 为 URL 时替换为 <image_url>。"""
    safe = client._sanitize_request_for_logging({"image": "https://x/y.png", "prompt": "p"})
    assert safe["image"] == "<image_url>"


def test_sanitize_redacts_image_data_uri(client: SeedreamClient) -> None:
    """image 为 data URI 时替换为字符数占位标记。"""
    uri = "data:image/png;base64,iVBORw0KGgo="
    safe = client._sanitize_request_for_logging({"image": uri})
    assert safe["image"] == f"<data_uri:{len(uri)} chars>"


def test_sanitize_redacts_image_list(client: SeedreamClient) -> None:
    """image 为 list 时替换为采样摘要 dict。"""
    safe = client._sanitize_request_for_logging({"image": ["https://x/1.png", "https://x/2.png"]})
    assert isinstance(safe["image"], dict)
    assert safe["image"]["count"] == 2


def test_sanitize_preserves_other_fields(client: SeedreamClient) -> None:
    """prompt/image 以外的字段原样引用。"""
    data = {"model": "m", "watermark": False, "size": "1K"}
    safe = client._sanitize_request_for_logging(data)
    assert safe["model"] == "m"
    assert safe["watermark"] is False
    assert safe["size"] == "1K"


def test_sanitize_does_not_mutate_original(client: SeedreamClient) -> None:
    """脱敏返回浅拷贝，不修改原始 dict。"""
    data = {"prompt": "secret", "image": "https://x/y.png"}
    _ = client._sanitize_request_for_logging(data)
    assert data["prompt"] == "secret"
    assert data["image"] == "https://x/y.png"


def test_sanitize_without_prompt_and_image(client: SeedreamClient) -> None:
    """无 prompt/image 时不注入对应 key。"""
    safe = client._sanitize_request_for_logging({"size": "2K"})
    assert "prompt" not in safe
    assert "image" not in safe
    assert safe["size"] == "2K"


def test_sanitize_prompt_non_string(client: SeedreamClient) -> None:
    """prompt 非 str 时长度记为 0。"""
    safe = client._sanitize_request_for_logging({"prompt": 123})
    assert safe["prompt"] == "<redacted:0 chars>"


def test_sanitize_prompt_empty_string(client: SeedreamClient) -> None:
    """空 prompt 记为 0 字符。"""
    safe = client._sanitize_request_for_logging({"prompt": ""})
    assert safe["prompt"] == "<redacted:0 chars>"
