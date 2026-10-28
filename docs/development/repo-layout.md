# Seedream MCP 仓库组织与命名规范

| 项             | 值                                                                             |
| -------------- | ------------------------------------------------------------------------------ |
| **版本**       | v1.10                                                                          |
| **适用范围**   | src/seedream_mcp、src/seedream_cli、tests、scripts、docs、仓库根工程文件       |
| **规范关键词** | 须＝强制；禁止＝强制不做；仅允许＝封闭集合；应当＝无充分理由须遵守；可以＝可选 |
| **行文**       | 遵循 docs/development/writing-guide.md；模块路径省略 `seedream_mcp.` 前缀      |

## 目录

- 阅读指南 ｜ 设计原则 ｜ 架构总览 ｜ 术语表
- §1 结构规则（S1–S8）｜§2 命名规则（N1–N9）｜§3 依赖规则（D1–D4）｜§4 近名对边界注记｜§5 扩展路径
- 附录 A（D3 白名单快照）｜附录 B（变更记录）

## 阅读指南

本规范是仓库结构与命名的唯一裁决依据，条款由守护测试机器执行（D4）。读者两类：AI 开发会话在动笔前查规则，维护者在评审时仲裁。全文按三层组织：设计原则与架构总览建立全局观，结构、命名、依赖三系列条款供逐条查阅，附录承载随代码漂移的资料。

任务入口：

| 要做的事                        | 先读         | 机器执行                                                            |
| ------------------------------- | ------------ | ------------------------------------------------------------------- |
| 新增跨域共享定义（常量/默认值） | S8、D1       | test_package_root_admission、test_module_dependency_edges_follow_d1 |
| 新增 MCP 工具                   | S5、S6、§5   | tests/contracts/test_readme_code_parity.py                          |
| 新增或调整跨包依赖              | D1           | test_module_dependency_edges_follow_d1                              |
| 新增函数级延迟导入              | D3、附录 A   | test_function_level_imports_within_whitelist                        |
| 迁移或重排模块                  | S7、S2       | test_frozen_facade_paths_importable                                 |
| 新增测试文件                    | N8           | test_test_files_basename_unique                                     |
| 修改本规范                      | 文档维护约定 | 同 PR 联动守护断言                                                  |

文档维护约定：行文遵循 writing-guide.md；规范条款变更须与对应守护断言同一 PR 修改；版本号与变更记录（附录 B）同步更新。

## 设计原则

条款未覆盖的场合，按以下四条裁决；条款与原则冲突时，先改规范再动代码。

- **依赖单向**：依赖边只沿 D1 允许的方向流动。网状依赖一旦成形不可逆，治于未乱。
- **名实一致**：文件、模块、符号的名称与承载内容一致。名不符实时迁移内容，不为便利收留杂物。
- **单一来源**：同一事实（默认值、白名单、契约、清单）只在一处定义，其余位置引用。拷贝即漂移。
- **封闭集合**：准入类规则（包根、执行面、冻结面）是封闭集合。新增成员须同时改规范与守护，例外不得渗透。

## 架构总览

包按自上而下的顺序分层，依赖只向下流动（唯一例外是 server⇄webapp 共生环，见 D3）；跨层与逐层依赖均可。边的全集以 D1 表为准。

```
server · webapp    MCP 装配与 Web 装配（装配域）；互相仅函数级延迟导入（共生环）
tools              能力层：schemas · pipeline · handlers · outputs · runners
client             火山引擎 API 客户端
config             多层配置构建
utils              通用原语：core · io · images · model
definitions        项目级定义：defaults · conventions——零依赖，任意包可依赖（D1 豁免）
version.py         版本标识——零依赖，任意包可导入（D1 豁免）
```

utils 组内方向：`model` 为组内叶子，`core` 依赖 `model`，`io` 与 `images` 依赖 `core`（`images` 另依赖 `io`）。

一次生成请求的旅程：MCP 客户端发起工具调用 → `server` 经注册的平铺签名定位工具（S4）→ `tools.runners` 入口 → `tools.handlers` 薄适配（S5）→ `tools.pipeline` 编排（输入校验、并发准入、API 调用、结果抽取、自动保存）→ `client` 调用火山引擎 API → 响应回流：`client` 读取响应体（流式或非流式）→ `pipeline` 抽取图片条目并触发自动保存（`utils.io` 下载与落盘、`utils.images` 缩略图）→ `tools.outputs` 按契约构建工具结果 → `server` 序列化返回。Web 操作台的生成入口与 MCP 工具共用同一能力层（S3）。

