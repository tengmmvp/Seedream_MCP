# Seedream MCP 仓库组织与命名规范 与 重构执行方案

| 项               | 值                                                                                                                                                                                                                              |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **版本**         | v1.7                                                                                                                                                                                                                            |
| **适用范围**     | src/seedream_mcp、tests、scripts、docs、仓库根工程文件                                                                                                                                                                          |
| **效力**         | 第一分册为长期规范（入库为 docs/development/repo-layout.md）；第二分册为一次性执行方案，完成归档于 PR 描述（如需入库则置 docs/development/archive/ 并将该目录加入 §11 硬门文档面排除清单）                                      |
| **规范关键词**   | 须＝强制；禁止＝强制不做；仅允许＝封闭集合；应当＝无充分理由须遵守；可以＝可选                                                                                                                                                  |
| **行文约定**     | 模块路径省略 `seedream_mcp.` 前缀；「阶段」＝PR 级单位（阶段 1–6）；「段」＝绿提交级单位（仅阶段 3 内部使用 3a/3b/3c 称谓）；「六域」＝tests 下 client/、config/、server/、tools/、utils/、webapp/ 六个子目录（与域包同名镜像） |
| **验证命令环境** | rg/grep 命令在 CI（ubuntu-latest）执行；本地 Windows 以 `git grep -P <模式>` 等价替代（POSIX -E 不支持非捕获组）                                                                                                                |

## 目录

- §0 概览｜术语表
- 第一分册 组织与命名规范
  - §1 结构规则（S1–S7）｜§2 命名规则（N1–N9）｜§3 依赖规则（D1–D4）｜§4 近名对边界注记｜§5 扩展路径
- 第二分册 重构执行方案
  - §6 目标结构（6.1 去前缀对照／6.2 迁移映射）｜§7 阶段总则（7.1 前置与纪律／7.2 六条不变式／7.3 各段生成模式）｜§8 阶段清单（阶段 1–6；阶段 3 含段 3a/3b/3c）｜§9 专项纪律｜§10 语义变更声明｜§11 验收门槛（门 1–8）｜§12 发布

## 0 概览

**终态一句话**：源码迁入 `src/seedream_mcp/`；包根收敛为四个身份文件＋六个域包＋一个数据目录；`tools/` 为消费方无关的能力层；`server/`（MCP 装配）与 `webapp/`（Web 装配）经同一能力层服务双消费方；一切结构、命名、依赖争议以第一分册编号条款（S/N/D）为唯一仲裁。（第一分册单独入库 repo-layout.md 时，标题层级随承载文档整体降一级。）

| 阶段 | 内容                                          | 一句话目标                                                                               |
| ---- | --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 1    | 规范落盘                                      | 第一分册入库 repo-layout.md，AGENTS/CLAUDE 指向规范                                      |
| 2    | src 布局＋验收网前置                          | 源码入 src/，全部工程配置同步，验收三件套（py.typed 断言／安装冒烟／server -m 冒烟）先行 |
| 3    | 包根归位＋utils 去前缀（3a→3b→3c 三段绿提交） | utils 终名 → 三包化与迁移 → client/config/server 薄门面化与符号级切分                    |
| 4    | tools 重组＋DAG 守护落地                      | schemas/outputs 升包级、core→pipeline、impl→handlers、守护测试启用                       |
| 5    | tests 镜像                                    | \_lib 收拢、六域分组、锚点修正、结构断言启用                                             |
| 6    | 收口                                          | vendor 隔离、文档清扫、RELEASING 条目、最终硬门                                          |

**防退化机制一览**：命名仲裁（N 系列）＋归属仲裁（D1 依赖 DAG）＋公共面冻结（S2）＋机器守护（D4 五组 AST 断言并入 test_consistency_guards）＋清单生成制（§7）＋规范修订制（§5）。

## 术语表

| 术语         | 定义                                                                                    |
| ------------ | --------------------------------------------------------------------------------------- |
| 身份文件     | 包根的 `__init__.py`／`__main__.py`／`version.py`／`py.typed`                           |
| 域包         | 包根下领域子包（client/config/server/tools/utils/webapp）                               |
| 门面         | 包 `__init__.py` 的再导出职责；门面模块＝仅承担再导出与包级组装副作用的薄 `__init__.py` |
| 契约层       | `tools/schemas.py` 与 `tools/outputs/`                                                  |
| 公共契约面   | 契约层的全部公共符号（契约模型、描述常量、构建与序列化原语）                            |
| 能力层       | `tools/` 全包                                                                           |
| 装配域       | `server/`（MCP 装配）与 `webapp/`（Web 装配）                                           |
| 执行面三件套 | runners 入口＋公共契约面＋通道作用域                                                    |
| 共生环       | server⇄webapp 经函数级延迟导入的受控双向依赖（D3 白名单承载）                           |
| 段终态       | 该段绿提交落点时的目标状态                                                              |
| 冻结面       | 见 S2                                                                                   |

---

# 第一分册 组织与命名规范

## 1 结构规则

**S1 包根准入**：包根仅允许身份文件、域包与 skills 数据目录驻留；新增领域须建子包，禁止向包根添加模块。三方文档快照隔离于 `docs/vendor/`，快照内容保真、不参与清扫与验收硬门。

**S2 公共导入面冻结**：

- 冻结面＝`seedream_mcp`、`seedream_mcp.client`、`seedream_mcp.config`、`seedream_mcp.server`、`seedream_mcp.tools`、`version.__version__` 与各门面 `__all__`／`_LAZY_EXPORTS` 所列公开名。
- 冻结的公开名以 `__all__`／`_LAZY_EXPORTS` 的现值为准；无 `__all__` 的模块其公开名不入冻结面（其门面再导出面由段 3c 名单生成制确立，确立后经 D4 冻结面断言锁定）。
- 该面内路径与所列公开名不得移动或移除；冻结面内的路径保持以「模块转包」（module→同名 package）实现。
- 非冻结面的域内重组不受本条限制（受 S1 与 N 系列约束）。
- 无下划线不因此获得公共性；公共性仅由冻结面定义（见 N5）。除冻结面外一切模块路径均为内部实现，不构成兼容承诺。

