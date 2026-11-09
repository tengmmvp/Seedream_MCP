"""os 层共享替身 _install_failing_resolve_for 的契约守护测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from _lib.os_fakes import _install_failing_resolve_for


def test_failing_resolve_counts_named_and_passes_others_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指定名抛瞬时 OSError 并计数，他名透传；计数保持与普通字典的相等与下标语义。"""
    good = tmp_path / "good.png"
    good.write_bytes(b"x")
    failure_counts = _install_failing_resolve_for(monkeypatch, "bad.png")

    with pytest.raises(OSError, match="temporarily locked"):
        (tmp_path / "sub" / "bad.png").resolve()
    assert good.resolve() == good
    assert failure_counts == {"bad.png": 1}
    assert failure_counts["bad.png"] == 1


def test_failing_resolve_active_gate_restores_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active 门置 False 后同名恢复透传且不再计数，供停滞谱系用例运行中翻转。"""
    failure_counts = _install_failing_resolve_for(monkeypatch, "bad.png")

    with pytest.raises(OSError, match="temporarily locked"):
        Path("dir/bad.png").resolve()
    failure_counts.active = False

    Path("dir/bad.png").resolve()
    assert failure_counts == {"bad.png": 1}