## 术语表

| 术语           | 定义                                                                                                                        |
| -------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 身份文件       | 包根的 `__init__.py`／`__main__.py`／`version.py`／`py.typed`                                                               |
| 域包           | 包根下领域子包（client/config/definitions/server/tools/utils/webapp）                                                       |
| 定义域         | `definitions` 包，项目级定义的唯一归属（见 S8）                                                                             |
| 项目级定义     | 描述项目整体约定或供跨模块共享、不承载单一域知识的定义（常量、默认值、枚举等）                                              |
| 域级常量       | 承载域知识的常量，留在所属域（如 `webapp/constants.py`、`tools/handlers/_metadata.py`），不因跨域消费上收                   |
| tests 六域镜像 | tests/ 下与域包同名的六个子目录（client/config/server/tools/utils/webapp）；definitions 无独立 tests 域，其守护归 contracts |
| 门面           | 包 `__init__.py` 的再导出职责；门面模块＝仅承担再导出与包级组装副作用的薄 `__init__.py`                                     |
| 契约层         | `tools/schemas.py` 与 `tools/outputs/`                                                                                      |
| 公共契约面     | 契约层的全部公共符号（契约模型、描述常量、构建与序列化原语）                                                                |
| 能力层         | `tools/` 全包                                                                                                               |
| 装配域         | `server/`（MCP 装配）与 `webapp/`（Web 装配）                                                                               |
| 执行面三件套   | runners 入口＋公共契约面＋通道作用域                                                                                        |
| 共生环         | server⇄webapp 经函数级延迟导入的受控双向依赖（D3 白名单承载）                                                               |
| 段终态         | 该段绿提交落点时的目标状态                                                                                                  |
| 冻结面         | 见 S2                                                                                                                       |

## 1 结构规则

**S1 包根准入**

- 意图：包根即架构索引——扫一眼包根即知全项目分域，零散模块破坏这一可读性。
- 规则：包根仅允许身份文件、域包与 skills 数据目录驻留；新增领域须建子包，禁止向包根添加模块。三方文档快照隔离于 `docs/vendor/`，快照内容保真、不参与清扫与验收硬门。
- 机器执行：D4-1。

**S2 公共导入面冻结**

- 意图：公开路径是对外承诺；承诺面小而明确，内部重组才自由。
- 规则：
  - 冻结面＝`seedream_mcp`、`seedream_mcp.client`、`seedream_mcp.config`、`seedream_mcp.server`、`seedream_mcp.tools`、`version.__version__` 与各门面 `__all__`／`_LAZY_EXPORTS` 所列公开名。
  - 冻结的公开名以 `__all__`／`_LAZY_EXPORTS` 的现值为准；无 `__all__` 的模块其公开名不入冻结面（其门面再导出面由段 3c 名单生成制确立，确立后经 D4 冻结面断言锁定）。
  - 该面内路径与所列公开名不得移动或移除；冻结面内的路径保持以「模块转包」（module→同名 package）实现。
  - 非冻结面的域内重组不受本条限制（受 S1 与 N 系列约束）。
  - 无下划线不因此获得公共性；公共性仅由冻结面定义（见 N5）。除冻结面外一切模块路径均为内部实现，不构成兼容承诺。
- 机器执行：D4-4。

**S3 能力层消费**

- 意图：能力层与装配解耦——消费面封闭，tools 得以独立演进而不被装配细节倒灌。
- 规则：
  - 消费方（webapp 及未来接入方）调用能力层，仅允许经执行面三件套：`tools.runners` 的 run\_\*；`tools.schemas` 与 `tools.outputs` 的公共契约面；`tools.outputs.channels` 的通道作用域。
  - 装配域 server 另仅允许消费装配支撑面白名单：`tools.pipeline` 门面的 `get_lifespan_resource`、`tools.handlers.get_model_info` 的 `build_model_info_description`；白名单变更须修改本规范。
  - `tools/` 禁止依赖 `server/`、`webapp/`。
- 机器执行：D4-3。

**S4 MCP 协议适配集中**

