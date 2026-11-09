"""normcase 分量级包含判定对 pathlib relative_to 的差分矩阵候选与参照：单基差分与多基包含测试共用。"""

from __future__ import annotations

from pathlib import Path


def relative_to_contains(candidate: Path, base: Path) -> bool:
    """pathlib relative_to 包含判定的参照实现，差分矩阵的期望值来源。"""
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def containment_candidates(scope: Path) -> list[Path]:
    """以 scope 为基构造差分候选清单，清单序为先导三种基础形态（相等、界内、父级界外），其后为同前缀兄弟目录及其内文件、兄弟前缀文件名与大小写折叠形态。

    先导三项经 basic_containment_candidates 具名导出，全量矩阵由原语差分测试承担。
    """
    base = scope.parent
    sibling_dir = base / f"{scope.name}c"
    return [
        scope,
        scope / "x.png",
        base,
        sibling_dir,
        sibling_dir / "x.png",
        base / f"{scope.name}-sibling.png",
        Path(str(scope).upper()),
        Path(str(scope / "x.png").upper()),
    ]


def basic_containment_candidates(scope: Path) -> list[Path]:
    """多基包含测试的基础候选：相等、界内与父级界外三项，取主清单先导段。"""
    return containment_candidates(scope)[:3]


def nonascii_containment_pairs(scope: Path) -> list[tuple[Path, Path]]:
    """非 ASCII 大小写变体的 (候选, 基) 差分对，大写形态在候选与基两侧各落一次。

    变体对 (U+212A KELVIN SIGN, k) 与 (U+0130 LATIN CAPITAL LETTER I WITH DOT
    ABOVE, i+U+0307) 经 str.lower() 折叠相等而 ntpath.normcase 的不变区域设置
    小写不展开，差分矩阵借此锁定 normcase_parts 与 pathlib 的同口径折叠。
    """
    base = scope.parent
    pairs: list[tuple[Path, Path]] = []
    for uppercase, folded in (("\u212a", "k"), ("\u0130", "i\u0307")):
        pairs.append((base / folded / "x.png", base / uppercase))
        pairs.append((base / uppercase / "x.png", base / folded))
    return pairs
