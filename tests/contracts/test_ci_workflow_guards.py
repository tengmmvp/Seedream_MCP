"""CI 工作流结构守护：锁定可复用工作流引用关系、触发器范围、并发分组与关键门禁步骤，
以及跑测作业对 node 运行时的依赖。

轻量文本断言 .github/workflows 下 YAML 的结构契约，不引入 pyyaml 依赖：
ci.yml 与 release.yml 的公共步骤必须经 reusable-checks.yml 复用，cspell 版本
与静态检查命令单源维护。下界作业拆至 sdk-lower-bound.yml 并按代码路径触发。
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from _lib.docker_uid import chown_owner_pair

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"


def _workflow_paths() -> list[Path]:
    """按文件名排序返回全部工作流文件，.yml 与 .yaml 扩展名 GitHub 均接受。"""
    return sorted(path for path in _WORKFLOWS_DIR.iterdir() if path.suffix in {".yml", ".yaml"})


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


def _step_block(text: str, step_name: str) -> str:
    """切出指定名称的步骤块，止于下一个步骤声明行。"""
    marker = f"- name: {step_name}"
    start = text.index(marker) + len(marker)
    return text[start:].split("\n      - name:", 1)[0]


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


def test_release_compose_quick_start_prepares_mount_owner() -> None:
    """Release 快速上手的 Compose 步骤含挂载属主预置命令，与 README 部署契约同源。

    Linux 上的 Docker 以 root 属主自动创建缺失的挂载源，容器内 uid 不可写，
    日志目录创建失败致启动退出，叠加 restart: unless-stopped 循环重启。
    """
    release = _workflow_text("release.yml")
    assert f"mkdir -p .seedream && chown {chown_owner_pair()} .seedream" in release


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
    assert "src/seedream_mcp/webapp/static/js/*.js" in reusable
    js_dir = _REPO_ROOT / "src" / "seedream_mcp" / "webapp" / "static" / "js"
    assert list(js_dir.glob("*.js")), "webapp 静态目录必须存在 JS 模块"


def test_node_behavior_tests_share_syntax_check_node_runtime() -> None:
    """跑测作业与 JS 语法检查同依赖运行器预装的 node：语法检查步骤在位，CI 缺 node 即红。

    tests/webapp/test_webapp_frontend_request_body.py 经 tests/_lib/frontend_harness.mjs
    调 node 执行真实 generate.js，本地缺 node 整段跳过。工作流不单独安装 node，
    运行器预装是唯一来源，语法检查步骤是该依赖在工作流内的显式锚。跑测换到
    无 node 的运行器后行为测试在 CI 静默跳过。
    """
    reusable = _workflow_text("reusable-checks.yml")
    test_jobs = [
        block for block in _job_blocks(reusable).values() if "uv run --no-sync pytest -q" in block
    ]
    assert test_jobs, "reusable-checks.yml 缺跑测步骤"
    for block in test_jobs:
        assert (
            "node --input-type=module --check" in block
        ), "跑测作业缺 JS 语法检查步骤，前端行为测试的 node 依赖失去显式锚"
    if os.environ.get("CI"):
        assert shutil.which("node") is not None, "CI 运行器缺 node，前端行为测试将静默跳过"


def test_per_file_coverage_floor_step_exists_and_gated() -> None:
    """单文件覆盖率下限步骤须绑定非降级跑测门，json 生成与脚本消费同步骤存在。

    总量下限由 pyproject fail_under 兜底，单文件下限仅此步骤生效，静默移除
    会使低覆盖文件无门禁合入。
    """
    reusable = _workflow_text("reusable-checks.yml")
    assert reusable.count("scripts/check_coverage_floors.py") == 1
    floors_step = _step_block(reusable, "📏 Check per-file coverage floors")
    assert "if: ${{ inputs.run-tests && inputs.mcp-pin-version == '' }}" in floors_step
    assert "python -m coverage json -o coverage.json" in floors_step
    assert "scripts/check_coverage_floors.py" in floors_step
    # json 生成的数据文件来自跑测步骤的同门 --cov，缺席时下限判定落在空数据上
    assert "--cov=seedream_mcp" in _step_block(reusable, "✅ Run tests")


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
        "src/seedream_mcp/**",
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


def test_dockerignore_wildcards_intact() -> None:
    """排除规则的关键条目在位，损坏形态在此拦截。"""
    lines = [
        line.strip()
        for line in (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    ]
    for required in ("__pycache__", "*.pyc", "*.md", "*.log", ".coverage.*"):
        assert required in lines, f"排除条目缺失或通配符损坏: {required}"
    for corrupted in ("_.", "\\*", "**pycache**"):
        assert not any(corrupted in line for line in lines), f"出现通配符损坏形态: {corrupted}"


def test_ci_setup_actions_sha_single_sourced() -> None:
    """ci.yml 不内联公共 setup 动作 SHA，setup 引用单源收敛到可复用工作流。"""
    ci = _workflow_text("ci.yml")
    assert "actions/setup-python@" not in ci
    assert "astral-sh/setup-uv@" not in ci
    # docker 作业保留自身 checkout，其余 setup 步骤复用 reusable-checks.yml
    assert ci.count("actions/checkout@") == 1


# uv 版本字面量形态：数字开头，其后为字母、数字、点与连字符。
_UV_VERSION_TOKEN = r"[0-9][0-9A-Za-z.-]*"

# Dockerfile 的全部 ARG UV_VERSION 声明行，捕获等号后的整行余量，合法形态由钉版值形态单独判定。
_DOCKERFILE_UV_VERSION_DECLARATION_PATTERN = re.compile(r"^ARG UV_VERSION=(.*)$", re.MULTILINE)

# Dockerfile 合法钉版值形态：<版本> 后仅允许行尾空白。
# Docker 把行首以外的 # 视为参数，行尾注释会混入版本值。
_DOCKERFILE_UV_VERSION_VALUE_PATTERN = re.compile(rf"(?P<version>{_UV_VERSION_TOKEN})[ \t]*")

# setup-uv 步骤的 uses 行，动作名可带单引号或双引号。
# 列表项短横线与 uses 同行，或在 uses 之前的某一行。其间可夹同级键。
_SETUP_UV_USES_PATTERN = re.compile(r"""(?:-\s+)?uses:\s*['"]?astral-sh/setup-uv@""")

# with: 行形态，允许行尾注释。
_WITH_KEY_PATTERN = re.compile(r"with:(?: +#.*)?")

# setup-uv 钉版行形态：version: <版本>，允许引号与行尾注释。
_WORKFLOW_UV_VERSION_PATTERN = re.compile(
    rf"""version: +(?P<quote>['"]?)(?P<version>{_UV_VERSION_TOKEN})(?P=quote)(?: +#.*)?"""
)

# 缩进列数与去首尾空白的行文本。
_IndentedLine = tuple[int, str]


def _dockerfile_uv_pin(dockerfile: str) -> str:
    """提取 Dockerfile 的 uv 钉版值，钉版值形态不合法或取值不唯一即断言失败。"""
    declared = _DOCKERFILE_UV_VERSION_DECLARATION_PATTERN.findall(dockerfile)
    assert declared, "Dockerfile 未在行首以大写声明 ARG UV_VERSION=<版本>"
    pins: list[str] = []
    for value in declared:
        pin = _DOCKERFILE_UV_VERSION_VALUE_PATTERN.fullmatch(value)
        assert pin is not None, (
            f"Dockerfile 的 ARG UV_VERSION 行不是合法钉版行，"
            f"须为 <版本> 后接至多行尾空白，实际 ARG UV_VERSION={value}"
        )
        pins.append(pin.group("version"))
    unique_pins = sorted(set(pins))
    assert len(unique_pins) == 1, f"Dockerfile 的 ARG UV_VERSION 声明须唯一，实际 {unique_pins}"
    return unique_pins[0]


def _content_lines(text: str) -> list[_IndentedLine]:
    """把文本的非空非注释行整理为带缩进的行列表。"""
    content: list[_IndentedLine] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            content.append((len(line) - len(line.lstrip()), stripped))
    return content


def _step_version_pins(block: list[_IndentedLine]) -> list[str]:
    """提取步骤块内 with: 直接子键 version 的取值，嵌套更深的键与块标量文本不计入。"""
    with_index = next(
        (index for index, (_, line) in enumerate(block) if _WITH_KEY_PATTERN.fullmatch(line)),
        None,
    )
    if with_index is None:
        return []
    with_indent = block[with_index][0]
    pins: list[str] = []
    child_indent: int | None = None
    for indent, line in block[with_index + 1 :]:
        if indent <= with_indent:
            break
        if child_indent is None:
            child_indent = indent
        if indent == child_indent and (pin := _WORKFLOW_UV_VERSION_PATTERN.fullmatch(line)):
            pins.append(pin.group("version"))
    return pins


def _setup_uv_step_pins(text: str) -> list[list[str]]:
    """按文件顺序逐个 setup-uv 步骤提取 version 钉版取值。

    步骤块自 uses 行的下一行起，止于首个更浅缩进的行或不深于 uses 行的列表项。
    空行、注释行与同级兄弟键不结束块。
    """
    content = _content_lines(text)
    pins_per_step: list[list[str]] = []
    for index, (uses_indent, uses_line) in enumerate(content):
        if _SETUP_UV_USES_PATTERN.match(uses_line) is None:
            continue
        block: list[_IndentedLine] = []
        for indent, line in content[index + 1 :]:
            is_list_item = line == "-" or line.startswith("- ")
            if indent < uses_indent or (is_list_item and indent <= uses_indent):
                break
            block.append((indent, line))
        pins_per_step.append(_step_version_pins(block))
    return pins_per_step


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.7\n",
            [["0.13.7"]],
            id="name-then-uses",
        ),
        pytest.param(
            "      - uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.7\n",
            [["0.13.7"]],
            id="dash-on-uses",
        ),
        pytest.param(
            "      - name: Install uv\n"
            '        uses: "astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567"\n'
            "        with:\n"
            "          version: 0.13.7\n",
            [["0.13.7"]],
            id="double-quoted-uses",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: 'astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567'\n"
            "        with:\n"
            "          version: 0.13.7\n",
            [["0.13.7"]],
            id="single-quoted-uses",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "\n"
            "        with:\n"
            "          version: 0.13.7\n",
            [["0.13.7"]],
            id="blank-line-in-step",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with: # 钉版输入\n"
            "            # 注释行的缩进不参与确定子行缩进。\n"
            "          version: 0.13.7 # 与 Dockerfile 同源\n",
            [["0.13.7"]],
            id="comments",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            '          version: "0.13.7"\n'
            "      - uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: '0.13.7'\n",
            [["0.13.7"], ["0.13.7"]],
            id="quoted-version-values",
        ),
        pytest.param(
            "      - name: Install uv\r\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\r\n"
            "        with:\r\n"
            "          version: 0.13.7\r\n",
            [["0.13.7"]],
            id="crlf",
        ),
    ],
)
def test_setup_uv_step_pins_reads_step_writing_forms(text: str, expected: list[list[str]]) -> None:
    """步骤写法变化不影响钉版识别。

    列表项短横线的位置、uses 与版本值的引号、注释、空行与 CRLF 行尾均不改变解析结果。
    with: 下的注释行不是内容行，不参与确定子行缩进。
    """
    assert _setup_uv_step_pins(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "      - name: Install uv\n"
            "        if: ${{ inputs.static-checks }}\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        env:\n"
            "          version: 9.9.9\n"
            "        with:\n"
            "          version: 0.13.7\n"
            "\n"
            "      - name: Install uv again\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.7\n"
            "        env:\n"
            "          version: 9.9.8\n"
            "        if: ${{ always() }}\n",
            [["0.13.7"], ["0.13.7"]],
            id="sibling-keys-around-with",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        run: |\n"
            "          version: 9.9.9\n"
            "        with:\n"
            "          version: 0.13.7\n"
            "      - name: Print uv version\n"
            "        run: |\n"
            "          uv --version\n"
            "          version: 9.9.8\n",
            [["0.13.7"]],
            id="block-scalar-versions",
        ),
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.7\n"
            "          cache-suffix:\n"
            "            version: 9.9.9\n",
            [["0.13.7"]],
            id="deeper-nested-key-under-with",
        ),
    ],
)
def test_setup_uv_step_pins_excludes_versions_outside_with_inputs(
    text: str, expected: list[list[str]]
) -> None:
    """with: 输入以外的 version 不计入钉版。

    同级兄弟键下的嵌套键、run: 块标量正文与 with: 下嵌套更深的键均在排除之列。
    """
    assert _setup_uv_step_pins(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.7\n"
            "      - name: Install uv again\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 0.13.6\n",
            [["0.13.7"], ["0.13.6"]],
            id="ordered-steps",
        ),
        pytest.param(
            "      - uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "      - name: Checkout\n"
            "        uses: actions/checkout@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 9.9.9\n"
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          enable-cache: true\n",
            [[], []],
            id="unpinned-steps-before-sibling-inputs",
        ),
        pytest.param(
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Install uv\n"
            "        uses: astral-sh/setup-uv@0123456789abcdef0123456789abcdef01234567\n"
            "  checks:\n"
            "    uses: ./.github/workflows/reusable-checks.yml\n"
            "    with:\n"
            "      version: 9.9.9\n",
            [[]],
            id="unpinned-last-step-before-caller-job",
        ),
        pytest.param(
            "      - name: Checkout\n"
            "        uses: actions/checkout@0123456789abcdef0123456789abcdef01234567\n"
            "        with:\n"
            "          version: 9.9.9\n",
            [],
            id="no-setup-uv-step",
        ),
    ],
)
def test_setup_uv_step_pins_reports_each_step_in_order(
    text: str, expected: list[list[str]]
) -> None:
    """setup-uv 步骤按文件顺序逐个上报。

    缺 version 的步骤得到空列表，不拾取其后兄弟步骤与调用作业的 with: 输入。
    """
    assert _setup_uv_step_pins(text) == expected


