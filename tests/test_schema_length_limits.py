"""工具输入 schema 字符串长度边界测试。

pydantic 在 core schema 层强制 max_length 约束，超长值在字段校验器运行前即被拒绝。
覆盖 prompt（100000）/save_path（1024）/custom_name（255）的接受与超长拒绝边界，
锁定 inputSchema 长度上限不被回归。统一使用 model_validate 构造输入。
"""

import pytest
from pydantic import ValidationError

from seedream_mcp.tools.core.schemas import TextToImageInput


def test_prompt_accepts_max_length_boundary() -> None:
    """prompt 长度恰为 100000 应被接受。"""
    model = TextToImageInput.model_validate({"prompt": "a" * 100000})

    assert len(model.prompt) == 100000


def test_prompt_rejects_exceeding_max_length() -> None:
    """prompt 长度 100001 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        TextToImageInput.model_validate({"prompt": "a" * 100001})


def test_save_path_accepts_max_length_boundary() -> None:
    """save_path 长度恰为 1024 应被接受。"""
    model = TextToImageInput.model_validate({"prompt": "x", "save_path": "a" * 1024})

    assert len(model.save_path) == 1024


def test_save_path_rejects_exceeding_max_length() -> None:
    """save_path 长度 1025 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        TextToImageInput.model_validate({"prompt": "x", "save_path": "a" * 1025})


def test_custom_name_accepts_max_length_boundary() -> None:
    """custom_name 长度恰为 255 应被接受。"""
    model = TextToImageInput.model_validate({"prompt": "x", "custom_name": "a" * 255})

    assert len(model.custom_name) == 255


def test_custom_name_rejects_exceeding_max_length() -> None:
    """custom_name 长度 256 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        TextToImageInput.model_validate({"prompt": "x", "custom_name": "a" * 256})
