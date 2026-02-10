# Seedream MCP 系统审查问题清单（2026-02-10）

## 1. 审查说明

- 审查日期：2026-02-10
- 审查范围：`seedream_mcp/`、`tests/`、`Dockerfile`、`docker-compose.yml`、`.github/workflows/release.yml`、`pyproject.toml`
- 审查维度：架构、代码质量、性能、可维护性、安全性
- 基线结果：
  - `pytest -q`：13 passed
  - `mypy seedream_mcp`：通过
  - `flake8 seedream_mcp tests`：失败（存在大量规则不一致/未生效问题）

## 2. 处理原则

- 按优先级分批处理：`P0 -> P1 -> P2`
- 每个问题修复必须包含：
  - 代码改动
  - 最小可复现测试或回归测试
  - 文档更新（如涉及行为变化）
- 不引入冗余兼容层，不做“历史包袱式”双轨长期保留

## 3. 问题总览

| ID    | 优先级 | 维度               | 问题摘要                                          | 状态   |
| ----- | ------ | ------------------ | ------------------------------------------------- | ------ |
| P0-01 | P0     | 架构/可维护性      | `docker-compose.yml` 使用无效端口映射，配置不可用 | 已完成 |
| P1-01 | P1     | 代码质量/可维护性  | CI 未执行真实测试与静态检查，回归门禁不足         | 已完成 |
| P1-02 | P1     | 代码质量           | Flake8 配置未生效，风格门禁失效                   | 已完成 |
| P1-03 | P1     | 可维护性           | 测试覆盖不足，核心链路缺少回归保护                | 已完成 |
| P1-04 | P1     | 架构/性能/可维护性 | `SeedreamClient._call_api` 职责过重、函数过长     | 已完成 |
| P1-05 | P1     | 架构/代码质量      | 参数校验多层重复，规则一致性风险高                | 已完成 |
| P1-06 | P1     | 安全性             | 日志记录提示词片段，存在隐私泄露风险              | 已完成 |
| P1-07 | P1     | 架构/可维护性      | 配置构建双轨并存，默认值维护重复                  | 已完成 |
| P1-08 | P1     | 安全性/可维护性    | Docker 构建不可复现，供应链与发布一致性风险       | 已完成 |
| P1-09 | P1     | 架构/安全性        | 日志初始化强制接管全局 logging，侵入性过强        | 已完成 |
| P2-01 | P2     | 性能               | 多图输入预处理串行执行，可并发优化                | 已完成 |
| P2-02 | P2     | 性能/安全性        | DNS 预校验无缓存，重复解析开销偏高                | 已完成 |
| P2-03 | P2     | 可维护性/安全性    | 保存路径异常时静默回退，掩盖配置错误              | 已完成 |
| P2-04 | P2     | 架构/可维护性      | 包入口 eager import 耦合偏高                      | 已完成 |
| P2-05 | P2     | 可维护性           | `pyproject.toml` 依赖声明重复维护                 | 已完成 |

---

## 4. 详细问题与改进建议

### P0-01 `docker-compose.yml` 使用无效端口映射

- 维度：架构、可维护性
- 证据：
  - （修复前）`docker-compose.yml:34` `- "stdin:stdin"`
  - （修复前）`docker-compose.yml:35` `- "stdout:stdout"`
- 影响：
  - Compose 配置在标准语义下无效，部署存在硬失败风险。
- 建议：
  - 删除上述 `ports` 项。
  - 明确区分两类部署模板：
    - `stdio`（无端口暴露）
    - `sse/streamable-http`（暴露明确 HTTP 端口）
- 验收标准：
  - `docker compose config` 校验通过。
  - 两种部署模板均可成功启动并完成健康检查。

#### 调整记录（2026-02-10）

- 变更文件：`docker-compose.yml`
- 变更内容：
  - 删除无效 `ports` 配置块及其中的 `stdin:stdin`、`stdout:stdout` 映射。
  - 保留 `stdin_open: true` 与 `tty: true`，用于 stdio 交互场景。
- 复核结果：
  - `docker-compose.yml` 已不存在 `ports`、`stdin:stdin`、`stdout:stdout` 配置项。
  - 本机未安装 Docker，无法在当前环境执行 `docker compose config` 动态校验。
  - 待在具备 Docker 的环境补充执行：`docker compose config` 与容器启动健康检查。