def test_uv_version_pinned_to_dockerfile() -> None:
    """uv 版本五处同钉，任一漂移即失败。

    五处为 Dockerfile 的 UV_VERSION、三个工作流的 setup-uv 与 pyproject 的 required-version。
    """
    pinned = _dockerfile_uv_pin((_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"))
    setup_uv_pins = {
        workflow.name: step_pins
        for workflow in _workflow_paths()
        if (step_pins := _setup_uv_step_pins(_workflow_text(workflow.name)))
    }
    assert len(setup_uv_pins) >= 3, (
        f"setup-uv 跨不少于三个工作流重复钉版是本守护的目标形态，"
        f"实际 {len(setup_uv_pins)} 个: {list(setup_uv_pins)}"
    )
    for name, step_pins in setup_uv_pins.items():
        for ordinal, pins in enumerate(step_pins, start=1):
            assert pins, f"{name} 第 {ordinal} 个 setup-uv 步骤未钉 version"
            assert pins == [pinned], (
                f"{name} 第 {ordinal} 个 setup-uv 步骤须恰钉一处 version: {pinned}，"
                f"与 Dockerfile UV_VERSION 同源，实际 {pins}"
            )
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    required_version = pyproject.get("tool", {}).get("uv", {}).get("required-version")
    assert (
        required_version == f"=={pinned}"
    ), f"pyproject 的 [tool.uv] required-version 须为 =={pinned}，与 Dockerfile UV_VERSION 同源"


# uses 行的 action 钉版引用形态：owner/action@40 位十六进制 SHA，动作名可带单引号或双引号，
# 本地复用工作流无 @ref 不计入。
_ACTION_SHA_USE_PATTERN = re.compile(
    r"""uses:\s*['"]?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})"""
)


@pytest.mark.parametrize("quote", ["", '"', "'"], ids=["bare", "double-quoted", "single-quoted"])
def test_action_sha_pattern_reads_quoted_uses(quote: str) -> None:
    """SHA 守护的 uses 模式对动作名无引号、单引号与双引号三种写法解析出同一 action 与 SHA。"""
    sha = "0123456789abcdef0123456789abcdef01234567"
    uses_line = f"        uses: {quote}astral-sh/setup-uv@{sha}{quote} # v10.3.3\n"
    assert _ACTION_SHA_USE_PATTERN.findall(uses_line) == [("astral-sh/setup-uv", sha)]


def test_action_sha_pins_single_sourced_across_workflows() -> None:
    """同一 action 名跨全部工作流的 SHA 钉版唯一，升级漏改任一文件即失败。

    checkout 在 ci/release/audit/reusable-checks 多处重复钉版、setup-uv 在
    reusable-checks/release/audit 三处重复。部分 bump 时 merge gate
    验证的动作版本与 release/audit 实际执行的版本分叉且无其他报警。
    """
    pins: dict[str, list[str]] = {}
    for workflow in _workflow_paths():
        for action, sha in _ACTION_SHA_USE_PATTERN.findall(_workflow_text(workflow.name)):
            pins.setdefault(action, []).append(sha)
    assert pins, ".github/workflows 未解析到任何 SHA 钉版的 action 引用，解析失效"
    # checkout 与 setup-uv 属跨文件重复钉版的 action，缺席说明扫描范围失真。
    for action in ("actions/checkout", "astral-sh/setup-uv"):
        assert action in pins, f"{action} 应以 SHA 钉版引用"
        assert len(pins[action]) >= 2, f"{action} 的多处钉版是本守护的目标形态，实际仅一处"
    drifted = {action: sorted(set(shas)) for action, shas in pins.items() if len(set(shas)) > 1}
    assert not drifted, f"同一 action 存在多个不同 SHA 钉版，升级须全部工作流同步: {drifted}"
