"""工具输入 schema 字符串与取值边界测试。

pydantic 在 core schema 层强制 max_length 约束，超长值在字段校验器运行前即被拒绝。
覆盖 prompt 100000、save_path 1024、custom_name 255、browse directory 1024、
browse format_filter 单项 16 与条目数 32 的接受与超长拒绝边界、browse
数值控件区间、枚举字段归一与拒绝、可选字符串字段的空白拒绝与 None 放行、
image 的空白拒绝、多图 image=None 的类型拒绝，以及多图与组图
空白条目报错文案一致、空白包裹参考图 strip 归一后按 url/data_uri 分类，
锁定 inputSchema 约束与跨字段约束描述不被回归。统一使用 model_validate 构造输入。
"""

from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

from seedream_mcp.tools.core import schemas as schemas_module
from seedream_mcp.tools.core.schemas import (
    BackgroundMode,
    BrowseImagesInput,
    GenerationTool,
    GenerationToolType,
    ImageToImageInput,
    MultiImageFusionInput,
    OutputFormat,
    PARALLELISM_DESCRIPTION,
    ResponseFormat,
    SequentialGenerationInput,
    STREAM_DESCRIPTION,
    TextToImageInput,
    _SequentialImageInput,
)
from seedream_mcp.utils.images.image_ref import classify_image_reference


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

    assert len(cast(str, model.save_path)) == 1024


def test_save_path_rejects_exceeding_max_length() -> None:
    """save_path 长度 1025 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        TextToImageInput.model_validate({"prompt": "x", "save_path": "a" * 1025})


def test_custom_name_accepts_max_length_boundary() -> None:
    """custom_name 长度恰为 255 应被接受。"""
    model = TextToImageInput.model_validate({"prompt": "x", "custom_name": "a" * 255})

    assert len(cast(str, model.custom_name)) == 255


def test_custom_name_rejects_exceeding_max_length() -> None:
    """custom_name 长度 256 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        TextToImageInput.model_validate({"prompt": "x", "custom_name": "a" * 256})


def test_blank_save_path_and_custom_name_rejected_none_allowed() -> None:
    """纯空白 save_path 与 custom_name 在模型校验器层被拒绝，None 走早退放行。

    webapp 直连构造不经平铺签名的 pattern 镜像，该层拒绝只由校验器保证。
    """
    with pytest.raises(ValidationError, match="该字段不能为空字符串"):
        TextToImageInput.model_validate({"prompt": "x", "save_path": "   "})
    with pytest.raises(ValidationError, match="该字段不能为空字符串"):
        TextToImageInput.model_validate({"prompt": "x", "custom_name": "   "})
    model = TextToImageInput.model_validate({"prompt": "x", "save_path": None, "custom_name": None})
    assert model.save_path is None
    assert model.custom_name is None


def test_browse_directory_accepts_max_length_boundary() -> None:
    """browse directory 长度恰为 1024 应被接受。"""
    model = BrowseImagesInput.model_validate({"directory": "a" * 1024})

    assert len(cast(str, model.directory)) == 1024