**S3 能力层消费**：

- 消费方（webapp 及未来接入方）调用能力层，仅允许经执行面三件套：`tools.runners` 的 run\_\*；`tools.schemas` 与 `tools.outputs` 的公共契约面；`tools.outputs.channels` 的通道作用域。
- 装配域 server 另仅允许消费装配支撑面白名单：`tools.pipeline` 门面的 `get_lifespan_resource`、`tools.handlers.get_model_info` 的 `build_model_info_description`；白名单变更须修改本规范。
- `tools/` 禁止依赖 `server/`、`webapp/`。机器执行见 D4-S3 断言。

**S4 MCP 协议适配集中**：

- 平铺参数签名镜像、inputSchema 收紧（`_tighten_flat_tool_schemas`）、outputSchema 注解、prompts 与 resources 的全部 `@mcp` 装饰注册，仅允许发生在 `server/_registration.py`。
- skills 的常量、路径锚定与读取函数（含 `_read_packaged_text`）位于 `server/skills.py`（无装饰器）。
- skill 资源 handler 与 `_skill_manifest_payload` 缓存全局位于 `server/_registration.py`。

**S5 一工具一模块**：`tools/handlers/<tool>.py` 为该工具唯一归属，仅允许两种形态之一：生成类＝薄适配（经 `pipeline` 门面委托编排）；独立类＝逻辑自持。形态随工具本质选择。

**S6 契约置顶**：输入契约（`tools/schemas.py`）与输出契约（`tools/outputs/` 包）置于 tools 包级。

**S7 名字与位置解耦**：模块名语义与所在包无关；模块迁移禁止伴随改名——本次重构按第二分册 §6 既定映射整体除外，此后即生效。

## 2 命名规则

**N1** 包名＝领域名词；禁止 `common`／`impl`／`misc`／`support`／`helpers`／`shared`。

**N2** 模块名＝职责名词，禁止重复宿主包名（no-stutter）。机制动词模块名须满足判定式：名称表述单一机制动作且无名词同义替代；动词／名词同形名按承载内容判定（承载动作实现→动词名；承载数据或容器→名词名）。冻结清单（以迁移终态为准，阶段 1 落盘时核对）：`settle`、`_execute`、`save`、`auto_save`、`prepare`、`download`、`scan`、`sanitize`、`bounded_store`、`bootstrap`。新增须依判定式并修改本规范。

**N3** 泛名单词须在完整路径消歧后仍准确（`utils/io/files.py` 允许；`utils/files.py` 禁止）。

**N4** 复数名＝同族原语集合：现存复数名冻结；新增复数名须由 ≥2 个异构成员构成。示例：`io/files.py` 承载文件打开、原子落盘、常规文件验证、重解析属性判定等多种文件原语；`images/inputs.py` 承载 URL、data URI、本地文件三种输入形态的归一。协议专有名词白名单：`roots`、`sse`。

**N5** 下划线前缀＝不对最终用户承诺稳定性。判定范围＝生产代码；判定粒度＝模块；测试属第一方，白盒导入下划线模块豁免；符号级下划线跨包引用不禁止。下划线与公共性判定无关（公共性仅由 S2 冻结面定义）。

**N6** 门面＝薄 `__init__.py`：仅承担再导出与包级组装副作用；实现主体住具名模块。例外：契约实现载体型 `__init__`（如 `tools/outputs/__init__`＝输出契约的实现与再导出一体）须在 repo-layout.md 注记。再导出一律 `X as X` 同名形态（mypy strict 的 no_implicit_reexport）。注册载体模块须被门面急切导入，禁止 PEP 562 懒加载（根与 tools 门面的 `_LAZY_EXPORTS` 机制不受本条限制）。

**N7** 私有模块按单一关注点命名，禁止杂居；仲裁条款：「`@mcp` 装饰注册」仲裁为单一关注点；「`pipeline/_execute` 生成编排主体」（`ToolMetadata` 元数据、`execute_generation_handler` 入口与阶段函数同属编排关注点）仲裁为单一关注点。

**N8** 测试文件名沿用 `test_<主题>`；tests 全树 test 文件 basename 须唯一。测试文件归属＝按被测模块的域包第一段入六域之一；不可判定时按文件名首主题词映射域（如 test*tool_parameter_order→tools、test_server_lifespan→server）；仍不可判定时以主导被测面裁定，并于 PR 清单记录理由。辅助模块入 `tests/_lib/` 时须去除前导下划线，命名＝`<主题>`（可含下划线复合词）或 `<主题>*<形态>`（形态∈fakes/fixtures/helpers/spy；尾段不属形态白名单时整体视作主题，如 `no_param_tools`）。补丁纪律＝patch 调用点解析名字的命名空间（定义模块与调用点命名空间不同时，打调用点）。

**N9** 模块 snake_case、类 PascalCase、常量与环境变量全大写。

## 3 依赖规则

**D1 跨包边**（源→允许目标）：

| 源                           | 允许目标                                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------------------- |
| utils.model                  | 无（纯叶子）                                                                                             |
| utils.core                   | utils.model                                                                                              |
| utils.io                     | utils.core                                                                                               |
| utils.images                 | utils.core、utils.io                                                                                     |
| config                       | utils                                                                                                    |
| client                       | config、utils                                                                                            |
| tools.schemas、tools.outputs | 仅 utils                                                                                                 |
| tools.pipeline               | client、config、tools.schemas、tools.outputs、utils                                                      |
| tools.handlers               | tools.pipeline、tools.schemas、tools.outputs、config、utils；client 仅 TYPE_CHECKING                     |
| tools.runners                | tools.handlers、tools.schemas、config、utils.io                                                          |
| server                       | tools、config、utils；client 仅 TYPE_CHECKING 与函数级；webapp 仅函数级                                  |
| webapp                       | tools、config、utils；server 仅函数级                                                                    |
| 包根门面                     | 根 `__init__` 经 `_LAZY_EXPORTS` → client/config/server；tools.`__init__` → tools.schemas、tools.runners |

