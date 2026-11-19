# 同步点清单

| 项           | 值                                                                                                                    |
| ------------ | --------------------------------------------------------------------------------------------------------------------- |
| **版本**     | v1.2                                                                                                                  |
| **适用范围** | 新增模型、新增工具、新增能力维度、新增或重排参数四类扩展的落点核对                                                    |
| **行文**     | 遵循 `docs/development/writing-guide.md`                                                                              |
| **分工**     | 同步点清单归本文档；参数顺序归 `parameter-order.md`；仓库组织与命名归 `repo-layout.md`；文档写作归 `writing-guide.md` |

本清单回答一个问题：四类扩展各自有哪些落点，漏改哪一处会坏。落点清单随代码漂移，置于附录 A–D；正文只留同步规则与登记流程。

| 要做的事       | 先读   | 落点清单 |
| -------------- | ------ | -------- |
| 新增模型       | K1、K3 | 附录 A   |
| 新增工具       | K1     | 附录 B   |
| 新增能力维度   | K1     | 附录 C   |
| 新增或重排参数 | K1、K4 | 附录 D   |

## K1 落点同步

**意图**：四类扩展的每次变更不遗漏任何落点。

**规则**：每类扩展的全部落点登记在对应附录。落点分两档：有守护测试的，漏改在 CI 变红；标注「无守护，人工核对」的，仅靠本清单核对。执行扩展任务时按任务入口表取附录，改一项勾一项。

**机器执行**：各附录条目标注的守护测试。

**参见**：K3（官方文档同步提交的核对）

## K2 落点登记

**意图**：新落点不因未登记而漏同步。

**规则**：新增文件或改动开始承担落点职责（校验、呈现、注册、接线）时，随同一变更登记进对应附录，登记行标注守护档位；落点废弃时删除条目，并在变更记录记要点。

## K3 官方文档同步提交

**意图**：官方文档更新引入的能力变化不因只看表面类别而漏核对。

**规则**：模型能力与参数约束以 `docs/vendor/volcengine/` 官方文档为准。官方文档的同步提交按变更类别对照附录 A–D 逐项核对——同步提交常含模型、链接、示例、文案多类变更，不得只核对模型新增一类。

**参见**：`parameter-order.md` O5（官方参数的归位方式）

## K4 参数顺序落点

**意图**：顺序判据与落点清单各守单一来源。

**规则**：参数顺序的归位流程与判据归 `parameter-order.md`（O1–O8），本清单附录 D 只登记落点；附录 A–C 中与顺序相关的条目引用附录 D，不复述。

## 附录 A 新增模型落点

- `seedream_mcp/utils/model/capabilities.py`：`MODEL_ALIASES` 别名映射、`_MODEL_FAMILY_TOKENS` 家族解析 token（Pro/Flash 行须先于 Lite 行，否则被 Lite 子串误吸）、`MODEL_CAPABILITIES` 能力表（`unknown` 兜底行保持全放行，不随新模型收窄）。
- `seedream_mcp/server/cli.py`：`--model` choices 从 `MODEL_ALIASES` 派生。守护：test_server_transport_options。
- `.env.example`：`SEEDREAM_MODEL_ID` 可选清单与 `SEEDREAM_DEFAULT_SIZE` 的家族档位注释。守护：test_env_example_guard。
- README 三语：`--model` 行别名清单（守护：test_env_example_guard 对账）、模型能力差异表加列（守护：test_readme_code_parity 列数断言）、模型价格表加行（守护：test_docs_consistency 价格表守护）、默认模型与切换提示 prose（无守护，人工核对）。
- `seedream_mcp/skills/seedream-image-generation/`：SKILL.md 与 references 的模型清单叙述。无守护，人工核对。
- 机器契约文案中的家族枚举：`supported_family_display_names`、`limited_reference_families_annotation` 等派生处随能力表自动更新；工具描述等硬编码处人工核对。无守护。
- 测试：test_model_capabilities 家族解析、test_model_alias_normalization 别名归一、test_config_model_selection 默认值。
- 本地评估集（仓库外）：以新模型跑一遍生成质量与能力回归。

## 附录 B 新增工具落点

