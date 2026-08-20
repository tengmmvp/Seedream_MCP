"""README 与代码的参数/能力/风格预设对账守护测试。

三语 README 之间的互等由 test_docs_consistency 锁定，本文件补上「文档对代码」
这一侧：工具参数 bullet 清单与 tools.core.schemas 输入模型的字段集全等，模型
能力差异表的档位/倍数/布尔/参考图上限与 MODEL_CAPABILITIES 派生值一致，风格
预设表与 server.py 的 @mcp.prompt 注册名单一致。以 README.md（简中版）为基准，
代码新增参数或改能力而文档未同步时在此变红，不再依赖三语互对的假绿。
"""

from __future__ import annotations

import re
from pathlib import Path

import seedream_mcp
from _readme_helpers import BASE_README, _fenced_blocks, _read_readme
from seedream_mcp.tools.core.schemas import (
    BrowseImagesInput,
    ImageToImageInput,
    MultiImageFusionInput,
    SequentialGenerationInput,
    TextToImageInput,
)
from seedream_mcp.utils.model.model_capabilities import MODEL_CAPABILITIES

# 工具名到输入模型的映射，镜像 server.py 平铺签名组装各工具时使用的输入模型。
_TOOL_INPUT_MODELS = {
    "text_to_image": TextToImageInput,
    "image_to_image": ImageToImageInput,
    "multi_image_fusion": MultiImageFusionInput,
    "sequential_generation": SequentialGenerationInput,
    "browse_images": BrowseImagesInput,
}

# 工具小节标题形态：<summary><b>1. <code>tool_name</code></b> — …</summary>
_TOOL_SUMMARY_PATTERN = re.compile(r"<b>\d+\.\s*<code>([a-z_]+)</code></b>")

# 工具参数 bullet 行形态：行首反引号参数名，与三语互对测试的提取口径一致。
_PARAM_BULLET_PATTERN = re.compile(r"^- `([A-Za-z_][A-Za-z0-9_]*)`")

# server.py 注册装饰器形态，工具与风格预设的注册名单都取源码装饰器为单一依据。
_TOOL_DECORATOR_PATTERN = re.compile(r'@mcp\.tool\(\s*name="([a-z_]+)"')
_PROMPT_DECORATOR_PATTERN = re.compile(r'@mcp\.prompt\(name="([^"]+)"')

# 能力差异表定位锚点：分辨率档位行的 "1K / 1.5K / 2K" 单元格全文唯一。
_CAPABILITY_TABLE_CELL_ANCHOR = "1K / 1.5K / 2K"

# 能力差异表数据列对应的模型家族，顺序镜像表头列序。
_CAPABILITY_COLUMN_FAMILIES = ("5.0-pro", "5.0-lite", "4.5", "4.0")

# 尺寸档位 token：形如 1K / 1.5K 的数字（可带小数）加大写 K。
_PRESET_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?K")

# 布尔能力行标签关键字到 ModelCapabilities 属性名的映射。
_CAPABILITY_BOOL_ROWS = {
    "组图生成": "supports_sequential_generation",
    "联网搜索": "supports_tools",
    "流式输出": "supports_stream",
    "输出格式": "supports_output_format",
    "图层拆分": "supports_layer_decomposition",
    "透明背景": "supports_background",
}

# 数值能力行的标签关键字，行内取值与能力表派生值对账。
_CAPABILITY_NUMBER_ROW_LABELS = ("分辨率档位", "参考图上限", "自定义尺寸倍数")

# 不参与对账的能力表行及理由。
_CAPABILITY_EXEMPT_ROWS = {
    "文生图": "全家族支持的基础生成能力，能力表无对应声明字段",
    "MCP 默认尺寸": "default_size=2K 档位经上游 API 解析的像素值，代码内无档位到像素映射",
}


def _server_source() -> str:
    """读取 server.py 源文本，注册名单以源码装饰器为单一依据不做导入。"""
    server_path = Path(seedream_mcp.__file__).resolve().parent / "server.py"
    return server_path.read_text(encoding="utf-8")


def _prose_lines(name: str) -> list[str]:
    """返回不在任何围栏代码块内的正文行。"""
    text = _read_readme(name)
    fenced = {
        lineno
        for block in _fenced_blocks(text)
        for lineno in range(block.line, block.line + len(block.lines) + 2)
    }
    return [raw for lineno, raw in enumerate(text.splitlines(), start=1) if lineno not in fenced]