def test_browse_directory_rejects_exceeding_max_length() -> None:
    """browse directory 长度 1025 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        BrowseImagesInput.model_validate({"directory": "a" * 1025})


def test_blank_directory_rejected_none_allowed() -> None:
    """纯空白 directory 在模型校验器层被拒绝，None 走早退放行。"""
    with pytest.raises(ValidationError, match="目录路径不能为空字符串"):
        BrowseImagesInput.model_validate({"directory": "   "})
    assert BrowseImagesInput.model_validate({"directory": None}).directory is None


def test_browse_format_filter_accepts_item_max_length_boundary() -> None:
    """format_filter 单项长度恰为 16 应被接受。"""
    model = BrowseImagesInput.model_validate({"format_filter": ["a" * 16]})

    assert model.format_filter == ["." + "a" * 16]


def test_browse_format_filter_rejects_exceeding_item_max_length() -> None:
    """format_filter 单项长度 17 应被 pydantic 拒绝。"""
    with pytest.raises(ValidationError):
        BrowseImagesInput.model_validate({"format_filter": ["a" * 17]})


def test_browse_format_filter_accepts_item_count_boundary() -> None:
    """format_filter 条目数恰为 32 应被接受。"""
    model = BrowseImagesInput.model_validate({"format_filter": [f".e{i}" for i in range(32)]})

    assert len(cast("list[str]", model.format_filter)) == 32


def test_browse_format_filter_rejects_exceeding_item_count() -> None:
    """format_filter 条目数 33 应被 pydantic 拒绝，无界列表造成遍历与回显放大。"""
    with pytest.raises(ValidationError):
        BrowseImagesInput.model_validate({"format_filter": [f".e{i}" for i in range(33)]})


def test_single_image_rejects_blank_string() -> None:
    """单图输入的空字符串 image 在 schema 级被拒绝。"""
    with pytest.raises(ValidationError, match="image 不能为空字符串"):
        ImageToImageInput.model_validate({"prompt": "x", "image": ""})


def test_single_image_rejects_whitespace_only_string() -> None:
    """单图输入仅含空白的 image 同样在 schema 级被拒绝。"""
    with pytest.raises(ValidationError, match="image 不能为空字符串"):
        ImageToImageInput.model_validate({"prompt": "x", "image": "   "})


def test_single_image_rejects_oversized_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """单图输入超过防御上限的 image 在 schema 级被拒绝，不付全量拷贝成本。"""
    monkeypatch.setattr(schemas_module, "MAX_IMAGE_INPUT_CHARS", 100)
    with pytest.raises(ValidationError, match="超过防御上限"):
        ImageToImageInput.model_validate({"prompt": "x", "image": "a" * 101})


def test_multi_image_rejects_oversized_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """多图输入逐项超过防御上限时在 schema 级被拒绝。"""
    monkeypatch.setattr(schemas_module, "MAX_IMAGE_INPUT_CHARS", 100)
    with pytest.raises(ValidationError, match="超过防御上限"):
        MultiImageFusionInput.model_validate(
            {"prompt": "x", "image": ["https://e/a.png", "a" * 101]}
        )


def test_sequential_image_rejects_oversized_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """组图参考图逐项超过防御上限时在 schema 级被拒绝，与其余带图工具同界。"""
    monkeypatch.setattr(schemas_module, "MAX_IMAGE_INPUT_CHARS", 100)
    with pytest.raises(ValidationError, match="超过防御上限"):
        SequentialGenerationInput.model_validate({"prompt": "x", "image": "a" * 101})


def test_sequential_image_mixin_rejects_oversized_single_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单独使用的组图输入基类对单字符串形态按整串校验防御上限，不因非列表形态绕过。"""
    monkeypatch.setattr(schemas_module, "MAX_IMAGE_INPUT_CHARS", 100)
    with pytest.raises(ValidationError, match="超过防御上限"):
        _SequentialImageInput.model_validate({"image": "a" * 101})


def test_sequential_image_rejects_blank_item() -> None:
    """组图参考图的空白条目在 schema 级被拒绝。"""
    with pytest.raises(ValidationError, match="image 不能为空字符串"):
        SequentialGenerationInput.model_validate({"prompt": "x", "image": ["   "]})


@pytest.mark.parametrize("model_cls", [MultiImageFusionInput, SequentialGenerationInput])
def test_list_image_tools_reject_blank_item_with_shared_message(
    model_cls: type[BaseModel],
) -> None:
    """多图与组图的空白条目由共享校验器拒绝且文案一致，规则调整须两工具同口径。"""
    with pytest.raises(ValidationError, match="image 不能为空字符串"):
        model_cls.model_validate({"prompt": "x", "image": ["https://e/a.png", "   "]})