### P1-01 CI 未执行真实测试与静态检查

- 维度：代码质量、可维护性
- 证据：
  - `.github/workflows/release.yml:40` 仅做快速导入校验
  - `.github/workflows/release.yml:53` 仅做 build，不含 `pytest/mypy/lint`
- 影响：
  - 回归问题可能直接进入发布。
- 建议：
  - CI 最少增加：
    - `pytest -q`
    - `mypy seedream_mcp`
    - 统一 lint（flake8 或 ruff）
  - 将测试门禁与发版 job 强绑定。
- 验收标准：
  - PR 与 tag 发布前必须全部门禁通过。

#### 调整记录（2026-02-10）

- 变更文件：`.github/workflows/release.yml`
- 变更内容：
  - 在 `test` job 增加 `uv run pytest -q`、`uv run mypy seedream_mcp`、`uv run flake8 seedream_mcp tests`。
  - 依赖缓存键由 `pyproject.toml` 调整为 `uv.lock`，与锁文件一致。
- 复核结果：
  - 发布工作流已具备测试、类型检查、风格检查三道门禁，且 release/docker job 均依赖 test job。

### P1-02 Flake8 配置未生效

- 维度：代码质量
- 证据：
  - `pyproject.toml:117` 配置了 `[tool.flake8]`
  - 直接执行 `flake8` 仍按 79 列规则大量报错，说明配置未被读取
- 影响：
  - 代码风格与静态质量规则形同虚设。
- 建议：
  - 方案 A：新增 `.flake8` 作为单一配置源。
  - 方案 B：切换到 `ruff`，统一 lint+部分格式检查。
- 验收标准：
  - 本地与 CI 执行同一命令，规则与结果一致。

#### 调整记录（2026-02-10）

- 变更文件：`.flake8`、`.github/workflows/release.yml`
- 变更内容：
  - 新增 `.flake8` 作为单一 Flake8 配置源（`max-line-length=100` 等）。
  - CI 统一执行 `uv run flake8 seedream_mcp tests`。
- 复核结果：
  - 本地执行 `uv run flake8 seedream_mcp tests` 通过，规则与 CI 一致。

### P1-03 测试覆盖不足

- 维度：可维护性、代码质量
- 证据：
  - 源码模块约 24 个，测试文件仅 3 个（`tests/`）
- 影响：
  - 核心链路重构或参数变更时容易引入不可见回归。
- 建议：
  - 第一阶段补齐：
    - `seedream_mcp/client.py`
    - `seedream_mcp/utils/download_manager.py`
    - `seedream_mcp/utils/auto_save.py`
    - `seedream_mcp/utils/path_utils.py`
    - `seedream_mcp/server.py`
  - 覆盖正向、异常、边界、并发场景。
- 验收标准：
  - 核心模块都有回归测试；关键场景出现问题可复现。

#### 调整记录（2026-02-10）

- 变更文件：
  - `tests/test_client_refactor.py`
  - `tests/test_config_builder.py`
  - `tests/test_logging_setup.py`
  - `tests/test_server_config_fallback.py`
  - `tests/test_sequential_generation_limits.py`
  - `tests/test_validation_prompt.py`
- 变更内容：
  - 新增客户端请求路径、SSE 解析、日志脱敏、工作区相对路径解析等回归测试。
  - 新增统一配置构建优先级、CLI 配置回退、日志接管开关测试。
  - 根据参数校验收敛后的行为，更新 schema 相关测试断言。
- 复核结果：
  - 本地执行 `uv run pytest -q`：`26 passed`。

### P1-04 `_call_api` 函数过长、职责过重

- 维度：架构、性能、可维护性
- 证据：
  - `seedream_mcp/client.py:533` (`_call_api`) 约 300+ 行
- 影响：
  - 修改任何一段逻辑都容易影响全局行为，维护成本高。
- 建议：
  - 拆分为独立组件：
    - 请求发送/重试策略
    - SSE 事件解析器
    - 错误映射器
    - 响应归一化器
