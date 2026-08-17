"""三语 README 围栏块定位与读取的共享辅助。

供 test_docs_consistency 与 test_env_example_guard 复用，避免两处重复实现围栏
解析与环境变量配置块锚点定位造成漂移。围栏解析以行首三反引号开合切换状态，
不依赖各语言的章节标题文字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_README = "README.md"

# 环境变量配置块的定位锚点，全文仅该 bash 块存在 SEEDREAM_MODEL_ID 赋值行。
_ENV_BLOCK_ANCHOR = re.compile(r"^\s*SEEDREAM_MODEL_ID=")


@dataclass(frozen=True)
class CodeBlock:
    """一个围栏代码块。line 为起始围栏所在行号，lines 为围栏内的正文行。"""

    lang: str
    line: int
    lines: tuple[str, ...]


def _read_readme(name: str) -> str:
    """读取仓库根目录下指定文件名的 README 全文。"""
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[CodeBlock]:
    """按行扫描全文，返回全部围栏代码块，按出现顺序排列。

    以行首三反引号围栏开合切换状态，开栏行围栏标记后的文字即为语言标识。
    """
    blocks: list[CodeBlock] = []
    lang: str | None = None
    start_line = 0
    body: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.lstrip().startswith("```"):
            if lang is not None:
                body.append(raw)
            continue
        if lang is None:
            lang = raw.lstrip()[3:].strip().lower()
            start_line = lineno
            body = []
        else:
            blocks.append(CodeBlock(lang, start_line, tuple(body)))
            lang = None
            body = []
    return blocks


def _lang_blocks(name: str, lang: str) -> list[CodeBlock]:
    """读取指定 README 并返回给定语言的全部围栏块。"""
    return [block for block in _fenced_blocks(_read_readme(name)) if block.lang == lang]


def _env_block(name: str) -> CodeBlock:
    """定位环境变量配置 bash 块，锚点为 SEEDREAM_MODEL_ID 赋值行。"""
    candidates = [
        block
        for block in _lang_blocks(name, "bash")
        if any(_ENV_BLOCK_ANCHOR.match(line) for line in block.lines)
    ]
    assert len(candidates) == 1, (
        f"{name} 环境变量配置块定位失败，含 SEEDREAM_MODEL_ID 赋值行的 bash 块"
        f"应唯一命中，实际命中 {len(candidates)} 个"
    )
    return candidates[0]