- 意图：协议适配单点集中——注册逻辑不散落，审查一处看全。
- 规则：
  - 平铺参数签名镜像、inputSchema 收紧（`_tighten_flat_tool_schemas`）、outputSchema 注解、prompts 与 resources 的全部 `@mcp` 装饰注册，仅允许发生在 `server/_registration.py`。
  - skills 的常量、路径锚定与读取函数（含 `_read_packaged_text`）位于 `server/skills.py`（无装饰器）。
  - skill 资源 handler 与 `_skill_manifest_payload` 缓存全局位于 `server/_registration.py`。

**S5 一工具一模块**

- 意图：工具归属唯一，找实现零歧义。
- 规则：`tools/handlers/<tool>.py` 为该工具唯一归属，仅允许两种形态之一：生成类＝薄适配（经 `pipeline` 门面委托编排）；独立类＝逻辑自持。形态随工具本质选择。

**S6 契约置顶**

- 意图：输入输出契约是能力层的公共语言，置于包级使消费方免知内部结构。
- 规则：输入契约（`tools/schemas.py`）与输出契约（`tools/outputs/` 包）置于 tools 包级。

**S7 名字与位置解耦**

- 意图：迁移不改名、改名不迁移——Git 历史与全文检索二者其一恒定。
- 规则：模块名语义与所在包无关；模块迁移禁止伴随改名——本次重构按第二分册 §6 既定映射整体除外，此后即生效。

**S8 定义域安置**

- 意图：项目级定义须有唯一归属，不寄居名实不符的模块；按类型生长而规范不动。
- 规则：项目级定义置于 `definitions` 域，域内按定义类型分模块——可配置默认值入 `defaults`，不可配置硬约定入 `conventions`，未来新类型（枚举、哨兵等）按类型另立模块。跨域协议标识（如工具名）属项目级定义。域级常量留在所属域（如 `webapp/constants.py` 的路由与安全头、`tools/handlers/_metadata.py` 的工具元数据），即使被跨域消费也不上收。`definitions` 零依赖，任意包可依赖（D1 豁免）。
- 机器执行：D4-1、D4-2。
- 参见：N4、D1、§4 近名对。

## 2 命名规则

**N1**

- 意图：包名是域的门牌，杂物名掩盖内容。
- 规则：包名＝领域名词；禁止 `common`／`impl`／`misc`／`support`／`helpers`／`shared`。

**N2**

- 意图：模块名自述职责——stutter 徒增噪音，动词名须真承载机制。
- 规则：模块名＝职责名词，禁止重复宿主包名（no-stutter）。机制动词模块名须满足判定式：名称表述单一机制动作且无名词同义替代；动词／名词同形名按承载内容判定（承载动作实现→动词名；承载数据或容器→名词名）。冻结清单（以迁移终态为准，阶段 1 落盘时核对）：`settle`、`_execute`、`save`、`auto_save`、`prepare`、`download`、`scan`、`sanitize`、`bounded_store`、`bootstrap`。新增须依判定式并修改本规范。

**N3**

- 意图：泛名靠路径消歧——消不动即名字太泛。
- 规则：泛名单词须在完整路径消歧后仍准确（`utils/io/files.py` 允许；`utils/files.py` 禁止）。

**N4**

- 意图：复数名承诺集合——不足两个异构成员即名不副实。
- 规则：复数名＝同族原语集合：现存复数名冻结；新增复数名须由 ≥2 个异构成员构成。示例：`io/files.py` 承载文件打开、原子落盘、常规文件验证、重解析属性判定等多种文件原语；`images/inputs.py` 承载 URL、data URI、本地文件三种输入形态的归一；`definitions` 域承载 defaults（可配置默认值）与 conventions（不可配置硬约定）两类定义。协议专有名词白名单：`roots`、`sse`。

**N5**

- 意图：下划线是稳定性信号，不是访问控制。
- 规则：下划线前缀＝不对最终用户承诺稳定性。判定范围＝生产代码；判定粒度＝模块；测试属第一方，白盒导入下划线模块豁免；符号级下划线跨包引用不禁止。下划线与公共性判定无关（公共性仅由 S2 冻结面定义）。

**N6**

- 意图：门面薄则包结构透明，再导出同名使 mypy 严格可查。
- 规则：门面＝薄 `__init__.py`：仅承担再导出与包级组装副作用；实现主体住具名模块。例外：契约实现载体型 `__init__`（如 `tools/outputs/__init__`＝输出契约的实现与再导出一体）须在 repo-layout.md 注记。再导出一律 `X as X` 同名形态（mypy strict 的 no_implicit_reexport）。注册载体模块须被门面急切导入，禁止 PEP 562 懒加载（根与 tools 门面的 `_LAZY_EXPORTS` 机制不受本条限制）。