豁免：任意包→`version.py`。粒度注记：client/config/server/tools/utils/webapp 作为依赖目标时按整包（或整组）计（含其全部子模块，如 pipeline→client.request_plan 计为 pipeline→client；utils 目标含 core/images/io/model 全部子模块）。

**D2 模块级无环**：全树（含包内）模块级导入须无环。

**D3 函数级导入白名单**：

- 作用域＝seedream_mcp 包内目标（标准库与第三方导入不入判定）；函数级（延迟）导入仅允许登记于白名单的 (源模块→目标模块) 二元组；新增须先登记再使用。
- 白名单的权威载体＝守护测试内的常量（单一来源），本表为快照、以守护为准；规范变更与守护常量须同一 PR 修改；守护引入时以机械扫描存量校正。
- 快照见下表。

| 源 → 目标                                                         | 备注                                                            |
| ----------------------------------------------------------------- | --------------------------------------------------------------- |
| server.transport → webapp；server.transport → webapp.constants    | 共生环（web 侧挂载）                                            |
| server.transport → server.runtime                                 | 包内延迟                                                        |
| server.runtime → client                                           | 借共享客户端                                                    |
| server.runtime → config                                           | 复位协议                                                        |
| server.runtime → utils.io.{save,files,path,scan,storage,download} | 复位协议（六模块）                                              |
| server.runtime → utils.images.thumbnail                           | 复位协议                                                        |
| webapp.routes → server.runtime；webapp.context → server.runtime   | 共生环（server 侧借柄）                                         |
| webapp.routes → webapp.{files,gallery,generate,meta}              | 域内懒装配                                                      |
| utils.images.thumbnail → utils.core.formats                       | PIL 惰性链                                                      |
| utils.io.files → utils.core.logs                                  | 既有延迟                                                        |
| config.\_model → config.\_builder                                 | 仅限 `SeedreamConfig.from_env` 调用点；其他函数级导入须另行登记 |

**D4 机器执行**：下列断言由 AST 守护测试执行（并入 `test_consistency_guards.py`）：

1. **包根准入断言**：包根目录与文件清单 ⊆ 封闭集合（身份文件＋六域包＋skills/，S1）。
2. **依赖断言**：D1 仅判跨包边，包内边仅受 D2（无环）约束；模块级与 TYPE_CHECKING 跨包导入按 D1 判定；函数级导入按 D3 白名单；相对导入按 level 解析为绝对模块后判边；两个门面的 `_LAZY_EXPORTS` 字符串表解析入图，`import_module` 禁止出现于门面以外模块；测试文件不入依赖判定。
3. **S3 断言**：webapp 对 tools 的导入集 ⊆ 执行面三件套封闭集；server 对 tools 的导入集 ⊆ 执行面三件套封闭集 ∪ 装配支撑面白名单。
4. **冻结面断言**：S2 五路径可导入；门面 `__all__` 快照的封闭集合＝根 `__init__`、tools、client、config、server 五门面（pipeline 门面 `__all__` 沿用 common.py 现值顺迁、outputs 门面同步定义 `__all__`，均于阶段 4 落定）；各门面 `__all__` 与守护内快照逐字一致。
5. **tests 结构断言**（随阶段 5 启用）：test 文件全树 basename 唯一；conftest.py 仅存在于 tests 根；六域与 contracts/e2e 子目录无 `__init__.py`；`_lib` 有 `__init__.py`。

## 4 近名对边界注记

| 名对                                         | 边界                                    |
| -------------------------------------------- | --------------------------------------- |
| `outputs/sanitize` ↔ `utils/core/sanitizers` | 输出净化层 ↔ 脱敏原语层                 |
| `io/storage` ↔ `io/save` ↔ `io/files`        | 存储策略 ↔ 保存编排 ↔ 文件机械          |
| `pipeline/auto_save` ↔ `io/save`             | 生成侧编排 ↔ 下载落盘                   |
| `outputs` ↔ `pipeline/results`               | 契约出口 ↔ 响应抽取                     |
| `pipeline/_paths` ↔ `io/path`                | 调用级 save_path 解析 ↔ 目录体系        |
| `images/inputs` ↔ `images/prepare`           | 输入归一 ↔ 缓存预处理                   |
| `skills/` ↔ `server/skills.py`               | 包根随包分发数据目录 ↔ 技能资源读取机制 |

保留注记：`utils/model` 单文件组（语义分组）；`io/download` 不拆（下载管线与 SSRF 防护内聚）。

## 5 扩展路径

- **新工具**＝`handlers/` 单模块＋四处同步点（终态落点：`tools/schemas.py` 输入模型 → `server/_registration.py` 平铺签名镜像 → `tools/pipeline/context.py` 能力补校验 → `client/_api.py` 公共校验入口）。
- **新消费方**＝执行面三件套（S3）。
- **handlers 内共享非工具模块**＝私有支撑模块（如 `_metadata`），按 N7 单一关注点命名。
- **新域**＝新子包；**新 utils 原语**＝按 N 系列入组。
- 结构规则变更须修改本规范并随 PR 评审。

---

# 第二分册 重构执行方案

## 6 目标结构

```text
src/seedream_mcp/
├── __init__.py ／ __main__.py ／ version.py ／ py.typed
├── client/   __init__(门面)  _api  _http  request_plan
├── config/   __init__(门面)  _model  _builder  sources  host_syntax
├── server/   __init__(门面)  __main__  _registration  skills
│             bootstrap  cli  transport  runtime  _icons
├── tools/    __init__  schemas
│             outputs/{__init__  channels  sanitize}
│             pipeline/{__init__(门面)  _execute  context  results
│                       parallel  auto_save  _progress  _failures  _paths}
│             handlers/{text_to_image  image_to_image  multi_image_fusion
│                       sequential_generation  browse_images  get_model_info  _metadata}
│             runners
├── utils/    core/{errors formats logs validators sanitizers inflight
│                   loop_bound settle executors http_int throttles bounded_store}
│             images/{inputs validation prepare ref thumbnail}
│             model/{capabilities}
│             io/{files path roots url stream sse download storage save scan}
├── webapp/   （域结构不动）
└── skills/   （数据目录）
```

