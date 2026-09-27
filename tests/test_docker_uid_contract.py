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


def test_docker_uid_contract_single_sourced_from_dockerfile() -> None:
    """useradd 声明的 uid 是挂载属主契约唯一事实源，任一站点字面量漂移即失败。

    只断言每份站点至少捕获一个 uid 字面量且全部等于声明值；计数在此登记
    会误伤合法文档编辑。
    """
    uid = mount_uid()
    for name in _CONTRACT_FILES:
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        captures = [
            number
            for pattern in _UID_LITERAL_PATTERNS
            for match in pattern.finditer(text)
            for number in match.groups()
        ]
        assert captures and all(
            number == uid for number in captures
        ), f"{name} 的 uid 字面量 {captures} 应存在且全部为 {uid}"