@pytest.mark.parametrize(
    ("model_cls", "raw", "expected"),
    [
        (
            ImageToImageInput,
            "  https://example.com/a.png  ",
            "https://example.com/a.png",
        ),
        (
            MultiImageFusionInput,
            ["  https://example.com/a.png  ", " data:image/png;base64,AAAA "],
            ["https://example.com/a.png", "data:image/png;base64,AAAA"],
        ),
        (
            SequentialGenerationInput,
            ["  https://example.com/a.png  "],
            ["https://example.com/a.png"],
        ),
        (
            SequentialGenerationInput,
            "  https://example.com/a.png  ",
            ["https://example.com/a.png"],
        ),
    ],
)
def test_generation_image_inputs_strip_wrapped_references(
    model_cls: (
        type[ImageToImageInput] | type[MultiImageFusionInput] | type[SequentialGenerationInput]
    ),
    raw: str | list[str],
    expected: str | list[str],
) -> None:
    """空白包裹的参考图经共享校验器 strip 归一，分类命中 url 与 data_uri 而非本地路径。"""
    model = model_cls.model_validate({"prompt": "x", "image": raw})

    assert model.image == expected
    items = expected if isinstance(expected, list) else [expected]
    assert all(
        classify_image_reference(item) == ("data_uri" if item.startswith("data:") else "url")
        for item in items
    )


def test_sequential_image_mixin_strips_wrapped_items() -> None:
    """共享校验器单点完成 strip 归一，不依赖具体输入模型的 str_strip_whitespace 配置。"""
    model = _SequentialImageInput.model_validate({"image": ["  https://example.com/a.png  "]})

    assert model.image == ["https://example.com/a.png"]
    assert classify_image_reference(model.image[0]) == "url"


def test_multi_image_rejects_none_input() -> None:
    """多图 image 显式传 None 被类型拒绝而非 None 早退放行。"""
    with pytest.raises(ValidationError):
        MultiImageFusionInput.model_validate({"prompt": "x", "image": None})


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("max_depth", [1, 10], [0, 11]),
        ("limit", [1, 200], [0, 201]),
    ],
)
def test_browse_numeric_controls_enforce_declared_bounds(
    field: str, accepted: list[int], rejected: list[int]
) -> None:
    """max_depth 与 limit 按声明区间执行：边界值接受，界外值拒绝。"""
    for value in accepted:
        assert getattr(BrowseImagesInput.model_validate({field: value}), field) == value
    for value in rejected:
        with pytest.raises(ValidationError):
            BrowseImagesInput.model_validate({field: value})


def test_browse_format_filter_rejects_blank_item() -> None:
    """空白后缀条目以校验错误拒绝，不静默归一为点号。"""
    with pytest.raises(ValidationError, match="不能为空白"):
        BrowseImagesInput.model_validate({"format_filter": ["   "]})


def test_enum_fields_normalize_case_and_whitespace() -> None:
    """枚举字段经 strip 与 lower 归一后按值命中，对齐提示词优化选项的宽容姿态。"""
    model = TextToImageInput.model_validate(
        {
            "prompt": "x",
            "response_format": " URL ",
            "output_format": "Png",
            "tools": [{"type": "Web_Search"}],
        }
    )

    assert model.response_format is ResponseFormat.URL
    assert model.output_format is OutputFormat.PNG
    assert model.tools == [GenerationTool(type=GenerationToolType.WEB_SEARCH)]

    layered = ImageToImageInput.model_validate(
        {"prompt": "x", "image": "https://e/a.png", "background": " TRANSPARENT "}
    )
    assert layered.background is BackgroundMode.TRANSPARENT


def test_enum_fields_reject_unknown_values_with_member_list() -> None:
    """归一后仍未知的枚举取值被拒绝，错误消息携带合法取值清单。"""
    with pytest.raises(ValidationError, match="response_format 仅支持"):
        TextToImageInput.model_validate({"prompt": "x", "response_format": "mailto"})
    with pytest.raises(ValidationError, match="output_format 仅支持"):
        TextToImageInput.model_validate({"prompt": "x", "output_format": "gif"})
    with pytest.raises(ValidationError, match="background 仅支持"):
        ImageToImageInput.model_validate(
            {"prompt": "x", "image": "https://e/a.png", "background": "alpha"}
        )
    with pytest.raises(ValidationError, match="type 仅支持"):
        TextToImageInput.model_validate({"prompt": "x", "tools": [{"type": "code_run"}]})


def test_parallel_descriptions_declare_cross_field_constraints() -> None:
    """并行参数描述须声明运行时强制的跨字段约束，模型不应依赖失败调用习得。"""
    assert "不得超过 request_count" in PARALLELISM_DESCRIPTION
    assert "request_count 必须为 1" in STREAM_DESCRIPTION
