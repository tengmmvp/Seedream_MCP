"""单文件语句覆盖率下限校验，供 CI 与 release 工作流共用。

下限取 pyproject [tool.coverage_floors].line_coverage_min，测量取 coverage.json；
任一文件低于下限或无测量数据即失败退出。
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    floor = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "coverage_floors"
    ]["line_coverage_min"]
    measured = json.loads((repo_root / "coverage.json").read_text(encoding="utf-8"))["files"]
    if not measured:
        sys.exit("coverage.json 无测量数据")
    below = [
        f"{path}: {stats['summary']['percent_covered']:.1f}%"
        for path, stats in measured.items()
        if stats["summary"]["percent_covered"] < floor
    ]
    if below:
        sys.exit(f"单文件覆盖率低于 {floor}% 下限: " + ", ".join(below))
    print(f"单文件覆盖率下限 {floor}% 通过（{len(measured)} 个文件）")


if __name__ == "__main__":
    main()
