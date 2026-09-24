"""三语 README 围栏块、正文行与 HTML 表格解析的共享辅助。

供 test_docs_consistency 与 test_readme_code_parity 复用，避免围栏解析、正文行
提取、表格解析与能力差异表定位在多处重复实现造成漂移。围栏解析以行首三反引
号开合切换状态，不依赖各语言的章节标题文字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_README = "README.md"


@dataclass(frozen=True)
class CodeBlock:
    """一个围栏代码块。

    Attributes:
        lang: 围栏语言标识。
        line: 起始围栏所在行号。
        lines: 围栏内的正文行。
    """

    lang: str
    line: int
    lines: tuple[str, ...]


def _read_readme(name: str) -> str:
    """读取仓库根目录下指定文件名的 README 全文。

    Args:
        name: README 文件名。

    Returns:
        文件全文文本。
    """
    return (PROJECT_ROOT / name).read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[CodeBlock]:
    """按行扫描全文提取全部围栏代码块。

    以行首三反引号围栏开合切换状态，开栏行围栏标记后的文字即为语言标识。

    Args:
        text: README 全文文本。

    Returns:
        按出现顺序排列的围栏块列表。
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


def _fenced_line_numbers(text: str) -> set[int]:
    """围栏块占用的行号集合，区间含起始围栏行、块内正文行与闭合围栏行。"""
    return {
        lineno
        for block in _fenced_blocks(text)
        for lineno in range(block.line, block.line + len(block.lines) + 2)
    }


def _lang_blocks(name: str, lang: str) -> list[CodeBlock]:
    """读取指定 README 并返回给定语言的全部围栏代码块。

    Args:
        name: README 文件名。
        lang: 围栏语言标识。

    Returns:
        该语言的围栏块列表，按出现顺序排列。
    """
    return [block for block in _fenced_blocks(_read_readme(name)) if block.lang == lang]


def _prose_lines(name: str) -> list[tuple[int, str]]:
    """返回不在任何围栏代码块内的正文行，带 1 基行号。"""
    text = _read_readme(name)
    fenced = _fenced_line_numbers(text)
    return [
        (lineno, raw)
        for lineno, raw in enumerate(text.splitlines(), start=1)
        if lineno not in fenced
    ]


# HTML 表格解析的标签形态，单元格内联标签剥除后以竖线拼接伪行
_TAG_PATTERN = re.compile(r"<[^>]+>")
_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL)
_ROW_PATTERN = re.compile(r"<tr[^>]*>.*?</tr>", re.DOTALL)
_CELL_PATTERN = re.compile(r"<t[hd][^>]*>.*?</t[hd]>", re.DOTALL)


def readme_html_tables(name: str) -> list[list[tuple[int, str]]]:
    """提取正文 HTML 表格为带 1 基行号的伪行序列。

    单元格剥除内联标签并以竖线拼接成伪行，供竖线拆分与锚点匹配复用；围栏
    代码块内的表格样本不计入。
    """
    text = _read_readme(name)
    fenced_lines = _fenced_line_numbers(text)
    tables: list[list[tuple[int, str]]] = []
    for block_match in _TABLE_PATTERN.finditer(text):
        if text.count("\n", 0, block_match.start()) + 1 in fenced_lines:
            continue
        rows: list[tuple[int, str]] = []
        for row_match in _ROW_PATTERN.finditer(block_match.group(0)):
            cells = [
                re.sub(r"\s+", " ", _TAG_PATTERN.sub("", cell)).strip()
                for cell in _CELL_PATTERN.findall(row_match.group(0))
            ]
            if cells:
                offset = block_match.start() + row_match.start()
                rows.append((text.count("\n", 0, offset) + 1, "|" + "|".join(cells) + "|"))
        if rows:
            tables.append(rows)
    return tables


def _row_cells(raw: str) -> list[str]:
    """拆分表格行为单元格序列，剥除首尾竖线与单元格两侧空白。"""
    stripped = raw.strip()
    inner = stripped[1:-1] if stripped.startswith("|") else stripped
    return [cell.strip() for cell in inner.split("|")]


# 能力差异表定位锚点，分辨率档位行的 "1K / 1.5K / 2K" 单元格为语言无关内容，全文唯一。
_CAPABILITY_TABLE_CELL_ANCHOR = "1K / 1.5K / 2K"


def _capability_table(name: str) -> list[tuple[int, str]]:
    """定位能力差异表，锚点为含 "1K / 1.5K / 2K" 单元格的唯一表格。"""
    candidates = [
        rows
        for rows in readme_html_tables(name)
        if any(_CAPABILITY_TABLE_CELL_ANCHOR in _row_cells(raw) for _, raw in rows)
    ]
    assert len(candidates) == 1, (
        f"{name} 能力差异表定位失败，含 {_CAPABILITY_TABLE_CELL_ANCHOR} 单元格的表格"
        f"应唯一命中，实际命中 {len(candidates)} 个"
    )
    return candidates[0]