- `seedream_mcp/tools/schemas.py`：输入模型，参数校验的单一来源；计数受限的图片字段以共享计数常量（前置校验与数量界）组合为两侧字面量别名单源挂载（平铺签名与输入模型共用同一常量）。守护：test_image_count_validator_shared_by_flat_signature_and_model 参数表同步扩充。
- `seedream_mcp/tools/schemas.py` 的生成输入场景声明：图片形态经 image mixin 或模型自身的 `reference_images` 访问器声明（context 构建消费，漏声明即 AttributeError）；单请求产图数最坏投影经 `worst_case_projection` 类方法随 option mixin 派发（Web 满档等待预算消费）。守护：test_reference_images_declared_for_every_registered_tool、test_worst_case_projection_dominates_projection_domain。
- `seedream_mcp/tools/schemas.py` 的 `GENERATION_INPUT_MODELS` 注册元组：Web 等待预算最坏候选按全量派生，新增生成工具须登记。守护：test_generation_input_models_registry_matches_five_tools 名单对账。
- `seedream_mcp/tools/handlers/<tool>.py`：处理器；生成类工具的 `_execute` 闭包须接线全部公共 kwargs。
- `seedream_mcp/tools/runners.py` 与 `seedream_mcp/tools/__init__.py`：适配器组装与包门面延迟导出映射。
- `seedream_mcp/server/_registration.py`：平铺参数镜像签名（守护：test_tool_parameter_order 锁定与输入模型等价）；描述与 title；`_run_tool_pipeline` 的 tool_name 传 definitions/conventions 常量（S8 禁字面量）；带参工具的 `workspace_roots: _ROOTS_DEPENDENCY` 依赖。
- `seedream_mcp/tools/outputs/__init__.py`：structured output 模型（生成类工具复用 `GenerationStructuredOutput`）与 `TOOL_OUTPUT_MODELS` 注册。守护：test_tool_output_models_keys_equal_registered_tool_names、test_output_schema。
- `seedream_mcp/server/_icons.py`：SVG 与 PNG 双图标表。守护：test_tool_icons 锁定键集与注册名单一致。
- `seedream_mcp/client/_api.py`：API 方法与 docstring 提示文案。无守护，人工核对。
- Web 端接线（仅生成类工具）：`seedream_mcp/webapp/generate.py` 端点与 `static/js` 前端调用。无守护，人工核对。
- README 三语：工具小节与参数 bullet。守护：test_readme_code_parity 对账输入模型字段集。
- `tests/_lib/no_param_tools.py`：无参工具名单。

## 附录 C 新增能力维度落点（ModelCapabilities 字段）

- `seedream_mcp/utils/model/capabilities.py`：`ModelCapabilities` 字段声明与各家族取值，明确 `unknown` 兜底行的取值口径。
- `seedream_mcp/tools/handlers/get_model_info.py`：`build_model_info_payload` 的 capabilities 键；布尔开关补 `capabilities.py` 的 `CAPABILITY_LABELS` 标签行（门控报错文案共用同一表）。守护：test_model_info_tool 锁定键集一致。
- `seedream_mcp/server/_registration.py` 的 get_model_info 工具描述：经 build_model_info_description 从 get_model_info.py 的 `_VALUE_FIELD_LABELS` 与 capabilities.py 的 `CAPABILITY_LABELS` 标签表派生，新增能力维度补标签行即自动同步。守护：test_get_model_info_description_matches_locked_text 逐字锁定，标签变化时变红提醒人工确认新描述文本。
- README 三语能力差异表加行，并在 test_readme_code_parity 的行分类（布尔 / 值 / 数值 / 豁免）中登记；未分类行由 test_capability_table_rows_are_triaged 拒绝。
- 校验与配置消费方：`utils/core/validators.py`、`config/_model.py` 需要引用新维度时同步。
- 本地评估集（仓库外）：覆盖新维度的开关两态各回归一遍。

## 附录 D 参数顺序落点