### 6.1 utils 去前缀对照（16 文件，阶段 3a）

throttles 同段拆出 `bounded_store`：`store_bounded_entry` 消费点＝io/path、io/storage；`ThrottleRegistry` 消费点＝io/save、images/thumbnail。

| 阶段 3a 现名       | 终名         | 阶段 3a 现名     | 终名       |
| ------------------ | ------------ | ---------------- | ---------- |
| io_file            | files        | io_storage       | storage    |
| io_path            | path         | io_save          | save       |
| io_roots           | roots        | io_scan          | scan       |
| io_url             | url          | image_input      | inputs     |
| io_stream          | stream       | image_validation | validation |
| io_sse             | sse          | image_prepare    | prepare    |
| io_download        | download     | image_ref        | ref        |
| model_capabilities | capabilities | image_thumbnail  | thumbnail  |

### 6.2 迁移映射（以 §6 树为准）

| 现路径                                          | 终态                                                                                                                             | 执行段  |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------- |
| client.py                                       | `client/__init__.py`（全文，胖中间态）                                                                                           | 3b      |
| config.py                                       | `config/__init__.py`（全文）                                                                                                     | 3b      |
| server.py                                       | `server/__init__.py`（全文；`_SKILLS_DIR` 锚改 `parents[1]`；尾部 `if __name__` 守卫删除）                                       | 3b      |
| resources.py                                    | `server/runtime`（`_reset_lifespan_state` 随迁）                                                                                 | 3b      |
| \_client_http                                   | `client/_http`                                                                                                                   | 3b      |
| request_plan                                    | `client/request_plan`                                                                                                            | 3b      |
| \_config_sources                                | `config/sources`                                                                                                                 | 3b      |
| \_host_syntax                                   | `config/host_syntax`                                                                                                             | 3b      |
| \_icons／bootstrap／cli／transport              | server/ 同名                                                                                                                     | 3b      |
| PROJECT_ROOT                                    | 定格 `parents[3]`                                                                                                                | 3b      |
| client.py（拆分）                               | `client/_api`＝全文，门面仅再导出 `SeedreamClient`                                                                               | 3c      |
| config.py（切分）                               | `config/_model`＋`config/_builder`（符号级清单见 §8 段 3c）                                                                      | 阶段 3c |
| server.py（拆分）                               | `server/_registration`＋`server/skills`                                                                                          | 3c      |
| tools/core/schemas                              | `tools/schemas`                                                                                                                  | 阶段 4  |
| tools/core/outputs＋\_channels＋\_sanitize      | `tools/outputs/{__init__,channels,sanitize}`                                                                                     | 阶段 4  |
| tools/core/common                               | `tools/pipeline/__init__`（门面）＋`tools/pipeline/_execute`（编排主体：`ToolMetadata`、`execute_generation_handler`、阶段函数） | 阶段 4  |
| tools/core/\_pipeline（三拆＋折入）             | 见表下注 A                                                                                                                       | 阶段 4  |
| tools/core/{context,results,parallel,auto_save} | pipeline/ 同名                                                                                                                   | 阶段 4  |
| tools/core/browse                               | 并入 `handlers/browse_images`                                                                                                    | 阶段 4  |
| tools/core/model_info                           | 并入 `handlers/get_model_info`                                                                                                   | 阶段 4  |
| tools/impl/\*                                   | handlers/\* 同名                                                                                                                 | 阶段 4  |

注 A（`tools/core/_pipeline` 三拆＋折入，阶段 4）：

- `_progress`＝8 个 PROGRESS\_\* 常量＋`safe_report_progress`＋`_yield_for_cancellation`
- `_failures`＝`_normalize_error_message`、`_classify_generation_error_type`、`_failure_guidance_for_code`、`log_tiered_failure`、`_extract_parallel_request_error` 及三个失败指引常量（`_FAILURE_GUIDANCE_DEFAULT_CODES`、`_FAILURE_GUIDANCE_BY_ERROR_CODE`、`_DEFAULT_FAILURE_GUIDANCE`）
- `_paths`＝`_resolve_base_dir`、`prevalidate_save_path`
- `_add_usage_value` 折入 `results`；原模块级 logger 不随迁，各新模块自建

## 7 阶段总则

### 7.1 前置与纪律

- **执行前置**：每阶段开始前工作树须干净（`git status` 无未提交与未跟踪变更）——计数与 `git grep` 仅读取已跟踪索引。
- **清单生成制**：一切改写清单须由 rg 命令机械生成并全文固化于 PR 描述；禁止手写计数。
- **同步随改写原则**：守护测试与断言的同步跟随改名发生的那次绿提交，按该次提交的段终态改写。
- **段收尾动作**：`uv sync`；一切验证经 `uv run`。

### 7.2 六条全案不变式（留证命令）

每段收尾在仓库根执行，输出固化于 PR 描述供人工研判命中项；通过判据属各段验证门的机判项，两者不混用。

| #   | 命令                                            |
| --- | ----------------------------------------------- | ------------------------------------- | -------- | ----------------------- | ------------------------- | ---------- | ----------- | -------------- |
| ①   | `rg "\b(from                                    | import)\s+seedream_mcp[.\w]\*"`       |
| ②   | `rg "\bfrom\s+\.{1,3}[.\w]*\s+import"`          |
| ③   | `rg "['\"]seedream_mcp[.\w]*['\"]"`             |
| ④   | `rg "(?:^                                       | [^\w.])seedream_mcp[\/]"`             |
| ⑤   | `rg "monkeypatch\.setattr\(\s*[\w.]*module\s\*, | monkeypatch\.setattr\(\s*server\s*,"` |
| ⑥   | `rg "**file**                                   | \brglob\(                             | \.glob\( | spec_from_file_location | \_ENV_KEY_READ_EXEMPTIONS | tests_root | \_REPO_ROOT | PROJECT_ROOT"` |