**N7**

- 意图：私有模块同样要有清晰身份——杂居模块无法被命名引用。
- 规则：私有模块按单一关注点命名，禁止杂居；仲裁条款：「`@mcp` 装饰注册」仲裁为单一关注点；「`pipeline/_execute` 生成编排主体」（`ToolMetadata` 元数据、`execute_generation_handler` 入口与阶段函数同属编排关注点）仲裁为单一关注点。

**N8**

- 意图：测试归属可判定、名字全树唯一——检索定位一步直达。
- 规则：测试文件名沿用 `test_<主题>`；tests 全树 test 文件 basename 须唯一。测试文件归属＝按被测模块的域包第一段入 tests 六域镜像之一；不可判定时按文件名首主题词映射域（如 test*tool_parameter_order→tools、test_server_lifespan→server）；仍不可判定时以主导被测面裁定，并于 PR 清单记录理由。辅助模块入 `tests/_lib/` 时须去除前导下划线，命名＝`<主题>`（可含下划线复合词）或 `<主题>*<形态>`（形态∈fakes/fixtures/helpers/spy；尾段不属形态白名单时整体视作主题，如 `no_param_tools`）。补丁纪律＝patch 调用点解析名字的命名空间（定义模块与调用点命名空间不同时，打调用点）。
- 机器执行：D4-5。

**N9**

- 意图：与 Python 生态惯例一致，跨项目读者零成本。
- 规则：模块 snake_case、类 PascalCase、常量与环境变量全大写。

## 3 依赖规则

**D1 跨包边**

- 意图：依赖方向治于未乱——表未允许即禁止。
- 规则（源→允许目标）：

| 源                           | 允许目标                                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------------------- |
| definitions                  | 无（纯叶子）                                                                                             |
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

豁免：任意包→`version.py` 与 `definitions`（零依赖的定义与版本，任意包可依赖）。粒度注记：client/config/server/tools/utils/webapp 作为依赖目标时按整包（或整组）计（含其全部子模块，如 pipeline→client.request_plan 计为 pipeline→client；utils 目标含 core/images/io/model 全部子模块）。

- 机器执行：D4-2。

**D2 模块级无环**

- 意图：模块级无环保证导入顺序可预期。
- 规则：全树（含包内）模块级导入须无环。
- 机器执行：D4-2。

**D3 函数级导入白名单**

- 意图：延迟导入是受控例外（共生环与惰性链），例外全部登记在册。
- 规则：
  - 作用域＝seedream_mcp 包内目标（标准库与第三方导入不入判定）；函数级（延迟）导入仅允许登记于白名单的 (源模块→目标模块) 二元组；新增须先登记再使用。
  - 白名单的权威载体＝守护测试内的常量（单一来源），本规范附录 A 为快照、以守护为准；规范变更与守护常量须同一 PR 修改；守护引入时以机械扫描存量校正。
- 机器执行：D4-2。

**D4 机器执行**

- 意图：条款的权威源是机器断言——文字会漂，测试不会。
- 规则：下列断言由 AST 守护测试执行（并入 `test_consistency_guards.py`）：

1. **包根准入断言**：包根目录与文件清单 ⊆ 封闭集合（身份文件＋七域包＋skills/，S1）。
2. **依赖断言**：D1 仅判跨包边，包内边仅受 D2（无环）约束；模块级与 TYPE_CHECKING 跨包导入按 D1 判定；函数级导入按 D3 白名单；相对导入按 level 解析为绝对模块后判边；两个门面的 `_LAZY_EXPORTS` 字符串表解析入图，`import_module` 禁止出现于门面以外模块；全树模块级导入图（runtime＋TYPE_CHECKING＋门面延迟导出）无环（D2）；测试文件不入依赖判定。
3. **S3 断言**：webapp 对 tools 的导入集 ⊆ 执行面三件套封闭集；server 对 tools 的导入集 ⊆ 执行面三件套封闭集 ∪ 装配支撑面白名单。
4. **冻结面断言**：S2 五路径可导入；门面 `__all__` 快照的封闭集合＝根 `__init__`、tools、client、config、server 五门面（pipeline 门面 `__all__` 沿用 common.py 现值顺迁、outputs 门面同步定义 `__all__`，均于阶段 4 落定）；各门面 `__all__` 与守护内快照逐字一致。
5. **tests 结构断言**（随阶段 5 启用）：test 文件全树 basename 唯一；conftest.py 仅存在于 tests 根；tests 六域镜像与 contracts/e2e 子目录无 `__init__.py`；`_lib` 有 `__init__.py`。

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
| `webapp/constants` ↔ `definitions`           | 域级常量（webapp 专属） ↔ 项目级定义    |

