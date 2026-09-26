"""Docker 挂载属主契约守护：容器 uid 以 Dockerfile 的 useradd 声明为唯一事实源。

契约字面量分散于 Dockerfile、三份 README、docker-compose.yml 与 release.yml，
任一站点与声明的 uid 漂移都会使 Linux 宿主的挂载目录对容器用户不可写。
"""

from __future__ import annotations

import re
from pathlib import Path

from _docker_uid import mount_uid

_REPO_ROOT = Path(__file__).resolve().parent.parent

# 站点内 uid 字面量的全部书写形态：uid 提法、chown 属主对、简繁体属主说明与
# user/--user 运行时覆写。
_UID_LITERAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\buid\s*[=:]?\s*(\d+)", re.IGNORECASE),
    re.compile(r"chown(?:\s+-[A-Za-z]+)*\s+(\d+):(\d+)"),
    re.compile(r"[属屬]主[为為]\s*(\d+)"),
    re.compile(r'(?<![\w-])user:\s*"?(\d+)(?::\d+)?'),
    re.compile(r"--user[= ](\d+)"),
)

_CONTRACT_FILES = (
    "Dockerfile",
    "README.md",
    "README.en.md",
    "README.zh-TW.md",
    "docker-compose.yml",
    ".github/workflows/release.yml",
)

# 每份契约文件的 uid 捕获数登记，站点增删或改形都须同步此表。
_EXPECTED_CAPTURE_COUNTS: dict[str, int] = {
    "Dockerfile": 4,
    "README.md": 6,
    "README.en.md": 6,
    "README.zh-TW.md": 6,
    "docker-compose.yml": 3,
    ".github/workflows/release.yml": 2,
}


def test_docker_uid_contract_single_sourced_from_dockerfile() -> None:
    """useradd 声明的 uid 是挂载属主契约唯一事实源，任一站点字面量漂移即失败。"""
    uid = mount_uid()
    for name in _CONTRACT_FILES:
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        captures = [
            number
            for pattern in _UID_LITERAL_PATTERNS
            for match in pattern.finditer(text)
            for number in match.groups()
        ]
        expected = [uid] * _EXPECTED_CAPTURE_COUNTS[name]
        assert captures == expected, (
            f"{name} 的 uid 捕获序列 {captures} 应为 {expected}，"
            "站点增删或书写形态变更须同步检出模式与登记"
        )
