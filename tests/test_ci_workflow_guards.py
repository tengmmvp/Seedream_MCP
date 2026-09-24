"""CI 工作流结构守护：锁定可复用工作流引用关系、触发器范围、并发分组与关键门禁步骤。

轻量文本断言 .github/workflows 下 YAML 的结构契约，不引入 pyyaml 依赖：
ci.yml 与 release.yml 的公共步骤必须经 reusable-checks.yml 复用，cspell 版本
与静态检查命令单源维护；下界作业拆至 sdk-lower-bound.yml 并按代码路径触发。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"


def _workflow_text(name: str) -> str:
    return (_WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def _job_blocks(text: str) -> dict[str, str]:
    """以两空格缩进的裸键为界切分 jobs 段，返回作业 id 到块文本的映射。"""
    blocks: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in text.split("\njobs:\n", 1)[-1].splitlines():
        job_key = re.match(r"^  ([a-z][a-z0-9-]*):\s*$", line)
        if job_key:
            if current is not None:
                blocks[current] = "\n".join(lines)
            current = job_key.group(1)
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        blocks[current] = "\n".join(lines)
    return blocks


def _paths_filter_entries(text: str) -> list[list[str]]:
    """逐块提取 paths 过滤条目，单双引号条目均归一为裸路径。"""
    normalized = text.replace("\r\n", "\n")
    block_pattern = r"""paths:\n((?:      - (?P<quote>['"])[^'"\n]+(?P=quote)\n)+)"""
    return [
        re.findall(r"""      - ['"]([^'"\n]+)['"]\n""", match.group(1))
        for match in re.finditer(block_pattern, normalized)
    ]


def test_ci_triggers_split_pr_and_main_push() -> None:
    """CI 由合入 main 的 PR 与 main 推送触发，功能分支经 PR 验证，放宽须显式修改此契约。"""
    ci = _workflow_text("ci.yml")
    assert "pull_request:" in ci
    assert "push:" in ci
    assert "branches: [main]" in ci
    assert "branches: [main, feature]" not in ci
    for banned_trigger in ("workflow_dispatch", "schedule:", "tags:"):
        assert banned_trigger not in ci


def test_ci_concurrency_keys_on_ref() -> None:
    """并发键取 ref：push 与 PR 的 ref 天然异组，无同提交双跑，fork PR 永不与本仓运行同组。

    触发器域分离后 push 只验 main（refs/heads/main），功能分支全经 PR 验证
    （refs/pull/N/merge），PR 的 ref 不含分支名；退回分支名键会让 fork 分支名
    与本仓分支名共享键空间，fork-main PR 更新可取消本仓 main 推送的 CI。
    """
    ci = _workflow_text("ci.yml")
    assert "group: ci-${{ github.ref }}" in ci
    assert "cancel-in-progress: true" in ci
    assert "group: ci-${{ github.head_ref || github.ref_name }}" not in ci


def test_release_triggers_unchanged() -> None:
    """发布流水线仅由 v* 标签与手动触发，不跟随分支推送。"""
    release = _workflow_text("release.yml")
    assert '- "v*"' in release
    assert "workflow_dispatch:" in release
    assert "pull_request:" not in release


def test_ci_test_matrix_keeps_platform_coverage() -> None:
    """Windows 与 macOS 留在矩阵以覆盖平台专属安全路径，收窄须显式修改此契约。"""
    ci = _workflow_text("ci.yml")
    for runner_os in ("ubuntu-latest", "windows-latest", "macos-latest"):
        assert runner_os in ci


def test_entries_call_reusable_checks() -> None:
    """三个入口经 workflow_call 复用公共步骤并启用各自的步骤组，不得复制命令。"""
    reusable = _workflow_text("reusable-checks.yml")
    assert "workflow_call:" in reusable
    entries = {
        "ci.yml": ("static-checks: true", "run-tests: true"),
        "release.yml": (
            "static-checks: true",
            "run-tests: true",
            "package-checks: true",
        ),
        "sdk-lower-bound.yml": ("run-tests: true",),
    }
    for name, flags in entries.items():
        text = _workflow_text(name)
        assert "uses: ./.github/workflows/reusable-checks.yml" in text
        for flag in flags:
            assert flag in text
        for command in ("black --check", "uv run flake8", "uv run mypy", "cspell@"):
            assert command not in text


def test_release_version_independent_checks_single_cell() -> None:
    """静态与构建门禁固定在单个 3.12 格只跑一次，矩阵调用只保留三版本 run-tests。"""
    jobs = _job_blocks(_workflow_text("release.yml"))
    package_jobs = [block for block in jobs.values() if "package-checks: true" in block]
    assert len(package_jobs) == 1
    package_job = package_jobs[0]
    assert "static-checks: true" in package_job
    assert "run-tests: true" not in package_job
    assert "python-versions: '[\"3.12\"]'" in package_job
    assert "uses: ./.github/workflows/reusable-checks.yml" in package_job
    test_jobs = [block for block in jobs.values() if "run-tests: true" in block]
    assert len(test_jobs) == 1
    assert 'python-versions: \'["3.12", "3.13", "3.14"]\'' in test_jobs[0]
    assert "uses: ./.github/workflows/reusable-checks.yml" in test_jobs[0]
    # 拆分不放松门禁：发布下游仍须同时等待两个门禁作业
    for job_id in ("pypi-release", "build-and-release", "docker-release"):
        assert "needs: [test, static-and-package" in jobs[job_id]


def test_release_static_mypy_covers_extra_python_versions() -> None:
    """发布门静态检查的 mypy 覆盖 3.12/3.13/3.14 三版本，回退单版本会放行新版解释器特有的类型回归。

    3.12 由矩阵格本身承担，3.13 与 3.14 经 extra-mypy-python-versions 输入在
    同一格内以 --python-version 模拟；输入默认空数组，PR 门不传不受拖慢。
    """
    reusable = _workflow_text("reusable-checks.yml")
    assert "extra-mypy-python-versions:" in reusable
    assert 'default: "[]"' in reusable
    assert "fromJSON(inputs.extra-mypy-python-versions)" in reusable
    assert "uv run --no-sync mypy --python-version" in reusable
    assert "inputs.static-checks && inputs.extra-mypy-python-versions != '[]'" in reusable
    release_jobs = _job_blocks(_workflow_text("release.yml"))
    static_jobs = [block for block in release_jobs.values() if "static-checks: true" in block]
    assert len(static_jobs) == 1
    assert 'extra-mypy-python-versions: \'["3.13", "3.14"]\'' in static_jobs[0]
    assert "extra-mypy-python-versions" not in _workflow_text("ci.yml")


def test_cspell_version_literal_single_sourced() -> None:
    """cspell 版本字面量只允许出现在可复用工作流一处。"""
    names = ("ci.yml", "release.yml", "reusable-checks.yml", "sdk-lower-bound.yml", "audit.yml")
    texts = [_workflow_text(name) for name in names]
    assert sum(text.count("cspell@") for text in texts) == 1


def test_js_syntax_check_covers_all_modules() -> None:
    """JS 语法校验步骤存在且 glob 覆盖 webapp 静态目录全部模块。"""
    reusable = _workflow_text("reusable-checks.yml")
    assert "node --input-type=module --check" in reusable
    assert "seedream_mcp/webapp/static/js/*.js" in reusable
    js_dir = _REPO_ROOT / "seedream_mcp" / "webapp" / "static" / "js"
    assert list(js_dir.glob("*.js")), "webapp 静态目录必须存在 JS 模块"


def test_sdk_lower_bound_workflow_pins_pyproject_floor() -> None:
    """下界作业拆至独立工作流，pin 与 pyproject 声明的 mcp 下界一致，以 --no-sync 全量跑测试。"""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    floor = re.search(r'"mcp>=([0-9.]+)', pyproject)
    assert floor is not None, "pyproject 必须声明 mcp 下界"
    lower = _workflow_text("sdk-lower-bound.yml")
    assert "sdk-lower-bound:" in lower
    assert f'mcp-pin-version: "{floor.group(1)}"' in lower
    # 拆出后 ci.yml 不得回流下界调用，否则同次推送双跑
    ci = _workflow_text("ci.yml")
    assert "mcp-pin-version" not in ci
    assert "sdk-lower-bound" not in _job_blocks(ci)
    reusable = _workflow_text("reusable-checks.yml")
    reusable_lines = [line.strip() for line in reusable.splitlines()]
    assert "run: uv sync --locked" in reusable_lines
    # 降级路径跳过锁版本 mcp 安装，消除先装锁版本再被 pin 替换的重复安装
    assert "run: uv sync --locked --no-install-package mcp" in reusable_lines
    assert "if: ${{ inputs.mcp-pin-version == '' }}" in reusable
    assert "uv pip install mcp==${{ inputs.mcp-pin-version }}" in reusable
    # 下界矩阵与常规矩阵同跑全量，slow 冒烟不得在下界缺席
    assert "uv run --no-sync pytest -q" in reusable
    assert '-m "not slow"' not in reusable
    # mcp-pin-version 不是步骤组开关，降级测试须与 run-tests 组合触发
    assert "inputs.run-tests && inputs.mcp-pin-version != ''" in reusable


def test_reusable_checks_uv_run_always_no_sync() -> None:
    """reusable-checks.yml 的全部 uv run 必须带 --no-sync，裸调用会按 lock 隐式重同步环境。

    mcp-pin-version 降级安装的 mcp 会被隐式重同步升回锁版本，输入允许 pin 与
    各步骤组自由组合，保护须覆盖全部步骤组而非仅测试步骤；环境保真由前置
    uv sync 与 pin 步骤承担。
    """
    reusable = _workflow_text("reusable-checks.yml")
    bare_runs = [
        line.strip()
        for line in reusable.splitlines()
        if not line.lstrip().startswith("#") and re.search(r"uv run\b(?! --no-sync)", line)
    ]
    assert not bare_runs, f"裸 uv run 会隐式重同步并覆盖 pin 版本: {bare_runs}"


def test_sdk_lower_bound_workflow_filters_document_only_changes() -> None:
    """下界工作流仅在代码、依赖与工作流文件路径变更时触发，文档类改动不跑数分钟的下界全量。"""
    lower = _workflow_text("sdk-lower-bound.yml")
    # 触发分支与 ci.yml 保持一致，路径收窄不得顺带收窄事件范围
    assert "branches: [main]" in lower
    assert "branches: [main, feature]" not in lower
    for banned_trigger in ("workflow_dispatch", "schedule:", "tags:"):
        assert banned_trigger not in lower
    entries = _paths_filter_entries(lower)
    assert len(entries) == 2, "pull_request 与 push 事件各自维护 paths 过滤"
    assert entries[0] == entries[1], "两侧过滤清单必须同步维护"
    for required_path in (
        "seedream_mcp/**",
        "tests/**",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/sdk-lower-bound.yml",
        ".github/workflows/reusable-checks.yml",
    ):
        assert required_path in entries[0]
        assert required_path in entries[1]
    assert "README" not in lower
    # 并发键前缀独立于 ci.yml，同次推送触发的两条流水线不得互相取消
    assert "group: sdk-lower-bound-${{ github.ref }}" in lower


def test_ci_setup_actions_sha_single_sourced() -> None:
    """ci.yml 不内联公共 setup 动作 SHA，setup 引用单源收敛到可复用工作流。"""
    ci = _workflow_text("ci.yml")
    assert "actions/setup-python@" not in ci
    assert "astral-sh/setup-uv@" not in ci
    # docker 作业保留自身 checkout，其余 setup 步骤复用 reusable-checks.yml
    assert ci.count("actions/checkout@") == 1