保留注记：`utils/model` 单文件组（语义分组）；`io/download` 不拆（下载管线与 SSRF 防护内聚）。

## 5 扩展路径

- **新工具**＝`handlers/` 单模块＋四处同步点（终态落点：`tools/schemas.py` 输入模型 → `server/_registration.py` 平铺签名镜像 → `tools/pipeline/context.py` 能力补校验 → `client/_api.py` 公共校验入口）。
- **新消费方**＝执行面三件套（S3）。
- **新共享定义**＝definitions 域内按类型定模块（S8）。
- **handlers 内共享非工具模块**＝私有支撑模块（如 `_metadata`），按 N7 单一关注点命名。
- **新域**＝新子包；**新 utils 原语**＝按 N 系列入组。
- 结构规则变更须修改本规范并随 PR 评审（见阅读指南·文档维护约定）。

## 附录 A：D3 函数级导入白名单快照

权威源＝`tests/contracts/test_consistency_guards.py` 的 `_ALLOWED_FUNCTION_IMPORTS` 常量；本附录为快照，以守护为准；规范变更与守护常量须同一 PR 修改。

| 源 → 目标                                                         | 备注                                                            |
| ----------------------------------------------------------------- | --------------------------------------------------------------- |
| server.transport → webapp；server.transport → webapp.constants    | 共生环（web 侧挂载）                                            |
| server.transport → server.runtime                                 | 包内延迟                                                        |
| server.runtime → client                                           | 借共享客户端                                                    |
| server.runtime → config                                           | 复位协议                                                        |
| server.runtime → config.sources                                   | 复位协议（构建告警缓冲清理）                                    |
| server.runtime → utils.io.{save,files,path,scan,storage,download} | 复位协议（六模块）                                              |
| server.runtime → utils.images.thumbnail                           | 复位协议                                                        |
| webapp.routes → server.runtime；webapp.context → server           | 共生环（server 侧借柄）                                         |
| webapp.routes → webapp.{files,gallery,generate,meta}              | 域内懒装配                                                      |
| utils.images.thumbnail → utils.core.formats                       | PIL 惰性链                                                      |
| utils.io.files → utils.core.logs                                  | 既有延迟                                                        |
| config.\_model → config.\_builder                                 | 仅限 `SeedreamConfig.from_env` 调用点；其他函数级导入须另行登记 |

## 附录 B：变更记录

| 版本      | 日期       | 要点                                                                                                                                                                                                                                                                                                     |
| --------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.10     | 2026-10-28 | runner 失败码 BROWSE_FAILED_ERROR_TYPE / MODEL_INFO_FAILED_ERROR_TYPE 上收 definitions/conventions，handler 生产侧与 webapp/\_responses 两侧同引；守护测试更名 test_error_type_slot_values_registered，语义调整为 error_type 槽位取值同源校验                                                            |
| v1.9      | 2026-10-26 | 并行生成上限 MAX_PARALLEL_REQUEST_COUNT 上收 definitions/conventions，validators 改为引用并同步全部消费方导入，单点定义入 test_consistency_guards 守护                                                                                                                                                   |
| v1.8      | 2026-10-20 | 新增 definitions 定义域（S8、D1 行与豁免、术语、近名对、扩展路径）；新增阅读指南、设计原则、架构总览三章；「六域」双义拆解为「tests 六域镜像」与七域「域包」；D3 快照外移附录 A；全部条款增补意图行，有守护锚点的条款补机器执行行；行文约定改引 writing-guide.md；工具名常量上收 definitions/conventions |
| v1.7      | 2026-10-16 | 落盘后演进（装配去配置参数、持锁归类守护等），详见 Git 历史                                                                                                                                                                                                                                              |
| v1.0–v1.6 | 2026-10-15 | 规范落盘与 tools 四层重组期间演进，详见 Git 历史                                                                                                                                                                                                                                                         |
