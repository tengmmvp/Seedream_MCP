"""单文件覆盖率下限校验，供 CI 与 release 工作流共用。

下限取 pyproject [tool.coverage_floors].file_coverage_min，branch 开启后为
语句+分支合并口径；测量取 coverage.json，任一文件低于下限或无测量数据即失败退出。
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def main(repo_root: Path | None = None) -> None:
    """校验 coverage.json 全部文件的覆盖率不低于 pyproject 声明的下限。

    Args:
        repo_root: 仓库根目录，取 pyproject.toml 与 coverage.json 的位置；
            缺省为脚本所在目录上溯一级的仓库根，测试注入临时目录复用同一判定路径。
    """
    # Windows 控制台默认代码页编不出中文输出，输出流重配置为 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parent.parent if repo_root is None else repo_root
    floor = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "coverage_floors"
    ]["file_coverage_min"]
    measured = json.loads((root / "coverage.json").read_text(encoding="utf-8"))["files"]
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
