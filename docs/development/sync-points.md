# 新增模型 / 工具 / 能力维度同步点清单

三类扩展各自必须同步的全部落点。多数落点有守护测试，漏改在 CI 变红；标注「无守护」的落点只能靠本清单人工核对。模型能力与参数约束以 `docs/vendor/volcengine/` 官方文档为准，官方文档的同步提交须逐类核对下列落点。仓库组织与命名规范见 repo-layout.md。

## 新增模型

- `seedream_mcp/utils/model/capabilities.py`：`MODEL_ALIASES` 别名映射、`_MODEL_FAMILY_TOKENS` 家族解析 token（Pro/Flash 行须先于 Lite 行，否则被 Lite 子串误吸）、`MODEL_CAPABILITIES` 能力表（`unknown` 兜底行保持全放行，不随新模型收窄）。
- `seedream_mcp/server/cli.py`：`--model` choices 从 `MODEL_ALIASES` 派生，守护见 test_server_transport_options。
- `.env.example`：`SEEDREAM_MODEL_ID` 可选清单与 `SEEDREAM_DEFAULT_SIZE` 的家族档位注释，守护见 test_env_example_guard。
- README 三语：`--model` 行别名清单（test_env_example_guard 对账）、模型能力差异表加列（test_readme_code_parity 列数断言）、模型价格表加行（test_docs_consistency 价格表守护）、默认模型与切换提示 prose。
- `seedream_mcp/skills/seedream-image-generation/`：SKILL.md 与 references 的模型清单叙述（无守护）。
- 机器契约文案中的家族枚举：`supported_family_display_names`、`limited_reference_families_annotation` 等派生处随能力表自动更新，工具描述等硬编码处须人工核对（无守护）。
- 测试：test_model_capabilities 家族解析、test_model_alias_normalization 别名归一、test_config_model_selection 默认值。
- 本地评估集（仓库外）：以新模型跑一遍生成质量与能力回归。

## 新增工具

- `seedream_mcp/tools/schemas.py`：输入模型，参数校验的单一来源；计数受限的图片字段经 multi_image_field_type/sequential_image_field_type 字段类型工厂单源挂载（平铺签名与输入模型两侧共用），新增时同步扩充 test_image_count_validator_shared_by_flat_signature_and_model 参数表。
- `seedream_mcp/tools/handlers/<tool>.py`：处理器；生成类工具的 `_execute` 闭包须接线全部公共 kwargs。
- `seedream_mcp/tools/runners.py` 与 `seedream_mcp/tools/__init__.py`：适配器组装与包门面延迟导出映射。
- `seedream_mcp/server/_registration.py`：平铺参数镜像签名（test_tool_parameter_order 锁定与输入模型等价）、描述与 title、`_run_tool_pipeline` 的 tool_name 传 definitions/conventions 常量（S8 禁字面量）、带参工具的 `workspace_roots: _ROOTS_DEPENDENCY` 依赖。
- `seedream_mcp/tools/outputs/__init__.py`：structured output 模型（生成类工具复用 `GenerationStructuredOutput`）与 `TOOL_OUTPUT_MODELS` 注册（守护见 test_tool_output_models_keys_equal_registered_tool_names、test_output_schema）。
- `seedream_mcp/server/_icons.py`：SVG 与 PNG 双图标表（test_tool_icons 锁定键集与注册名单一致）。
- `seedream_mcp/client/_api.py`：API 方法与 docstring 提示文案（无守护）。
- Web 端接线（仅生成类工具）：`seedream_mcp/webapp/generate.py` 端点与 `static/js` 前端调用（无守护）。
- README 三语：工具小节与参数 bullet（test_readme_code_parity 对账输入模型字段集）。
- `tests/_lib/no_param_tools.py`：无参工具名单。

## 新增能力维度（ModelCapabilities 字段）

- `seedream_mcp/utils/model/capabilities.py`：`ModelCapabilities` 字段声明与各家族取值，明确 `unknown` 兜底行的取值口径。
- `seedream_mcp/tools/handlers/get_model_info.py`：`build_model_info_payload` 的 capabilities 键；布尔开关补 `capabilities.py` 的 `CAPABILITY_LABELS` 标签行（门控报错文案共用同一表），test_model_info_tool 锁定键集一致。
- `seedream_mcp/server/_registration.py` get_model_info 工具描述：经 build_model_info_description 从 get_model_info.py 的 `_NUMERIC_FIELD_LABELS` 与 capabilities.py 的 `CAPABILITY_LABELS` 标签表派生，新增能力维度补标签行即自动同步；test_get_model_info_description_matches_locked_text 逐字锁定，标签变化时变红提醒人工确认新描述文本。
- README 三语能力差异表加行，并在 test_readme_code_parity 的行分类（布尔 / 值 / 数值 / 豁免）中登记，未分类行由 test_capability_table_rows_are_triaged 拒绝。
- 校验与配置消费方：`utils/core/validators.py`、`config/_model.py` 需要引用新维度时同步。
- 本地评估集（仓库外）：覆盖新维度的开关两态各回归一遍。
