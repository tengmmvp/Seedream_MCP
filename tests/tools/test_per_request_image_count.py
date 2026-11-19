"""单请求产图数元数据与参考图场景声明的签名形态、能力门控与最坏投影守护。

per_request_image_count 在 BaseGenerationInput 单点实现，产图数值与流水线真实
形态的逐值对账由 tests/webapp/test_webapp_wave_divisor_parity.py 的 parity 用例
承担；本文件锁定输入模型注册表与生成工具、option 与 image mixin 同场景的一一
对应、签名形态（核心投影键全为必传 keyword-only，漏接调用点以 TypeError 响亮失败
而非静默按缺省档低估）与门控对称性（组图与拆分投影均自门控，调用点免做外层
能力判断）。参考图场景经 reference_images 访问器在输入模型侧声明，注册表内
漏声明即红灯；最坏投影 worst_case_projection 须覆盖 per_request_image_count
的全投影域，新场景漏接入派发时聚合预算欠计即红灯。
"""

from __future__ import annotations

import inspect

import pytest

from seedream_mcp.definitions.conventions import (
    LAYER_DECOMPOSITION_MAX_IMAGES,
    LAYER_DECOMPOSITION_MIN_IMAGES,
    MAX_IMAGES_MIN,
    MAX_SEQUENTIAL_TOTAL_IMAGES,
)
from seedream_mcp.tools.schemas import (
    _LayerOptionsInput,
    _MultiImageInput,
    _SequentialImageInput,
    _SequentialOptionsInput,
    _SingleImageInput,
    BaseGenerationInput,
    GENERATION_INPUT_MODELS,
    LayerDecomposeInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.model.capabilities import (
    MODEL_ALIASES,
    SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES,
    SEEDREAM_LEGACY_LAYER_DECOMPOSITION_MAX_IMAGES,
)

_REQUIRED_PROJECTION_KEYS = ("max_images", "image_count", "model_id")
# 缺省时取官方默认。
_OPTIONAL_PROJECTION_KEYS = ("layer_max_images",)


def test_generation_input_models_registry_matches_five_tools() -> None:
    """生成输入模型注册表与五个生成工具的输入类一一对应，新增生成工具漏登记即失败。"""
    assert {cls.__name__ for cls in GENERATION_INPUT_MODELS} == {
        "TextToImageInput",
        "ImageToImageInput",
        "MultiImageFusionInput",
        "LayerDecomposeInput",
        "SequentialGenerationInput",
    }
    # 场景由 mixin 组合定义：option 与 image mixin 均与具体工具一一对应，新工具
    # 混入即须在此对账，派发随组合自动生效。
    assert {
        cls.__name__ for cls in GENERATION_INPUT_MODELS if issubclass(cls, _LayerOptionsInput)
    } == {"LayerDecomposeInput"}
    assert {
        cls.__name__ for cls in GENERATION_INPUT_MODELS if issubclass(cls, _SequentialOptionsInput)
    } == {"SequentialGenerationInput"}
    assert {
        cls.__name__ for cls in GENERATION_INPUT_MODELS if issubclass(cls, _SingleImageInput)
    } == {"ImageToImageInput", "LayerDecomposeInput"}
    assert {
        cls.__name__ for cls in GENERATION_INPUT_MODELS if issubclass(cls, _MultiImageInput)
    } == {"MultiImageFusionInput"}
    assert {
        cls.__name__ for cls in GENERATION_INPUT_MODELS if issubclass(cls, _SequentialImageInput)
    } == {"SequentialGenerationInput"}


def test_reference_images_declared_for_every_registered_tool() -> None:
    """注册表内工具均声明 reference_images 场景访问器，新图片形态漏声明即红灯。"""
    for model in GENERATION_INPUT_MODELS:
        assert callable(getattr(model, "reference_images", None)), (
            f"{model.__name__} 未声明 reference_images 场景访问器，"
            "context 构建取参考图时将 AttributeError"
        )


def test_per_request_image_count_signature_shape_locked() -> None:
    """投影键集与必传形态锁定：键集漂移或核心键带默认值即红。"""
    parameters = inspect.signature(BaseGenerationInput.per_request_image_count).parameters
    assert set(parameters) == set(_REQUIRED_PROJECTION_KEYS) | set(_OPTIONAL_PROJECTION_KEYS)
    for name, parameter in parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, f"{name} 须为 keyword-only"
    for name in _REQUIRED_PROJECTION_KEYS:
        assert (
            parameters[name].default is inspect.Parameter.empty
        ), f"{name} 带默认值使漏接调用点静默按缺省档低估预算，须显式传参"


def test_per_request_image_count_capability_gates_symmetric() -> None:
    """组图与拆分投影均自门控：不支持家族产 0，webapp 调用点免做外层能力判断。"""
    assert (
        SequentialGenerationInput.per_request_image_count(
            max_images=None, image_count=0, model_id="doubao-seedream-5-5-261028"
        )
        == 0
    ), "5.5 家族不支持组图，组图投影应自门控归零"
    assert (
        LayerDecomposeInput.per_request_image_count(
            max_images=None, image_count=0, model_id="doubao-seedream-5-0-260128"
        )
        == 0
    ), "5.0 家族不支持拆分，拆分投影应自门控归零"
    assert (
        SequentialGenerationInput.per_request_image_count(
            max_images=None, image_count=0, model_id="doubao-seedream-5-0-260128"
        )
        == 15
    ), "支持组图的家族缺省投影应为无参考图推导值"


def test_worst_case_projection_values_locked() -> None:
    """各场景最坏投影数值锁定：拆分取值域上界或能力表满额，组图取总数上界，缺省恒一图。"""
    assert (
        LayerDecomposeInput.worst_case_projection(model_id="doubao-seedream-5-5-261028")
        == LAYER_DECOMPOSITION_MAX_IMAGES
    )
    assert (
        LayerDecomposeInput.worst_case_projection(model_id="doubao-seedream-5-0-pro-260628")
        == SEEDREAM_LEGACY_LAYER_DECOMPOSITION_MAX_IMAGES
    )
    assert LayerDecomposeInput.worst_case_projection(model_id="doubao-seedream-5-0-260128") == 0
    assert (
        SequentialGenerationInput.worst_case_projection(model_id="doubao-seedream-5-0-260128")
        == MAX_SEQUENTIAL_TOTAL_IMAGES
    )
    assert (
        SequentialGenerationInput.worst_case_projection(model_id="doubao-seedream-5-5-261028") == 0
    )
    assert TextToImageInput.worst_case_projection(model_id="doubao-seedream-5-5-261028") == 1


# 拆分投影域：layer_max_images 的合法值全集（None 为官方默认档）。
_LAYER_MAX_IMAGES_DOMAIN: tuple[int | None, ...] = (
    None,
    *range(LAYER_DECOMPOSITION_MIN_IMAGES, LAYER_DECOMPOSITION_MAX_IMAGES + 1),
)

# 组图投影域：max_images 合法值全集乘参考图数量上限（None 为按参考图推导档）。
_SEQUENTIAL_MAX_IMAGES_DOMAIN: tuple[int | None, ...] = (
    None,
    *range(MAX_IMAGES_MIN, MAX_SEQUENTIAL_TOTAL_IMAGES + 1),
)


@pytest.mark.parametrize("model_id", tuple(MODEL_ALIASES.values()))
def test_worst_case_projection_dominates_projection_domain(model_id: str) -> None:
    """最坏投影覆盖 per_request_image_count 全投影域：新场景漏接入派发时在此红灯。

    未接入派发的新场景回落缺省 1，满档等待预算欠计下载尾巴而服务器仍在工作。
    """
    for model in GENERATION_INPUT_MODELS:
        worst = model.worst_case_projection(model_id=model_id)
        for layer_max_images in _LAYER_MAX_IMAGES_DOMAIN:
            assert worst >= model.per_request_image_count(
                max_images=None,
                image_count=0,
                model_id=model_id,
                layer_max_images=layer_max_images,
            ), f"{model.__name__} layer_max_images={layer_max_images}: 最坏投影欠计"
        for max_images in _SEQUENTIAL_MAX_IMAGES_DOMAIN:
            for image_count in range(SEEDREAM_DEFAULT_MAX_REFERENCE_IMAGES + 1):
                assert worst >= model.per_request_image_count(
                    max_images=max_images,
                    image_count=image_count,
                    model_id=model_id,
                ), f"{model.__name__} max_images={max_images} image_count={image_count}: 最坏投影欠计"