- 验收标准：
  - 单函数长度和圈复杂度显著下降。
  - SSE 与非 SSE 路径各自有独立测试。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/client.py`、`tests/test_client_refactor.py`
- 变更内容：
  - 将 `_call_api` 拆分为请求发送、状态码处理、SSE 事件解析、响应归一化、重试控制等多个私有方法。
  - 保留原重试与异常映射行为，同时提升函数可读性与可测试性。
- 复核结果：
  - 新增并通过 SSE 与非 SSE 路径测试，覆盖重构关键逻辑。

### P1-05 参数校验多层重复

- 维度：架构、代码质量
- 证据：
  - `seedream_mcp/tools/core/schemas.py` 与 `seedream_mcp/client.py` 同时校验
  - `validate_image_url` 在 schema/client/path 层多次调用
- 影响：
  - 行为不一致风险增大，后续修复容易“漏一层”。
- 建议：
  - 明确单一权威校验层（推荐 schema + domain validator）。
  - 客户端只保留必要防御校验。
- 验收标准：
  - 每类参数规则只有一个主实现点。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/tools/core/schemas.py`、`seedream_mcp/client.py`
- 变更内容：
  - schema 层保留结构与边界校验，移除多余语义重复校验。
  - 客户端移除 `validate_image_url/validate_image_list` 前置重复调用，改为统一结构校验后由 `_prepare_image_input` 处理。
- 复核结果：
  - 相对路径图片输入在工作区根目录语义下可正常通过，且规则实现点更集中。

### P1-06 日志记录提示词片段

- 维度：安全性
- 证据：
  - `seedream_mcp/client.py:119`、`seedream_mcp/client.py:187`
  - `seedream_mcp/client.py:260`、`seedream_mcp/client.py:371`
- 影响：
  - 提示词可能包含敏感业务信息，日志侧存在泄露风险。
- 建议：
  - 默认脱敏：仅记录长度、hash、统计字段。
  - 增加显式开关控制是否记录明文片段，默认关闭。
- 验收标准：
  - 默认日志不出现 prompt 原文或片段。

#### 调整记录（2026-02-10）

- 变更文件：
  - `seedream_mcp/client.py`
  - `seedream_mcp/tools/impl/text_to_image.py`
  - `seedream_mcp/tools/impl/image_to_image.py`
  - `seedream_mcp/tools/impl/multi_image_fusion.py`
  - `seedream_mcp/tools/impl/sequential_generation.py`
  - `seedream_mcp/tools/core/common.py`
  - `tests/test_client_refactor.py`
- 变更内容：
  - 客户端日志改为 `prompt_meta`（长度 + 摘要哈希），不输出明文片段。
  - 工具层启动日志改为 `prompt_len`，不记录提示词内容。
  - 自动保存 `alt_text` 去除 prompt 片段，避免敏感内容落盘。
- 复核结果：
  - 新增日志脱敏测试通过，默认日志不再包含 prompt 原文。

### P1-07 配置构建双轨并存

- 维度：架构、可维护性
- 证据：
  - `seedream_mcp/server.py:237`（CLI 构建）
  - `seedream_mcp/config.py:174`（from_env 构建）
- 影响：
  - 默认值、映射、优先级规则可能逐步漂移。
- 建议：
  - 建立唯一配置解析器，CLI 仅作为 override 注入。
  - 减少全局状态分散管理。
