"""三语 README 一致性守护测试。

README.md、README.en.md、README.zh-TW.md 是同一份文档的三种语言版本。项目不为
三语文档建单源渲染系统，改由本文件把跨语言漂移从人工同步变成测试强制：任一语
言单独修改而未同步其余两份，对应断言立即变红。

配对策略：围栏代码块按出现顺序配对，先断言数量相等，再逐块比较第 N 块；基准
版本为 README.md，其余两份与基准对齐。断言只比较语言无关要素，如 JSON 块全文、
bash 块内的 KEY=value 赋值与 CLI 旗标 token、环境变量键序、工具参数 bullet 列表
的参数名序列、标题层级、链接 URL、表格列数与能力差异表的数字 token 序列，不比
较自然语言正文。定位环境变量配置块时以含 SEEDREAM_MODEL_ID 赋值行的 bash 块为
锚点，定位能力差异表时以含 "1K / 1.5K / 2K" 单元格的表格为锚点，均不依赖各语言
的章节标题文字。围栏块解析与配置块定位的共享实现位于 _readme_helpers。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TypeVar

from _readme_helpers import BASE_README, CodeBlock, _env_block, _lang_blocks, _read_readme

OTHER_READMES = ("README.en.md", "README.zh-TW.md")

# bash 块内 KEY=value 形态的赋值提取。值取等号后到首个空白前的片段并剥除尾部
# 标点，可为空串，避免句子收尾的右括号等标点在不同语言注释中的附着差异造成误
# 报；注释行中的赋值同样提取，用于捕捉译文注释里漂移的键名与取值。
_ASSIGNMENT_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)=(\S*)")
_VALUE_TRAILING_PUNCTUATION = ")]}.,;:!?"

# CLI 旗标 token 提取后的行尾标点剥除集合，与赋值取值的剥除口径一致。
_FLAG_TRAILING_PUNCTUATION = ")]}.,;:!?"

# 环境变量键名形态，与 .env.example 守护测试的口径一致。
_ENV_KEY_PATTERN = re.compile(r"\b(?:SEEDREAM|ARK)_[A-Z0-9_]+")
_ENV_KEY_FULL_PATTERN = re.compile(r"(?:SEEDREAM|ARK)_[A-Z0-9_]+\Z")

# 正文中的 markdown 链接与 HTML 链接属性两类 URL 提取。
_MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_HTML_LINK_PATTERN = re.compile(r'(?:href|src)="([^"]+)"')

# ATX 标题行，井号序列后须跟空白。
_HEADING_PATTERN = re.compile(r"^(#{1,6})(?=\s)")

# 工具参数 bullet 行形态：行首反引号包裹的参数名，参数名本身语言无关。
_PARAM_BULLET_PATTERN = re.compile(r"^- `([A-Za-z_][A-Za-z0-9_]*)`")

_T = TypeVar("_T")


def _prose_lines(name: str) -> list[tuple[int, str]]:
    """返回不在任何围栏代码块内的正文行，带 1 基行号。"""
    result: list[tuple[int, str]] = []
    in_code = False
    for lineno, raw in enumerate(_read_readme(name).splitlines(), start=1):
        if raw.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            result.append((lineno, raw))
    return result


def _block_assignments(block: CodeBlock) -> list[tuple[str, str]]:
    """提取块内全部 KEY=value 赋值，按行序与行内出现顺序展开为扁平序列。"""
    pairs: list[tuple[str, str]] = []
    for line in block.lines:
        for key, value in _ASSIGNMENT_PATTERN.findall(line):
            pairs.append((key, value.rstrip(_VALUE_TRAILING_PUNCTUATION)))
    return pairs


def _block_flag_tokens(block: CodeBlock) -> list[str]:
    """提取块内全部 CLI 旗标 token，按行序与行内出现顺序展开为扁平序列。

    旗标为以 -- 开头的 token，剥除附着在译文注释里的行尾标点；旗标名与默认值
    提示同为语言无关要素，启动参数清单与示例命令的单语漂移由序列比较暴露。
    """
    flags: list[str] = []
    for line in block.lines:
        for token in line.split():
            if token.startswith("--"):
                flags.append(token.rstrip(_FLAG_TRAILING_PUNCTUATION))
    return flags


def _param_bullet_groups(name: str) -> list[tuple[int, list[str]]]:
    """把正文工具参数 bullet 行按连续行分组，每组的组首行号与参数名序列。

    bullet 仅出现于正文而非围栏代码块内；分组按连续 bullet 行切分，各工具的
    参数列表彼此隔离，新增、删除或调序参数而未三语同步时序列先行失配。
    """
    groups: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start_line = 0
    for lineno, raw in _prose_lines(name):
        match = _PARAM_BULLET_PATTERN.match(raw)
        if match is None:
            if current:
                groups.append((start_line, current))
                current = []
            continue
        if not current:
            start_line = lineno
        current.append(match.group(1))
    if current:
        groups.append((start_line, current))
    return groups


def _env_block_keys(name: str) -> list[str]:
    """环境变量配置块内出现的全部 SEEDREAM_/ARK_ 键名，含注释行，按出现顺序。"""
    keys: list[str] = []
    for line in _env_block(name).lines:
        keys.extend(_ENV_KEY_PATTERN.findall(line))
    return keys


def _env_block_pairs(name: str) -> list[tuple[str, str]]:
    """环境变量配置块内环境变量键的赋值对，默认数值经键值对成对锁定。"""
    return [
        (key, value)
        for key, value in _block_assignments(_env_block(name))
        if _ENV_KEY_FULL_PATTERN.match(key)
    ]


def _heading_depths(name: str) -> list[tuple[int, int]]:
    """正文标题行的行号与层级序列。"""
    depths: list[tuple[int, int]] = []
    for lineno, raw in _prose_lines(name):
        match = _HEADING_PATTERN.match(raw)
        if match is not None:
            depths.append((lineno, len(match.group(1))))
    return depths


def _link_urls(name: str) -> set[str]:
    """正文链接 URL 集合，markdown 链接与 HTML href/src 都计入。"""
    urls: set[str] = set()
    for _, raw in _prose_lines(name):
        urls.update(_MARKDOWN_LINK_PATTERN.findall(raw))
        urls.update(_HTML_LINK_PATTERN.findall(raw))
    return urls


def _table_columns(name: str) -> list[tuple[int, int]]:
    """正文表格行的行号与列数序列，列数按行内竖线数减一计算。"""
    columns: list[tuple[int, int]] = []
    for lineno, raw in _prose_lines(name):
        if raw.lstrip().startswith("|"):
            columns.append((lineno, raw.count("|") - 1))
    return columns


# 能力差异表定位锚点，分辨率档位行的 "1K / 1.5K / 2K" 单元格为语言无关内容，全文唯一。
_CAPABILITY_TABLE_CELL_ANCHOR = "1K / 1.5K / 2K"

# 单元格内数字 token 提取，尺寸档位、倍数、像素值与参考图上限等取值均为数字。
_NUMBER_TOKEN_PATTERN = re.compile(r"\d+")


def _row_cells(raw: str) -> list[str]:
    """拆分表格行为单元格序列，剥除首尾竖线与单元格两侧空白。"""
    stripped = raw.strip()
    inner = stripped[1:-1] if stripped.startswith("|") else stripped
    return [cell.strip() for cell in inner.split("|")]


def _tables(name: str) -> list[list[tuple[int, str]]]:
    """把正文表格行按连续行分组为表，每表为带 1 基行号的行序列。"""
    tables: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for entry in _prose_lines(name):
        if entry[1].lstrip().startswith("|"):
            current.append(entry)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _capability_table(name: str) -> list[tuple[int, str]]:
    """定位能力差异表，锚点为含 "1K / 1.5K / 2K" 单元格的唯一表格。"""
    candidates = [
        rows
        for rows in _tables(name)
        if any(_CAPABILITY_TABLE_CELL_ANCHOR in _row_cells(raw) for _, raw in rows)
    ]
    assert len(candidates) == 1, (
        f"{name} 能力差异表定位失败，含 {_CAPABILITY_TABLE_CELL_ANCHOR} 单元格的表格"
        f"应唯一命中，实际命中 {len(candidates)} 个"
    )
    return candidates[0]


def _row_number_tokens(raw: str) -> list[str]:
    """按单元格顺序提取行内全部数字 token，构成语言无关的取值指纹。

    "1K / 2K" 展开为 ["1", "2"]，"10 张" 展开为 ["10"]，"2048x2048" 展开为
    ["2048", "2048"]；纯文字单元格贡献空序列，不影响行对齐。
    """
    tokens: list[str] = []
    for cell in _row_cells(raw):
        tokens.extend(_NUMBER_TOKEN_PATTERN.findall(cell))
    return tokens


def _first_mismatch_index(base: Sequence[_T], other: Sequence[_T]) -> int:
    """返回两序列首个差异位置，互为前缀时返回较短者长度。"""
    for index in range(min(len(base), len(other))):
        if base[index] != other[index]:
            return index
    return min(len(base), len(other))


def _block_drift_message(ordinal: int, base: CodeBlock, other_name: str, other: CodeBlock) -> str:
    """构造代码块漂移的失败消息，定位到文件、块序与首个差异行。"""
    base_detail = "缺失"
    other_detail = "缺失"
    for offset in range(max(len(base.lines), len(other.lines))):
        base_line = base.lines[offset] if offset < len(base.lines) else "缺失"
        other_line = other.lines[offset] if offset < len(other.lines) else "缺失"
        if base_line != other_line:
            base_detail = f"第 {base.line + 1 + offset} 行「{base_line.strip()}」"
            other_detail = f"第 {other.line + 1 + offset} 行「{other_line.strip()}」"
            break
    return (
        f"{other_name} 第 {other.line} 行起的第 {ordinal} 个 {base.lang} 代码块与 "
        f"{BASE_README} 第 {base.line} 行起的同序块漂移:\n"
        f"  {BASE_README} {base_detail}\n"
        f"  {other_name} {other_detail}"
    )


def test_json_blocks_are_identical_across_languages() -> None:
    """三语全部 json 代码块逐字一致，含工具调用示例与客户端配置。

    JSON 示例语言无关，块内字符串值三语共用同一份内容；某语言单独修改示例而
    未同步其余两份时，首个差异行消息直接指明漂移的文件与块。
    """
    base_blocks = _lang_blocks(BASE_README, "json")
    assert base_blocks, "README.md 应存在 json 代码块，围栏解析失效或文档被清空"

    for name in OTHER_READMES:
        other_blocks = _lang_blocks(name, "json")
        assert len(other_blocks) == len(base_blocks), (
            f"{name} 的 json 代码块数量为 {len(other_blocks)}，{BASE_README} 为 "
            f"{len(base_blocks)}，存在单语增删的示例块"
        )
        for ordinal, (base, other) in enumerate(zip(base_blocks, other_blocks), start=1):
            assert other.lines == base.lines, _block_drift_message(ordinal, base, name, other)


def test_bash_blocks_share_assignments_across_languages() -> None:
    """三语全部 bash 代码块内 KEY=value 赋值序列一致。

    提取按行内 KEY= 形态进行，注释行同样参与，允许注释正文的语言差异；赋值序
    列覆盖环境变量清单、示例命令与译文注释中出现的键名与取值。
    """
    base_blocks = _lang_blocks(BASE_README, "bash")
    assert base_blocks, "README.md 应存在 bash 代码块，围栏解析失效或文档被清空"

    for name in OTHER_READMES:
        other_blocks = _lang_blocks(name, "bash")
        assert len(other_blocks) == len(base_blocks), (
            f"{name} 的 bash 代码块数量为 {len(other_blocks)}，{BASE_README} 为 "
            f"{len(base_blocks)}，存在单语增删的命令块"
        )
        for ordinal, (base, other) in enumerate(zip(base_blocks, other_blocks), start=1):
            base_pairs = _block_assignments(base)
            other_pairs = _block_assignments(other)
            assert other_pairs == base_pairs, (
                f"{name} 第 {other.line} 行起的第 {ordinal} 个 bash 块赋值序列与 "
                f"{BASE_README} 第 {base.line} 行起的同序块漂移:\n"
                f"  {BASE_README}: {base_pairs}\n"
                f"  {name}: {other_pairs}"
            )


def test_bash_blocks_share_cli_flag_tokens_across_languages() -> None:
    """三语全部 bash 代码块内 CLI 旗标 token 序列一致。

    启动参数清单与示例命令中的旗标为语言无关要素，按块配对比较旗标序列；某语言
    单独增删旗标、改名或调序时，首个差异块的消息指明漂移的文件与块序。
    """
    base_blocks = _lang_blocks(BASE_README, "bash")
    assert base_blocks, "README.md 应存在 bash 代码块，围栏解析失效或文档被清空"
    assert any(
        _block_flag_tokens(block) for block in base_blocks
    ), "bash 代码块未提取到任何 CLI 旗标 token，旗标解析失效"

    for name in OTHER_READMES:
        other_blocks = _lang_blocks(name, "bash")
        assert len(other_blocks) == len(base_blocks), (
            f"{name} 的 bash 代码块数量为 {len(other_blocks)}，{BASE_README} 为 "
            f"{len(base_blocks)}，存在单语增删的命令块"
        )
        for ordinal, (base, other) in enumerate(zip(base_blocks, other_blocks), start=1):
            base_flags = _block_flag_tokens(base)
            other_flags = _block_flag_tokens(other)
            assert other_flags == base_flags, (
                f"{name} 第 {other.line} 行起的第 {ordinal} 个 bash 块旗标序列与 "
                f"{BASE_README} 第 {base.line} 行起的同序块漂移:\n"
                f"  {BASE_README}: {base_flags}\n"
                f"  {name}: {other_flags}"
            )


def test_tool_param_bullet_groups_match() -> None:
    """各工具参数 bullet 列表的参数名序列三语一致。

    五个工具的参数清单以行首反引号参数名的 bullet 列表表达，参数名与列表分组
    均为语言无关要素；某语言单独增删参数 bullet 或拆并列表时，分组序列先行失配，
    失败消息定位两份文件的差异分组行号。
    """
    base_groups = _param_bullet_groups(BASE_README)
    assert base_groups, "未提取到任何工具参数 bullet 列表，bullet 解析失效或文档被清空"

    for name in OTHER_READMES:
        other_groups = _param_bullet_groups(name)
        assert len(other_groups) == len(base_groups), (
            f"{name} 的参数 bullet 列表为 {len(other_groups)} 组，{BASE_README} 为 "
            f"{len(base_groups)} 组，存在单语增删的参数列表或分组漂移"
        )
        for ordinal, ((base_line, base_names), (other_line, other_names)) in enumerate(
            zip(base_groups, other_groups), start=1
        ):
            assert other_names == base_names, (
                f"{name} 第 {other_line} 行起的第 {ordinal} 组参数 bullet 序列与 "
                f"{BASE_README} 第 {base_line} 行起的同序组漂移:\n"
                f"  {BASE_README}: {base_names}\n"
                f"  {name}: {other_names}"
            )


def test_env_block_key_sequence_matches() -> None:
    """环境变量配置块的键名序列三语一致，含注释行中出现的键。

    键按首次出现顺序展开为扁平序列比较，同时锁定集合与顺序；新增、删除或移动
    环境变量而未三语同步时失败。
    """
    base_keys = _env_block_keys(BASE_README)
    assert base_keys, "环境变量配置块未提取到任何键，锚点定位或解析失效"

    for name in OTHER_READMES:
        other_keys = _env_block_keys(name)
        assert other_keys == base_keys, (
            f"{name} 环境变量配置块的键序列与 {BASE_README} 漂移:\n"
            f"  {BASE_README}: {base_keys}\n"
            f"  {name}: {other_keys}"
        )


def test_env_block_default_values_match() -> None:
    """环境变量配置块的赋值键值对三语一致，默认值数值成对相等。

    端口、字节上限、天数等默认值经键值对逐对比较，67108864、268435456、
    10737418240、30 等任一语言漂移即失败。
    """
    base_pairs = _env_block_pairs(BASE_README)
    assert base_pairs, "环境变量配置块未提取到任何赋值对，解析失效"

    for name in OTHER_READMES:
        other_pairs = _env_block_pairs(name)
        assert other_pairs == base_pairs, (
            f"{name} 环境变量配置块的赋值键值对与 {BASE_README} 漂移:\n"
            f"  {BASE_README}: {base_pairs}\n"
            f"  {name}: {other_pairs}"
        )


def test_heading_depth_sequence_matches() -> None:
    """标题层级序列三语一致，忽略标题文本只比较深度。

    某一语言新增章节或调整层级而未同步时，深度序列先行失配，失败消息给出首个
    差异标题在两份文件中的行号。
    """
    base_headings = _heading_depths(BASE_README)
    assert base_headings, "未提取到任何标题行，解析失效"
    base_depths = [depth for _, depth in base_headings]

    for name in OTHER_READMES:
        other_headings = _heading_depths(name)
        other_depths = [depth for _, depth in other_headings]
        if other_depths == base_depths:
            continue
        mismatch = _first_mismatch_index(base_depths, other_depths)
        base_entry = base_headings[mismatch] if mismatch < len(base_headings) else base_headings[-1]
        other_entry = (
            other_headings[mismatch] if mismatch < len(other_headings) else other_headings[-1]
        )
        assert other_depths == base_depths, (
            f"{name} 的标题层级序列与 {BASE_README} 漂移，数量 {len(other_depths)} 对 "
            f"{len(base_depths)}，首个差异在第 {mismatch + 1} 个标题: "
            f"{BASE_README} 第 {base_entry[0]} 行层级 {base_entry[1]}，"
            f"{name} 第 {other_entry[0]} 行层级 {other_entry[1]}"
        )


def test_link_url_sets_match() -> None:
    """正文链接 URL 集合三语一致，markdown 链接与 HTML href/src 都计入。

    代码块内的 URL 不参与比较；导航链接、徽章图与参考文档相对链接三语共享同
    一份集合，单语增删链接即失败。
    """
    base_urls = _link_urls(BASE_README)
    assert base_urls, "未提取到任何链接，解析失效"

    for name in OTHER_READMES:
        other_urls = _link_urls(name)
        assert other_urls == base_urls, (
            f"{name} 的链接 URL 集合与 {BASE_README} 漂移:\n"
            f"  仅 {BASE_README} 有: {sorted(base_urls - other_urls)}\n"
            f"  仅 {name} 有: {sorted(other_urls - base_urls)}"
        )


def test_table_column_sequence_matches() -> None:
    """表格列数序列三语一致，覆盖模型能力差异等数据表。

    某一语言增删数据列或整表而未同步时，列数序列先行失配，失败消息给出首个差
    异表格行在两份文件中的行号。
    """
    base_columns = _table_columns(BASE_README)
    assert base_columns, "未提取到任何表格行，解析失效"

    for name in OTHER_READMES:
        other_columns = _table_columns(name)
        if other_columns == base_columns:
            continue
        mismatch = _first_mismatch_index(base_columns, other_columns)
        base_entry = base_columns[mismatch] if mismatch < len(base_columns) else base_columns[-1]
        other_entry = (
            other_columns[mismatch] if mismatch < len(other_columns) else other_columns[-1]
        )
        assert other_columns == base_columns, (
            f"{name} 的表格列数序列与 {BASE_README} 漂移，行数 {len(other_columns)} 对 "
            f"{len(base_columns)}，首个差异在第 {mismatch + 1} 个表格行: "
            f"{BASE_README} 第 {base_entry[0]} 行 {base_entry[1]} 列，"
            f"{name} 第 {other_entry[0]} 行 {other_entry[1]} 列"
        )


def test_capability_table_number_tokens_match() -> None:
    """能力差异表逐行数字 token 序列三语一致，单元格取值漂移即失败。

    列数守护只锁定表格结构，单元格取值不在其比对范围，任一语言单独修改数值
    不会变红。数字 token 为语言无关要素，按行成序列比较即可覆盖尺寸档位、
    倍数、默认像素与参考图上限等取值；失败消息定位两份文件的差异行号。
    """
    base_rows = _capability_table(BASE_README)
    base_tokens = [_row_number_tokens(raw) for _, raw in base_rows]
    assert any(base_tokens), "能力差异表未提取到任何数字 token，定位或解析失效"

    for name in OTHER_READMES:
        other_rows = _capability_table(name)
        assert len(other_rows) == len(base_rows), (
            f"{name} 能力差异表为 {len(other_rows)} 行，{BASE_README} 为 "
            f"{len(base_rows)} 行，存在单语增删的表格行"
        )
        for (base_lineno, base_raw), (other_lineno, other_raw) in zip(base_rows, other_rows):
            base_row_tokens = _row_number_tokens(base_raw)
            other_row_tokens = _row_number_tokens(other_raw)
            assert other_row_tokens == base_row_tokens, (
                f"{name} 第 {other_lineno} 行「{other_raw.strip()}」的数字 token 序列与 "
                f"{BASE_README} 第 {base_lineno} 行「{base_raw.strip()}」漂移:\n"
                f"  {BASE_README}: {base_row_tokens}\n"
                f"  {name}: {other_row_tokens}"
            )