### 7.3 各段生成模式

**阶段 2**：不变式④限定工程文件面（pyproject.toml、Dockerfile、docker-compose.yml、cspell.json、RELEASING.md、.github/、scripts/）＋

`
rg "seedream_mcp" pyproject.toml Dockerfile docker-compose.yml cspell.json RELEASING.md .github scripts

```

（工程配置全引用枚举，含 RELEASING.md 的 version.py 相对路径两处）

**段 3a**：

`
rg "utils\.(io|images|model)\.(io_|image_|model_)"
rg "from seedream_mcp\.utils\.(io|images|model) import (io_|image_|model_)"
rg "from \.(io_|image_|model_)\w+ import|from \.{2,3}(io|images|model)\.(io_|image_|model_)\w+"
rg "monkeypatch\.setattr\(\s*(io_|image_|model_)?\w*module"
rg "monkeypatch\.setattr\(\s*(io_|image_|model_)[a-z_]+[.,]"     # 裸模块名与类属性补丁目标
rg "(store_bounded_entry|ThrottleRegistry)" src/seedream_mcp     # throttles 拆分消费点
```

改写策略：旧模块名导入保留别名为缺省策略（如 `from seedream_mcp.utils.io import save as io_save`），别名保留则类属性补丁无须改写；显式去别名时同步重定位。

**段 3b**（覆盖点分引用、import-模块、from 包 import 子模块、相对导入、from 包 import 名五形态；单行命令）：

`
rg "\bfrom seedream_mcp\.(resources|transport|bootstrap|cli|\_icons|\_client_http|\_config_sources|\_host_syntax|request_plan)\b|\bimport seedream_mcp\.(resources|transport|bootstrap|cli|\_icons|\_client_http|\_config_sources|\_host_syntax|request_plan)\b|from seedream_mcp import (resources|transport|bootstrap|cli|\_icons|\_client_http|\_config_sources|\_host_syntax|request_plan)\b|from \.{1,3}(resources|transport|cli|bootstrap|\_icons|\_client_http|\_config_sources|\_host_syntax|request_plan) import|from \.{1,3} import (resources|transport|bootstrap|cli|\_icons|\_client_http|\_config_sources|\_host_syntax|request_plan)\b"

```

**段 3c**（门面再导出名单四轴；根门面 TYPE_CHECKING 块与 `_LAZY_EXPORTS` 字符串表所列名（如 `get_global_config` 等 7 名）为固定名单源一并固化）：

`
rg "from seedream_mcp\.(server|client|config|tools) import"
rg "from \.{1,3}(config|client|server) import|from \.{1,3} import (config|client|server)"   # 包内相对，含缩进与三级
rg "monkeypatch\.setattr\(\s*(server|client_module|config_module)\s*,"
rg "\b\w*(server|client|config)_?module\.[A-Za-z_]\w*|\bserver\.[A-Za-z_]\w*"               # 属性式访问面（server.X 裸别名主导形态）
```

**阶段 4**：

`
rg "\bfrom seedream*mcp\.tools\.(core|impl)|from \.{1,3}tools\.(core|impl)\.|\bfrom \.{1,2}(core|impl)\."
rg "(?:tools/)?(?:core|impl)/[a-z*]+" src/seedream_mcp tests # 斜杠散文形态（含 tools/ 前缀与无扩展名）
rg "monkeypatch\.setattr\(\s*(browse_core_module|browse_module|browse_images_module|auto_save_module|common_module|outputs_module|results_module|schemas_module|parallel_module|impl_module)\s*,|import seedream_mcp\.tools\.(core|impl)\.\w+ as \w+"
rg "tools\.(core|impl)\b" src/seedream_mcp tests # docstring/注释点形态
rg "\bcore\.(schemas|common|outputs|browse|model_info|context|results|parallel|auto_save)\b|\bimpl\b" src/seedream_mcp # 裸词散文

```

对被拆散模块（core/_pipeline）的内省与负向断言按「同关注点宿主」重定位（如 `_safe_ctx_log` 缺失断言→`pipeline._failures`），无法判定关注点时删除断言并记录理由；裸词散文的自然语言行人工改写留档（不设硬门）。

**阶段 5**：

`
rg "^\s*from _[a-z][a-z_]*(\.|\s+import)|^\s*import _[a-z][a-z_]*\b"   # 行首锚定含缩进；排除 _winapi 与 from _lib.
rg "from conftest import"
```

## 8 阶段清单

每阶段按「动作 → 同步面 →（守护）→ 验证门」组织；无则省略该层。

### 阶段 1 规范落盘

- **动作**：第一分册（含 N2 冻结清单终态核对）入库 repo-layout.md；AGENTS.md「项目结构与模块组织」节与 CLAUDE.md「Project Overview／Core Architecture／File Organization Notes」中的逐模块描述替换为指向 repo-layout.md 的指针（部署、命令、契约等其余内容不动）。
- **验证门**：全量测试绿＋节切片核验——上述被替换节的文本切片内 `seedream_mcp[/\.]` 零命中（切片以节标题为界，脚本与输出固化于 PR；保留章节的旧引用属当前事实，由阶段 6 清扫）。

### 阶段 2 src 布局＋验收网前置