- `seedream_mcp/tools/schemas.py`：输入模型字段序，经 mixin 继承列表的逆序实现（排列规则见 `parameter-order.md` O8）。
- `seedream_mcp/tools/schemas.py` 的 `GenerationInputParams` 协议：成员序随共享字段集与字段序同步（单类体直接声明，不经 mixin）。无守护，人工核对。
- `seedream_mcp/server/_registration.py`：平铺签名参数序（条目见附录 B；守护：test_tool_parameter_order 锁定等价）。
- `seedream_mcp/tools/pipeline/context.py`：`GenerationExecutionContext` 字段序。守护：test_generation_context。
- `seedream_mcp/tools/handlers/browse_images.py`：`_BrowseRequestState` 字段序（浏览工具输入派生参数序，`parameter-order.md` O6 投影）。无守护，人工核对。
- `seedream_mcp/tools/outputs/__init__.py`：structuredContent 回显参数序。无守护，人工核对。
- `seedream_mcp/tools/pipeline/results.py`：structuredContent 回显的 payload 装配清单序，随 `GenerationStructuredOutput` 字段序。无守护，人工核对。
- `seedream_mcp/tools/handlers/<tool>.py`：`_execute` 调用 client 方法的 kwargs 序随 client 方法签名。无守护，人工核对。
- `seedream_mcp/client/_api.py`：生成方法签名的参数序与工具参数序同构（条目见附录 B）。无守护，人工核对。
- 公共参数校验通道：`utils/core/validators.py` 的 `ValidatedCommonParams` 字段序与 `validate_common_generation_params` 签名、`client/_api.py` 的 `prevalidate_common_generation_params` 签名、inputs 快照元组，四处联动，快照元组单点构造、按相等比较复用，签名与调用 kwargs 按命名绑定；`_build_common_request` 整收 `ValidatedCommonParams` 聚合，不逐字段联动。无守护，人工核对。
- `utils/core/validators.py` 多参校验与合成函数签名（`validate_parallel_generation_options`、`validate_sequential_image_limit`、`resolve_sequential_max_images` 等）：输入派生参数序随规范投影（登记见 `parameter-order.md` 附录 B），位置绑定调用点随签名同步（`client/_api.py`、`tools/schemas.py` 与对应测试）。无守护，人工核对。
- `utils/core/validators.py` docstring、`tools/schemas.py` 字段描述常量与 `tools/pipeline/context.py`：参数描述散文的参数提及序，主体先行、其余循规范序（登记见 `parameter-order.md` 附录 B）。无守护，人工核对。
- `seedream_mcp/tools/handlers/_metadata.py` 与 `seedream_mcp/client/_api.py`：生成开始日志字段序随输入派生参数序（日志模板占位符序与取值元组序两处联动）；`seedream_mcp/tools/handlers/browse_images.py` 的浏览开始日志字段序随 O6 段序。无守护，人工核对。
- `tests/tools/test_tool_parameter_order.py`：锁序断言，逐工具硬编码序，承载现状序；重排须三处同步（见 `parameter-order.md` O7）。
- `tests/server/test_tool_call_assembly.py` 与 `tests/e2e/test_streamable_http_e2e.py`：模拟工具调用的参数载荷序随输入模型序。无守护，人工核对。
- `tests/_lib/generation_fixtures.py`：`make_generation_context` defaults 全字段罗列序随 `GenerationExecutionContext` 字段序。无守护，人工核对。
- `docs/development/parameter-order.md` 附录 A：规范序登记。无守护，人工核对。
- README 三语：参数 bullet 与调用示例 JSON 载荷顺序（bullet 条目见附录 B；载荷三语逐字一致由 test_docs_consistency 锁定，顺序对规范序无守护，人工核对）。
- `webapp` 表单、前端提交载荷、`SKILL.md` 参数规则、工具速查必需参数列与 `skills` 目录参考文件：表单分区与主题组织按 `parameter-order.md` O4 例外折叠，前端提交载荷平铺键序与各场合内参数相对先后随现状序（参考文件叙事散点提及不登记，单句罗列与同主题/步骤内相对先后循现状序）。无守护，人工核对。

## 变更记录

| 版本 | 日期       | 要点                                                                                                                                                                                                                         |
| ---- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.0 | 2026-10-14 | 初版：新增模型、新增工具、新增能力维度三类扩展的落点清单                                                                                                                                                                     |
| v1.1 | 2026-11-14 | 增设参数顺序落点（第四类扩展）；按 writing-guide 重写：正文收敛为 K1–K4 规则条款，落点清单外移附录 A–D 并按参数流转排序，统一守护标注与情态词                                                                                |
| v1.2 | 2026-11-19 | 参数顺序落点增补与勘正；公共参数校验通道联动点收缩为四处：`_build_common_request` 改收 `ValidatedCommonParams` 聚合；附录 B 增补生成输入场景声明落点（`reference_images` 图片形态访问器与 `worst_case_projection` 最坏投影） |