- 验收标准：
  - 配置优先级在单一实现中可追溯、可测试。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/config.py`、`seedream_mcp/server.py`、`tests/test_config_builder.py`
- 变更内容：
  - 新增统一配置构建入口 `build_config_from_sources`。
  - CLI 构建配置改为 override 注入统一构建路径。
  - 环境优先级统一为：`overrides > 系统环境变量 > .env 文件 > 默认值`。
- 复核结果：
  - 配置优先级测试通过，server 端构建逻辑与 config 模块实现一致。

### P1-08 Docker 构建不可复现

- 维度：安全性、可维护性
- 证据：
  - `Dockerfile:19` `pip install uv` 未固定版本
  - `Dockerfile:30` editable 安装（`-e .`）
  - `Dockerfile:22-24` 未使用 lock 文件进行冻结安装
- 影响：
  - 构建结果随时间漂移，增加供应链与线上一致性风险。
- 建议：
  - 固定基础镜像 digest 与 uv 版本。
  - 使用 `uv.lock` + `uv sync --frozen`。
  - 优先 wheel 安装，避免容器内 editable。
- 验收标准：
  - 同 commit 多次构建得到一致依赖树。

#### 调整记录（2026-02-10）

- 变更文件：`Dockerfile`
- 变更内容：
  - 基础镜像固定为 `python:3.11.11-slim`，并固定 `uv` 版本（`UV_VERSION=0.9.18`）。
  - 复制 `uv.lock`，使用 `uv sync --frozen --no-dev --no-editable` 安装依赖。
  - 运行时统一使用 `/app/.venv`，移除容器内 editable 安装方式。
- 复核结果：
  - 构建链路由“实时解析依赖”改为“锁文件冻结安装”，可复现性显著提升。

### P1-09 日志初始化强制接管全局 logging

- 维度：架构、安全性
- 证据：
  - `seedream_mcp/utils/logging.py:97` `logging.basicConfig(..., force=True)`
- 影响：
  - 作为库被嵌入时会破坏宿主应用日志策略。
- 建议：
  - 默认不 `force=True`。
  - 仅 CLI 入口提供“全局接管模式”。
- 验收标准：
  - 嵌入式使用不影响外部日志配置。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/utils/logging.py`、`seedream_mcp/server.py`、`tests/test_logging_setup.py`
- 变更内容：
  - `setup_logging` 增加 `force_standard_logging` 参数，默认不强制接管。
  - 仅 CLI 启动路径传入 `force_standard_logging=True`。
- 复核结果：
  - 新增日志配置测试通过，库模式与 CLI 模式行为分离明确。

### P2-01 多图输入预处理串行执行

- 维度：性能
- 证据：
  - `seedream_mcp/client.py:266-267`
  - `seedream_mcp/client.py:366-367`
- 影响：
  - 多图场景响应时间随图片数线性增长明显。
- 建议：
  - 采用受限并发（`Semaphore + gather`）处理本地图片读取和编码。
- 验收标准：
  - 多图场景平均耗时显著下降且无并发异常。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/client.py`、`tests/test_client_refactor.py`
- 变更内容：
  - 在客户端新增 `_prepare_images_in_parallel`，使用 `Semaphore + gather` 进行受限并发预处理。
  - `multi_image_fusion` 与 `sequential_generation` 的多图路径由串行循环改为并发处理，保持输入顺序不变。
- 复核结果：
  - 新增并发回归测试，验证多图预处理存在并发执行且请求参数顺序正确。

### P2-02 DNS 预校验缺少缓存与连接后校验

- 维度：性能、安全性
- 证据：
  - `seedream_mcp/utils/download_manager.py:126`
  - `seedream_mcp/utils/download_manager.py:189`
- 影响：
  - 高频下载时重复 DNS 解析增加延迟；防御链仍可加强。
- 建议：
  - 增加短 TTL 的域名解析缓存。
  - 在连接建立后补充远端 IP 安全校验（防 DNS rebinding）。
- 验收标准：
  - 并发下载吞吐提升，SSRF 防护策略文档化并测试覆盖。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/utils/download_manager.py`、`tests/test_download_manager_security.py`
- 变更内容：
  - 增加 DNS 解析缓存（TTL 可配置，默认 60 秒），减少重复 `getaddrinfo` 调用。
  - 新增连接建立后的对端 IP 校验，阻断非公网 IP（补强 DNS rebinding 防护链）。
  - 保持“静态校验 + 重试循环内网络校验”结构，确保瞬时网络故障可重试恢复。
- 复核结果：
  - 新增测试覆盖 DNS 缓存命中与连接后 IP 校验分支，验证逻辑生效。

### P2-03 保存路径异常时静默回退

- 维度：可维护性、安全性
- 证据：
  - `seedream_mcp/utils/file_manager.py:47`
  - `seedream_mcp/utils/file_manager.py:50`
- 影响：
  - 用户配置错误被掩盖，问题排查困难。
- 建议：
  - 默认 fail-fast 抛错，不静默切换路径。
  - 如需容错，必须显式配置并输出清晰告警。