- **动作**：
  - git mv 至 src/。
  - pyproject 同步：hatch.version path、sdist include、wheel `packages=["src/seedream_mcp"]`、mypy files、coverage omit 前缀。
  - Dockerfile COPY 行；reusable-checks.yml（black/flake8 作用域两行、JS glob 一行、package-checks 构建步骤改 `uv build` 以同产 wheel＋sdist）；sdk-lower-bound.yml paths 数组两处；cspell.json files 首项。
  - 生产侧 PROJECT_ROOT 过渡 `parents[2]`（`seedream_mcp/_config_sources.py:24`；测试侧 `tests/_readme_helpers.py:14` 同名锚不动，留待阶段 5）。
  - config 增 `PROJECT_ROOT` 再导出（config.py 导入清单加 `PROJECT_ROOT as PROJECT_ROOT`，后续段保持）；test_env_example_guard 的 `_repo_root` 改 `from seedream_mcp.config import PROJECT_ROOT`。
  - reusable-checks.yml「Assert wheel assets」required 列表补 `seedream_mcp/py.typed`。
  - package-checks 增安装冒烟：

    ```bash
    T=$(mktemp -d)
    uv pip install --target $T/wheel --no-deps dist/*.whl
    uv pip install --target $T/sdist --no-deps dist/*.tar.gz
    PYTHONPATH=$T/wheel python -c "import seedream_mcp, pathlib; assert pathlib.Path(seedream_mcp.__file__).is_relative_to('$T/wheel')"
    test -f $T/wheel/seedream_mcp/py.typed
    PYTHONPATH=$T/sdist python -c "import seedream_mcp, pathlib; assert pathlib.Path(seedream_mcp.__file__).is_relative_to('$T/sdist')"
    test -f $T/sdist/seedream_mcp/py.typed
    ```

  - test_package_lazy_import 增 `python -m seedream_mcp.server --help` 冒烟（PYTHONUTF8=1、退出码 0）。

- **同步面**：test_ci_workflow_guards 三处断言（L179 JS glob 字面量、L180 `_REPO_ROOT/"seedream_mcp"` js_dir、L253-262 paths 条目）；test_version_unified 的 path 字面量断言。
- **验证门**：全量测试＋package-checks 绿。

### 阶段 3 包根归位＋utils 去前缀（合并 PR，三段绿提交）

#### 段 3a utils 去前缀＋bounded_store 拆分

- **动作**：按 §6.1 对照表完成 16 文件改名；throttles 拆出 `bounded_store`（消费点归属见 §6.1 注）。
- **同步面**：豁免键重键 `utils/io/path.py:os.getenv(env_var)`；utils 侧补丁面与模块别名重定位（按 §7.3 段 3a 模式生成）；utils 各组 `__init__.py` docstring 的前缀约定陈述随段改写；源码树注释/docstring 内旧名裸词以 `rg "\b(io_|image_|model_)[a-z_]+" src/seedream_mcp` 枚举、自然语言行人工改写并留档（不设硬门）。
- **验证门**：全量测试＋mypy＋不变式留证。

#### 段 3b 包根归位

- **动作**：
  - client/config/server 三包化（三主文件全文入 `__init__`）。
  - 新增 `server/__main__.py`（完整内容＝模块 docstring＋`from seedream_mcp.server import cli_main`＋`if __name__ == "__main__": raise SystemExit(cli_main())`，与根 `__main__.py` 同构）并同步追加 coverage omit。
  - server.py 尾部 `if __name__` 守卫删除；`_SKILLS_DIR` 锚改 `parents[1]`。
  - 其余迁移按 §6.2 表（3b 行）执行；PROJECT_ROOT 定格 `parents[3]`。
  - 被迁移文件内部的相对导入按新包层级整体加深一级（如 `from .config import`→`from ..config import`；指向包内新兄弟模块者改指同级；清单以不变式②对该批文件的输出为准）。
  - 退役名（`resources`、`_client_http`、`_config_sources`、`_host_syntax`）禁止别名保留，其引用一律直改终态名（别名保留缺省策略仅适用于 3a 去前缀名与 tools.core/impl 名）。
- **同步面**：webapp/context.py 与 webapp/routes.py 懒导入改写；源码树 `resources` 裸词散文以 `rg "\bresources\b" src/seedream_mcp`（排除导入行）枚举随段改写；tests 侧 `resources.` 属性使用点以 `rg "\bresources\.[A-Za-z_]\w*" tests` 枚举随段改写；根模块导入改写（按 §7.3 段 3b 模式生成，覆盖 conftest 与全部 tests）；test_readme_code_parity 的 `_server_source` 改读 `server/__init__.py`；豁免键 `config/__init__.py:os.getenv(env_name)` 与 `config/sources.py:os.getenv(env_key)`。
- **验证门**：全量测试＋三入口冒烟。

#### 段 3c 薄门面拆分（client/config/server 三域）

- **动作与符号级切分**（行号仅参考，以符号边界为准）：
  - `client/_api`＝client.py 全文；门面仅再导出 `SeedreamClient`。
  - `config/_model`＝`SeedreamConfig` 类＋常量区（`LIFESPAN_KEY_*`、`DEFAULT_HTTP_HOST/PORT`、`HTTP_AUTH_TOKEN_MIN_LENGTH` 与四个私有校验常量）＋`_first_invalid_request_state_key_message`。
  - `config/_builder` 的符号构成（行号仅参考，以符号边界为准）：
    - 函数与常量：`drain_pending_build_warnings` 函数；`ENV_FAMILY_PREFIXES`；`build_config_from_sources` 及其私有体；`active_request_state_keys`
    - 锁与全局状态：`_config_build_lock` 与 `_global_config_lock` 双锁；`_active_config`/`_global_config` 与其访问器同模块
    - 注册区：环境提供者注册区（＝`_make_env_location_provider` 函数与 config.py:1024-1059 区间全部 `register_*` 调用）；`_active_pool_concurrency` 与 CPU 池深提供者注册（随访问器同住 `_builder`，误归 `_model` 即成模块环）
    - 尾部：`init_env_registry(SeedreamConfig)` 调用置模块尾部
  - `config/host_syntax` 吸收四符号：函数 `_ipv6_host_literal_forms`、`_is_valid_url_hostname` 与常量 `_REG_NAME_SYMBOLS`、`_PCT_ENCODED_HEXDIGITS`（`_validate_ipv6_host_literal` 留守 `_model`，其依赖 sources 的 `_env_var_suffix` 与本组形态函数）。
  - `server/_registration`＝server.py 注册主体＋skill 资源 handler＋`_skill_manifest_payload` 缓存全局。
  - `server/skills`＝`_SKILL_*` 常量＋`_read_packaged_text`＋`_read_skill_manifest`＋`_read_skill_reference`＋路径锚定。
  - 唯一最小改写豁免：`SeedreamConfig.from_env` 体内对 `build_config_from_sources` 函数级延迟导入（破 `_model↔_builder` 模块环，登记 D3）。