def _tool_param_bullets(name: str) -> dict[str, list[str]]:
    """把各工具参数 bullet 组关联到其上方最近的工具小节标题。

    bullet 组由连续 bullet 行构成，组遇到非 bullet 行结束；出现在任何工具标题
    之前或同一工具出现多组时断言失败。
    """
    bullets: dict[str, list[str]] = {}
    current_tool: str | None = None
    group: list[str] = []

    def _flush() -> None:
        nonlocal group
        if not group:
            return
        assert current_tool is not None, f"参数 bullet 组出现在任何工具小节标题之前: {group}"
        assert current_tool not in bullets, f"工具 {current_tool} 出现多组参数 bullet: {group}"
        bullets[current_tool] = group
        group = []

    for raw in _prose_lines(name):
        summary = _TOOL_SUMMARY_PATTERN.search(raw)
        if summary is not None:
            _flush()
            current_tool = summary.group(1)
            continue
        bullet = _PARAM_BULLET_PATTERN.match(raw)
        if bullet is None:
            _flush()
            continue
        group.append(bullet.group(1))
    _flush()
    return bullets


def _row_cells(raw: str) -> list[str]:
    """拆分表格行为单元格序列，剥除首尾竖线与单元格两侧空白。"""
    stripped = raw.strip()
    inner = stripped[1:-1] if stripped.startswith("|") else stripped
    return [cell.strip() for cell in inner.split("|")]


def _tables(name: str) -> list[list[str]]:
    """把正文表格行按连续行分组为表，每表为行文本序列。"""
    tables: list[list[str]] = []
    current: list[str] = []
    for raw in _prose_lines(name):
        if raw.lstrip().startswith("|"):
            current.append(raw)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _capability_table(name: str) -> list[list[str]]:
    """定位模型能力差异表，锚点为含 "1K / 1.5K / 2K" 单元格的唯一表格。"""
    candidates = [
        rows
        for rows in _tables(name)
        if any(_CAPABILITY_TABLE_CELL_ANCHOR in _row_cells(raw) for raw in rows)
    ]
    assert len(candidates) == 1, (
        f"{name} 能力差异表定位失败，含 {_CAPABILITY_TABLE_CELL_ANCHOR} 单元格的表格"
        f"应唯一命中，实际命中 {len(candidates)} 个"
    )
    return candidates[0]


def _capability_row(name: str, label_keyword: str) -> list[str]:
    """按行首标签关键字定位能力差异表行，返回其单元格序列。"""
    for raw in _capability_table(name):
        cells = _row_cells(raw)
        if cells and label_keyword in cells[0]:
            return cells
    raise AssertionError(f"能力差异表未找到行首含 {label_keyword!r} 的行")


def _style_prompt_names(name: str) -> set[str]:
    """提取风格预设章节表格首列的反引号 Prompt 名称集合。"""
    in_section = False
    names: set[str] = set()
    for raw in _read_readme(name).splitlines():
        if raw.startswith("#"):
            in_section = "风格预设" in raw
            continue
        if not in_section:
            continue
        match = re.match(r"^\|\s*`([a-z_]+)`", raw)
        if match is not None:
            names.add(match.group(1))
    return names


def test_tool_param_bullets_exactly_match_input_model_fields() -> None:
    """各工具参数 bullet 清单与输入模型字段集全等，双向不得缺漏。

    方向上文档不得缺参数（模型新增字段而文档漏更即失败）；当前两侧精确相等，
    文档多列参数同样视为漂移，防止文档残留已删除的字段误导调用方。
    """
    bullets = _tool_param_bullets(BASE_README)
    assert set(bullets) == set(_TOOL_INPUT_MODELS), (
        f"README 工具小节 {sorted(bullets)} 与受守护输入模型的工具集 "
        f"{sorted(_TOOL_INPUT_MODELS)} 不一致"
    )
    for tool_name, model in _TOOL_INPUT_MODELS.items():
        documented = bullets[tool_name]
        model_fields = list(model.model_fields.keys())
        assert sorted(documented) == sorted(model_fields), (
            f"工具 {tool_name} 的 README 参数清单与 {model.__name__} 字段集漂移:\n"
            f"  仅文档有: {sorted(set(documented) - set(model_fields))}\n"
            f"  仅模型有: {sorted(set(model_fields) - set(documented))}"
        )


def test_readme_tool_sections_match_registered_tools() -> None:
    """README 工具小节集合与 server.py 的 @mcp.tool 注册名单一致。"""
    registered = set(_TOOL_DECORATOR_PATTERN.findall(_server_source()))
    assert set(_tool_param_bullets(BASE_README)) == registered, (
        f"README 工具小节与 server.py 注册工具漂移:\n"
        f"  仅文档有: {sorted(set(_tool_param_bullets(BASE_README)) - registered)}\n"
        f"  仅注册有: {sorted(registered - set(_tool_param_bullets(BASE_README)))}"
    )


