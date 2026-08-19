"""守护测试：seedream_mcp.utils 包门面的延迟导出声明保持一致。

``utils/__init__.py`` 的公开接口由 ``__all__`` 与 ``_LAZY_EXPORTS`` 两处声明共同
维护，只改一处而漂移会使对外符号静默丢失或抛 ``AttributeError``。本测试锁定两者
一致，并校验映射指向的真实目标可解析。
"""

from importlib import import_module

import pytest

import seedream_mcp.utils as utils_pkg


def test_all_keys_match_lazy_exports() -> None:
    """``__all__`` 与 ``_LAZY_EXPORTS`` 的键集合必须完全一致。"""
    assert set(utils_pkg.__all__) == set(utils_pkg._LAZY_EXPORTS.keys())


def test_lazy_export_targets_exist() -> None:
    """``_LAZY_EXPORTS`` 中每个映射指向的 (子模块, 属性名) 必须真实存在。"""
    for export_name, (submodule_rel, attr_name) in utils_pkg._LAZY_EXPORTS.items():
        module = import_module(submodule_rel, utils_pkg.__name__)
        assert hasattr(
            module, attr_name
        ), f"{submodule_rel}.{attr_name} 不存在，导出名 {export_name!r} 映射有误"


def test_every_public_export_resolves_via_getattr() -> None:
    """经包门面逐个访问 ``__all__`` 中的符号，必须成功解析且非 None。"""
    for name in utils_pkg.__all__:
        value = getattr(utils_pkg, name)
        assert value is not None, f"导出名 {name!r} 解析为 None"


def test_unknown_attribute_raises_attribute_error() -> None:
    """访问未声明的属性应抛出 AttributeError，而非静默返回 None。"""
    with pytest.raises(AttributeError):
        getattr(utils_pkg, "definitely_not_an_export")


def test_dir_includes_lazy_exports() -> None:
    """``dir()`` 须包含尚未触发导入的延迟导出公开名，保证补全与可发现性。"""
    listed = dir(utils_pkg)
    for name in utils_pkg.__all__:
        assert name in listed, f"dir() 缺少延迟导出名 {name!r}"