- **同步面**：
  - 门面再导出名单按 §7.3 段 3c 四轴生成并固化。
  - `_BUILD_WARNINGS` 的 `.clear()`/`.snapshot()` 依赖改经定义模块 `config.sources`（按 §9 决胜规则，门面不再导出该私有名）。
  - `_server_source` 二跳改读 `server/_registration.py`；豁免键 `config/_builder.py:os.getenv(env_name)`。
  - 补丁面重定位：`validate_common_generation_params`→`client._api`（调用点命名空间）；`build_preview_contents`/`auto_save_from_urls`→pipeline 侧调用点（阶段 4 前暂打 `tools.core.common` 现址，阶段 4 随迁 `pipeline._execute`）；`_SKILL_*` 常量→定义模块 `server.skills`；`_read_skill_*`→调用点命名空间 `server._registration`（handler 的导入绑定）；`_active_config`/`_global_config` 及访问器（`get/set_active_config`、`get_global_config`）补丁与复位直写→`config._builder`。
  - 第 2–4 轴（包内相对导入、补丁面、属性式访问面）命中的名单外条目按 N8/§9 原则裁定落点（定义符号→定义模块；绑定符号→保留门面再导出或改打消费方命名空间），逐条记录于 PR 清单（已知示例：`server.mcp`/`server.logger`/`server.run_*` 补丁→`server._registration`；`server.get_active_config` 补丁→其调用点命名空间）。
  - 源码树 `server.py` 的非导入文本引用（docstring/注释，如 tools/**init** 与 tools/core/schemas 的平铺签名描述）以 `rg "server\.py" src/seedream_mcp tests` 枚举（tests 内 `_server_source` 两跳同步之外的散文行随段改写），随本段改指注册主体新位置。
  - client/config/server 三门面以生成名单显式定义 `__all__`（D4 冻结面断言的锁定前提）。
- **验证门**：全量测试＋mypy＋不变式留证＋三入口冒烟（同阶段 2 三命令）。

### 阶段 4 tools 重组

- **动作**：按 §6 树执行（含 `pipeline/__init__`＋`_execute` 的移动与门面拆分在本段完成）；tools/**init** 的 TYPE_CHECKING 再导出块与 `_LAZY_EXPORTS` 字符串表两处同步改指 `.schemas`/`.runners`；tools 侧补丁面与模块别名重定位（按 §7.3 阶段 4 模式生成，含 `core.browse` 别名→`handlers.browse_images`、`build_preview_contents`/`auto_save_from_urls` 补丁落 `pipeline._execute`）。
- **同步面**：webapp 对 tools/core 的全部导入改写（generate/gallery 的 schemas、\_responses 的 outputs 序列化原语等，按 §7.3 阶段 4 模式生成）；其中 `output_channels_scope`（generate/gallery）改指 `tools.outputs.channels`（S3 合规落点，禁止机械重定位到 pipeline 门面）；server/\_registration（原 server.py 的四处 tools.core 相对导入 common/model_info/schemas/outputs 已随 3c 迁入其中）按终态改写。
- **守护**：DAG 守护测试落地（D4 依赖断言＋S3 断言＋冻结面断言；tests 结构断言组待阶段 5 启用）。
- **验证门**：全量测试＋守护绿。

### 阶段 5 tests 镜像

- **动作**：
  - 辅助模块（数量以 `git ls-files "tests/_*.py" | wc -l` 机械核验并生成清单）入 `_lib/`（去前导下划线，N8 命名）。
  - 其余 test 文件（数量于执行时以 `git ls-files "tests/test_*.py" | wc -l` 机械核验、扣除 contracts/e2e 后即六域总数）按 N8 归属规则入六域。
  - contracts 11 文件（test_ci_workflow_guards、test_pyproject_dependency_source、test_version_unified、test_env_example_guard、test_docs_consistency、test_readme_code_parity、test_coverage_floors_script、test_docker_uid_contract、test_consistency_guards、test_package_lazy_import、test_sdk_behavior_characterization）与 e2e 3 文件（test_streamable_http_e2e、test_workspace_roots_resolver_e2e、test_workspace_roots_resource_e2e）。
  - 九处 repo 根锚逐一 `parents[N]+1`（\_readme_helpers、\_docker_uid、\_subprocess_helpers、test_ci_workflow_guards、test_docker_uid_contract、test_coverage_floors_script、test_pyproject_dependency_source、test_version_unified、test_package_lazy_import）；模块对象锚只改文件名段。
  - test_server_roots_dependency 两件套（glob→rglob＋锚 `parents[1]`）＋豁免条目 `_roots_session_fakes.py` 改为 `roots_session_fakes.py`（该断言为 basename 比较，不含路径）。
  - tests 裸名辅助导入全改 `_lib.` 形态；`from conftest import` 的两处消费保留原样（prepend 模式下根 conftest 先于测试模块导入，tests/ 在 sys.path，跨子目录可解析）。
- **验证门**：全量测试＋D4 结构断言绿。

### 阶段 6 收口

- **动作**：
  - docs/mcp-python-sdk/ 整体迁至 docs/vendor/mcp-python-sdk/（快照保真，迁移后内容不改写）。
  - README.md、README.en.md、README.zh-TW＋docs（排除 docs/vendor/ 与 docs/volcengine/）全量清扫（模式集＝§11 硬门封闭集含斜杠形态与命令上下文裸名×文档范围）。
  - AGENTS.md 与 CLAUDE.md 为本地未跟踪文件（.gitignore 所列），同步动作本地执行、PR 附两文件 diff 留档（如需机器守护，可另行决定将两文件纳入版本跟踪）。
  - .gitignore 删 `seedream_mcp.egg-info/` 冗余条目；RELEASING.md 条目。
- **验证门**：§11 全部。

## 9 专项纪律

- **作用域**＝迁移涉及的四个域包（client/config/server/tools）的包初始化链：导入期须用直接子模块导入（`from .runtime import mcp`），禁止导入期属性式（`from . import runtime`）。webapp 域内既有兄弟模块属性式导入（`from . import _responses` 等）不受本条约束。
- **全局状态复位与补丁**须打定义模块或调用点命名空间（按 N8）。
- **决胜规则**：冻结面之外的绑定符号一律优先改打定义模块或消费方命名空间，门面再导出仅限冻结面与生成名单所需。
- **提交粒度**须保证 git mv rename 追踪。

## 10 语义变更声明（迁移后现象，均无须处理）

1. src 布局后仓库根不可直接 `import seedream_mcp`（须经安装环境或 `uv run`）。
2. 导入 server 任意子模块将触发完整注册（原 resources 独立导入不注册）。
3. 导入 `config.sources`/`client._http` 连带执行所在包门面。
4. `python -m seedream_mcp.server` 下工具函数 `__module__` 由 `__main__` 变为包名。
5. wheel 安装态 PROJECT_ROOT 空转层位置改变（同类空转）。

## 11 验收门槛

1. `uv run pytest -q` 全绿（含 slow）；`uv run black --check src/seedream_mcp tests scripts`＋flake8＋mypy＋cspell 干净；覆盖率门禁配置不变（总量 80／单文件 70），实测总覆盖率不低于阶段 2 前基线（基线固化于阶段 2 PR 描述）。
2. 安装环境三入口退出码 0：`uv run python -m seedream_mcp --help`；`uv run python -m seedream_mcp.server --help`；`uv run seedream-image-mcp --help`。
3. package-checks 绿（导入冒烟、wheel 构建、资产断言含 py.typed、wheel＋sdist 安装冒烟）。
4. DAG 守护绿（D4 全部断言）。
5. **grep 硬门**＝下列封闭模式 × 指明范围零命中。范围定义：「源码树」＝src/seedream_mcp＋tests＋scripts；「文档面」＝README.md、README.en.md、README.zh-TW＋docs（排除 docs/vendor/ 与 docs/volcengine/）＋.github＋pyproject.toml；AGENTS/CLAUDE 为本地未跟踪文件，以 PR 附 diff 人工核验。所有门以完整命令给出（CI 于仓库根执行；先设会话变量）：

   ```bash
   SRC="src/seedream_mcp tests scripts"
   DOC="README.md README.en.md README.zh-TW docs .github pyproject.toml"
   DOC_EX="-g !docs/vendor/** -g !docs/volcengine/**"
   ```

   **门 1（退役名导入，源码树）**——四退役名新树无同名模块、零误伤；保留名 transport/bootstrap/cli/\_icons/request_plan 的相对形态在新家合法、不纳入，残留由运行期导入失败兜底：

   ```bash
   rg -n "from seedream_mcp\.resources|from \.{1,3}(resources|_client_http|_config_sources|_host_syntax) import|from \.{1,3} import (resources|_client_http|_config_sources|_host_syntax)\b" $SRC
   ```

   **门 2（旧名点分引用，源码树＋文档面）**：

   ```bash
   rg -n "\bseedream_mcp\.(resources|transport|bootstrap|cli|_icons|_client_http|_config_sources|_host_syntax|request_plan)\b|from seedream_mcp import (resources|transport|bootstrap|cli|_icons|_client_http|_config_sources|_host_syntax|request_plan)\b" $SRC $DOC $DOC_EX
   ```

   **门 3（tools 旧结构，范围同门 2）**：

   ```bash
   rg -n "\btools\.(core|impl)\b" $SRC $DOC $DOC_EX
   ```

   **门 4（去前缀文件名，源码树路径清单口径——对文件路径列表匹配，非文件内容扫描）**——锚定退役子树，同名保留文件 handlers/image_to_image.py 不命中：

   ```bash
   git ls-files | grep -P 'utils[/\\](?:io|images|model)[/\\](?:io_|image_|model_)[a-z_]+\.py$'
   ```

   **门 5（tests 裸名辅助导入，tests）**——行首锚定含缩进，`__future__` 因首字符非字母自动不命中；管道排除后输出须为空：

   ```bash
   rg "^\s*from _[a-z][a-z_]*(\.|\s+import)|^\s*import _[a-z][a-z_]*\b" tests | grep -v "_winapi" | grep -vE "from _lib import|import _lib\b|from _lib\."
   ```

   **门 6（tests 根唯一性）**——输出仅 `conftest.py`：

   ```bash
   cd tests && git ls-files | grep -v /
   ```

   **门 7（命令上下文裸名，文档面）**——合法形态为 `src/seedream_mcp`：

   ```bash
   rg -n "(?:black|flake8|mypy|pytest)(?: --[a-z-]+)* seedream_mcp\b" $DOC $DOC_EX
   ```

   **门 8（路径与文件名旧形态，文档面＋源码树，内容扫描口径）**——退役名禁别名保留后零误伤；合法新路径（含 src/ 前缀或新模块名）不命中；image\_ 前缀仅经退役子树锚定捕获（image_to_image.py 为同名保留文件）：

   ```bash
   rg -n "seedream_mcp/(resources|transport|bootstrap|cli|_icons|_client_http|_config_sources|_host_syntax|request_plan|server|client|config)\.py|tools/(core|impl)/|utils[/\\](?:io|images|model)[/\\](?:io_|image_|model_)[a-z_]+\.py|(?:^|[^\w./])(?:io_|model_)[a-z_]+\.py|(?:^|[^\w/])(?:server|client|config|resources)\.py|resources\._[A-Za-z_]\w*" $SRC $DOC $DOC_EX
   ```

6. **文档与实际目录一致性**：应当经门 1–8 与既有守护测试（test_docs_consistency 等）佐证；源码树注释/docstring 内旧模块名裸词（阶段 3a/4 已枚举留档项）与 AGENTS/CLAUDE 本地文件同步列入 PR 人审清单。

## 12 发布

全部阶段在同一 minor 版本周期合入；RELEASING.md 追加条目；release note 须声明：纯结构重构、冻结面（S2）零变化、内部路径变更清单。