def test_capability_table_resolution_presets_match_allowed_presets() -> None:
    """能力差异表分辨率档位行的各列 token 与 allowed_presets 一致。"""
    cells = _capability_row(BASE_README, "分辨率档位")
    for family, cell in zip(_CAPABILITY_COLUMN_FAMILIES, cells[1:]):
        documented = frozenset(_PRESET_TOKEN_PATTERN.findall(cell))
        expected = MODEL_CAPABILITIES[family].allowed_presets
        assert (
            documented == expected
        ), f"{family} 分辨率档位文档 {sorted(documented)} != 代码 {sorted(expected)}"


def test_capability_table_reference_image_limits_match() -> None:
    """能力差异表参考图上限行的各列数字与 max_reference_images 一致。"""
    cells = _capability_row(BASE_README, "参考图上限")
    for family, cell in zip(_CAPABILITY_COLUMN_FAMILIES, cells[1:]):
        numbers = re.findall(r"\d+", cell)
        assert len(numbers) == 1, f"{family} 参考图上限单元格应恰含一个数字: {cell!r}"
        assert int(numbers[0]) == MODEL_CAPABILITIES[family].max_reference_images, (
            f"{family} 参考图上限文档 {numbers[0]} != 代码 "
            f"{MODEL_CAPABILITIES[family].max_reference_images}"
        )


def test_capability_table_pixel_multiple_matches() -> None:
    """能力差异表自定义尺寸倍数行与 size_pixel_multiple 一致，不限制对应 None。"""
    cells = _capability_row(BASE_README, "自定义尺寸倍数")
    for family, cell in zip(_CAPABILITY_COLUMN_FAMILIES, cells[1:]):
        numbers = re.findall(r"\d+", cell)
        documented = int(numbers[0]) if numbers else None
        assert documented == MODEL_CAPABILITIES[family].size_pixel_multiple, (
            f"{family} 尺寸倍数文档 {documented} != 代码 "
            f"{MODEL_CAPABILITIES[family].size_pixel_multiple}"
        )


def test_capability_table_boolean_rows_match_capabilities() -> None:
    """能力差异表布尔行的 ✅/❌ 与对应能力声明字段一致。"""
    for label_keyword, attribute in _CAPABILITY_BOOL_ROWS.items():
        cells = _capability_row(BASE_README, label_keyword)
        for family, cell in zip(_CAPABILITY_COLUMN_FAMILIES, cells[1:]):
            marked_supported = "✅" in cell
            marked_unsupported = "❌" in cell
            assert (
                marked_supported != marked_unsupported
            ), f"{label_keyword} 行 {family} 列应恰含一个 ✅/❌ 标记: {cell!r}"
            assert marked_supported == getattr(MODEL_CAPABILITIES[family], attribute), (
                f"{label_keyword} 行 {family} 列文档 {marked_supported} != 代码 "
                f"{getattr(MODEL_CAPABILITIES[family], attribute)}"
            )


def test_capability_table_rows_are_triaged() -> None:
    """能力差异表每行都须纳入对账或豁免，新增行未归类即失败。"""
    checked = set(_CAPABILITY_BOOL_ROWS) | set(_CAPABILITY_NUMBER_ROW_LABELS)
    known = checked | set(_CAPABILITY_EXEMPT_ROWS)
    untriaged: list[str] = []
    # 首行为表头，不入对账；纯短横线行为列对齐分隔行，同样跳过。
    for raw in _capability_table(BASE_README)[1:]:
        label = _row_cells(raw)[0]
        if set(label) <= set("-: "):
            continue
        if not any(keyword in label for keyword in known):
            untriaged.append(label)
    assert not untriaged, f"能力差异表行未纳入对账或豁免: {untriaged}"


def test_style_preset_table_matches_registered_prompts() -> None:
    """风格预设表首列 Prompt 名称与 server.py 的 @mcp.prompt 注册名单一致。"""
    registered = set(_PROMPT_DECORATOR_PATTERN.findall(_server_source()))
    documented = _style_prompt_names(BASE_README)
    assert documented == registered, (
        f"风格预设表与 server.py 注册 Prompt 漂移:\n"
        f"  仅文档有: {sorted(documented - registered)}\n"
        f"  仅注册有: {sorted(registered - documented)}"
    )
