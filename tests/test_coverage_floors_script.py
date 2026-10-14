"""scripts/check_coverage_floors.py 的判定路径单元测试。

门禁脚本经可选 repo_root 参数注入 tmp_path 下的假 pyproject 与 coverage.json，
覆盖「低于下限失败 / 全部通过 / files 为空退出」三条路径；解析与比较逻辑回归
在本地测试变红，而非以 CI 门禁步骤的裸 traceback 形态出现。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_coverage_floors.py"

_PYPROJECT_TEMPLATE = """\
[tool.coverage_floors]
file_coverage_min = 70
"""


def _load_script() -> ModuleType:
    """按文件位置加载门禁脚本模块，scripts 目录不在测试的导入路径上。"""
    spec = importlib.util.spec_from_file_location("check_coverage_floors", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_case(tmp_path: Path, files: dict[str, Any]) -> None:
    """在临时仓库根下落盘假 pyproject 与 coverage.json。"""
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT_TEMPLATE, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(json.dumps({"files": files}), encoding="utf-8")


def _entry(percent: float) -> dict[str, Any]:
    """构造 coverage.json 的单文件条目，取 summary.percent_covered 字段形态。"""
    return {"summary": {"percent_covered": percent}}


def test_all_files_meeting_floor_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """全部文件达到下限时正常返回，恰好等于下限的文件按通过处理。"""
    _write_case(tmp_path, {"pkg/edge.py": _entry(70.0), "pkg/ok.py": _entry(100.0)})
    _load_script().main(repo_root=tmp_path)
    output = capsys.readouterr().out
    assert "通过" in output
    assert "2 个文件" in output


def test_file_below_floor_fails_with_listing(tmp_path: Path) -> None:
    """低于下限的文件以清单形态失败，达标文件不进入失败消息。"""
    _write_case(tmp_path, {"pkg/low.py": _entry(50.0), "pkg/ok.py": _entry(100.0)})
    with pytest.raises(SystemExit) as excinfo:
        _load_script().main(repo_root=tmp_path)
    message = str(excinfo.value.code)
    assert "pkg/low.py" in message
    assert "50.0%" in message
    assert "pkg/ok.py" not in message


def test_empty_files_measurement_fails(tmp_path: Path) -> None:
    """coverage.json 无测量数据时失败退出，不落入空清单假绿。"""
    _write_case(tmp_path, {})
    with pytest.raises(SystemExit) as excinfo:
        _load_script().main(repo_root=tmp_path)
    assert str(excinfo.value.code) == "coverage.json 无测量数据"
