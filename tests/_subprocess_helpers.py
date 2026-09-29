"""子进程断言共享辅助，供延迟导入与导入纯净性用例复用。"""

import subprocess
import sys
from pathlib import Path


def _run_in_subprocess(code: str) -> None:
    """在干净子进程中断言，失败时透传 stderr。

    cwd 固定为仓库根，不依赖 pytest 进程的当前工作目录。
    """
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert completed.returncode == 0, completed.stderr