- 验收标准：
  - 路径配置异常能被调用方明确感知。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/utils/file_manager.py`、`tests/test_file_manager_fail_fast.py`
- 变更内容：
  - 移除不安全路径或路径解析失败时的默认目录静默回退逻辑。
  - 调整为 fail-fast：遇到路径异常直接抛出 `FileManagerError`。
- 复核结果：
  - 新增测试覆盖不安全路径、非法路径、合法路径三类场景，行为符合预期。

### P2-04 包入口 eager import 耦合偏高

- 维度：架构、可维护性
- 证据：
  - `seedream_mcp/__init__.py:18`
  - `seedream_mcp/__init__.py:21`
  - `seedream_mcp/__init__.py:24`
- 影响：
  - 增加导入副作用和冷启动成本，扩展性受限。
- 建议：
  - 改为按需导入或分层导出（runtime API / server API）。
- 验收标准：
  - `import seedream_mcp` 不触发不必要的重模块初始化。

#### 调整记录（2026-02-10）

- 变更文件：`seedream_mcp/__init__.py`、`tests/test_package_lazy_import.py`
- 变更内容：
  - 包入口移除顶层 eager import，改为 `__getattr__` 惰性加载公开导出对象。
  - 保留原导出 API 名称，按访问时再加载 `client/config/server` 子模块。
- 复核结果：
  - 新增测试确认 `import seedream_mcp` 不再立即加载 `client/server`，访问导出时才触发加载。

### P2-05 依赖声明重复维护

- 维度：可维护性
- 证据：
  - `pyproject.toml:31` 与 `pyproject.toml:77` 存在重复 runtime 依赖列表
- 影响：
  - 更新依赖时易漏改，长期造成不一致。
- 建议：
  - 保留单一 runtime 依赖源。
  - dev 环境仅补充测试/质量工具依赖。
- 验收标准：
  - 依赖升级只需修改一处定义。

#### 调整记录（2026-02-10）

- 变更文件：`pyproject.toml`、`tests/test_pyproject_dependency_source.py`
- 变更内容：
  - 删除 `[tool.hatch.envs.default]` 中重复的 runtime 依赖列表。
  - 保留 `project.dependencies` 作为唯一 runtime 依赖来源。
- 复核结果：
  - 新增测试校验 runtime 依赖单一来源约束，避免后续重复维护回归。

---

## 5. 本次变更记录（首轮）

| 问题ID | 变更文件                                                                                      | 校验结果                                                       | 结论                   |
| ------ | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ---------------------- |
| P0-01  | `docker-compose.yml`                                                                          | 静态复核通过；`docker compose config` 因本机缺少 Docker 未执行 | 已完成（待补动态校验） |
| P1-01  | `.github/workflows/release.yml`                                                               | 工作流已新增 `pytest/mypy/flake8` 并绑定发布依赖               | 已完成                 |
| P1-02  | `.flake8`、`.github/workflows/release.yml`                                                    | `uv run flake8 seedream_mcp tests` 通过                        | 已完成                 |
| P1-03  | `tests/test_client_refactor.py` 等 6 个测试文件                                               | `uv run pytest -q`：`26 passed`                                | 已完成                 |
| P1-04  | `seedream_mcp/client.py`、`tests/test_client_refactor.py`                                     | SSE/非 SSE 回归测试通过                                        | 已完成                 |
| P1-05  | `seedream_mcp/tools/core/schemas.py`、`seedream_mcp/client.py`                                | 参数校验链路复核通过                                           | 已完成                 |
| P1-06  | `seedream_mcp/client.py`、`seedream_mcp/tools/impl/*.py`、`seedream_mcp/tools/core/common.py` | 提示词日志脱敏测试通过                                         | 已完成                 |
| P1-07  | `seedream_mcp/config.py`、`seedream_mcp/server.py`、`tests/test_config_builder.py`            | 优先级与构建路径测试通过                                       | 已完成                 |
| P1-08  | `Dockerfile`                                                                                  | Docker 安装链路改为 lock 冻结 + 非 editable                    | 已完成                 |
| P1-09  | `seedream_mcp/utils/logging.py`、`seedream_mcp/server.py`、`tests/test_logging_setup.py`      | force 接管开关测试通过                                         | 已完成                 |
| P2-01  | `seedream_mcp/client.py`、`tests/test_client_refactor.py`                                     | 多图预处理并发路径测试通过                                     | 已完成                 |
| P2-02  | `seedream_mcp/utils/download_manager.py`、`tests/test_download_manager_security.py`           | DNS 缓存与连接后 IP 校验测试通过                               | 已完成                 |
| P2-03  | `seedream_mcp/utils/file_manager.py`、`tests/test_file_manager_fail_fast.py`                  | 路径异常 fail-fast 测试通过                                    | 已完成                 |
| P2-04  | `seedream_mcp/__init__.py`、`tests/test_package_lazy_import.py`                               | 包入口惰性导入测试通过                                         | 已完成                 |
| P2-05  | `pyproject.toml`、`tests/test_pyproject_dependency_source.py`                                 | 依赖单一来源约束测试通过                                       | 已完成                 |

## 6. 审查回合补充记录（2026-02-10）

| 回合问题 | 变更文件                                                                                                      | 调整摘要                                                               | 校验结果         |
| -------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ---------------- |
| RV-01    | `seedream_mcp/utils/download_manager.py`、`tests/test_download_manager_security.py`                           | 异步下载链路移除阻塞式 DNS 校验，恢复 DNS 相关失败可重试语义           | `pytest -q` 通过 |
| RV-02    | `seedream_mcp/server.py`、`tests/test_server_config_fallback.py`                                              | `_active_config` 未注入时回退全局配置，恢复非 CLI 启动路径可用性       | `pytest -q` 通过 |
| RV-03    | `seedream_mcp/client.py`、`tests/test_client_refactor.py`                                                     | 图片相对路径在校验前按 `SEEDREAM_WORKSPACE_ROOT` 统一解析              | `pytest -q` 通过 |
| RV-04    | `seedream_mcp/tools/core/common.py`                                                                           | 自动保存目录不可用时降级跳过自动保存，不中断主生成流程                 | `pytest -q` 通过 |
| RV-05    | `seedream_mcp/client.py`、`tests/test_client_refactor.py`                                                     | 恢复 Data URI 本地语义校验，错误或超大输入在请求前拦截                 | `pytest -q` 通过 |
| RV-06    | `.github/workflows/release.yml`、`tests/test_pyproject_dependency_source.py`                                  | 增加 Python 3.10 `tomli` 回退，并在 CI 中显式安装 `pytest/mypy/flake8` | `pytest -q` 通过 |
| RV-07    | `seedream_mcp/tools/core/schemas.py`、`tests/test_sequential_generation_limits.py`                            | `model_validator` 中限额校验异常统一封装为 `ValueError`                | `pytest -q` 通过 |
| RV-08    | `seedream_mcp/config.py`、`tests/test_config_builder.py`                                                      | 恢复 `.env` 注入环境变量，默认读取项目根与 cwd `.env` 并合并           | `pytest -q` 通过 |
| RV-09    | `seedream_mcp/config.py`、`tests/test_config_builder.py`                                                      | 修复显式 `env_file` 重复构建时被上次注入值污染的问题                   | `pytest -q` 通过 |
| RV-10    | `seedream_mcp/config.py`、`tests/test_config_builder.py`                                                      | 修复运行时动态设置环境变量被 `.env` 覆盖的优先级回归                   | `pytest -q` 通过 |
| RV-11    | `seedream_mcp/client.py`、`tests/test_client_refactor.py`                                                     | 图片参数结构校验恢复抛出 `SeedreamValidationError`                     | `pytest -q` 通过 |
| RV-12    | `seedream_mcp/tools/core/schemas.py`、`seedream_mcp/tools/core/common.py`、`tests/test_generation_context.py` | 显式空 `size` 输入改为报错，禁止静默回退默认值                         | `pytest -q` 通过 |

补充复核结论：

- 当前工作区全量测试：`pytest -q` => `48 passed in 1.53s`。

## 7. 变更记录模板

| 问题ID      | 修复分支/提交 | 变更文件                 | 测试结果         | 结论   |
| ----------- | ------------- | ------------------------ | ---------------- | ------ |
| 示例：P1-04 | `abc1234`     | `seedream_mcp/client.py` | `pytest -q` 通过 | 已完成 |
