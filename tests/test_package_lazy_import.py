"""包级延迟导入测试：导入 seedream_mcp 不立即加载 client 与 server。

涉及包身份与 sys.modules 快照的用例一律子进程运行：进程内 reload 会替换模块对象，
迫使其他测试文件以 import_module 间接取类规避过期引用，污染成本扩散到整个套件。
"""

import subprocess
import sys
from pathlib import Path


def _run_in_subprocess(code: str) -> None:
    """在干净子进程中断言，失败时透传 stderr。

    cwd 固定为仓库根，使 ``python -c`` 的 sys.path 首项稳定指向项目根，
    不依赖 pytest 进程的当前工作目录。
    """
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert completed.returncode == 0, completed.stderr


def test_import_seedream_package_does_not_eager_import_client_or_server() -> None:
    _run_in_subprocess(
        "import sys, seedream_mcp; "
        "assert 'seedream_mcp.client' not in sys.modules; "
        "assert 'seedream_mcp.server' not in sys.modules"
    )


def test_accessing_export_triggers_lazy_import() -> None:
    _run_in_subprocess(
        "import sys, seedream_mcp; "
        "_ = seedream_mcp.SeedreamClient; _ = seedream_mcp.mcp; "
        "assert 'seedream_mcp.client' in sys.modules; "
        "assert 'seedream_mcp.server' in sys.modules"
    )


def test_all_derived_from_version_and_lazy_exports() -> None:
    """__all__ 须派生自 __version__ 与 _LAZY_EXPORTS，避免手动同步漂移。"""
    import seedream_mcp

    assert seedream_mcp.__all__ == ["__version__"] + list(seedream_mcp._LAZY_EXPORTS)


def test_every_public_export_resolves_via_getattr() -> None:
    """__all__ 中除 __version__ 外的每个导出名须经 __getattr__ 成功解析且非 None。"""
    import seedream_mcp

    for name in seedream_mcp.__all__:
        if name == "__version__":
            continue
        value = getattr(seedream_mcp, name)
        assert value is not None, f"导出名 {name!r} 解析为 None"


def test_unknown_attribute_raises_attribute_error() -> None:
    """访问未声明的属性应抛出 AttributeError，而非静默返回 None。"""
    import pytest

    import seedream_mcp

    with pytest.raises(AttributeError):
        getattr(seedream_mcp, "definitely_not_an_export")


def test_import_server_does_not_eager_load_pil() -> None:
    """导入 seedream_mcp.server 不得触发 PIL 加载。

    PIL 首次导入含解码器注册，成本达数十毫秒；图像相关模块一律函数内惰性导入，
    使该成本落点在工作线程而非事件循环线程。子进程运行守护，避免本进程已加载
    的模块污染断言。
    """
    import subprocess
    import sys

    code = (
        "import sys, seedream_mcp.server; "
        "loaded = [m for m in sys.modules if m == 'PIL' or m.startswith('PIL.')]; "
        "assert not loaded, f'PIL eagerly imported: {loaded}'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
