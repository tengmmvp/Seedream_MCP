# Seedream API 对齐审查问题清单

## 1. 审查范围

- 官方文档（图片生成 API）：https://www.volcengine.com/docs/82379/1541523?lang=zh
- 官方文档（流式响应）：https://www.volcengine.com/docs/82379/1824137?lang=zh
- 本地实现：
  - `seedream_mcp/client.py`
  - `seedream_mcp/tools/core/schemas.py`
  - `seedream_mcp/utils/validation.py`
  - `seedream_mcp/config.py`
  - `seedream_mcp/tools/impl/sequential_generation.py`

> 说明：本清单仅记录“与官方文档不一致或存在行为风险”的项，不重复记录已对齐项。

---

## 2. 问题总览

| ID    | 优先级 | 维度              | 问题摘要                                                  | 状态     |
| ----- | ------ | ----------------- | --------------------------------------------------------- | -------- |
| P0-01 | P0     | 兼容性/功能正确性 | 多图融合参考图上限实现为 5，文档要求最大 14               | 已完成   |
| P0-02 | P0     | 兼容性/功能正确性 | `size` 未支持像素值输入（如 `2560x1440`）                 | 已完成   |
| P1-01 | P1     | 稳定性/可观测性   | SSE 流式解析未处理 `image_generation.partial_failed` 事件 | 已完成   |
| P1-02 | P1     | 产品策略          | `watermark` 默认值为 `false`，与官方默认 `true` 不一致    | 项目设计 |
| P1-03 | P1     | 行为一致性        | `max_images` 默认值为 4，与文档默认 15 不一致             | 已完成   |
| P2-01 | P2     | 可维护性          | 组图参考图上限表达不直观（允许 15，再靠总和限制兜底）     | 已完成   |

---

## 3. 详细问题与建议

### P0-01 多图融合参考图上限过低

- 文档要求：4.5/4.0 多图生图支持 `2-14` 张参考图。
- 当前实现：
  - `seedream_mcp/tools/core/schemas.py` 中 `MultiImageFusionInput.image` 为 `max_length=5`
  - `seedream_mcp/client.py` 调用 `_normalize_image_sequence(... max_count=5 ...)`
  - `seedream_mcp/utils/validation.py` 的 `validate_image_list` 默认上限也偏低
- 影响：合法请求在本地校验阶段被拒绝，功能能力低于官方。
- 建议修复：
  - 统一把多图融合参考图上限改为 14。
  - 同步更新 schema、client、validation 与测试用例。
- 验收标准：
  - 2-14 张图请求可通过本地校验并成功发起 API 调用。
  - > 14 张图请求返回明确参数校验错误。

调整记录（2026-02-10）：

- 将 `MultiImageFusionInput.image` 上限从 5 调整为 14（`seedream_mcp/tools/core/schemas.py`）。
- 将 `SeedreamClient.multi_image_fusion` 的本地数量校验上限从 5 调整为 14（`seedream_mcp/client.py`）。
- 将 `validate_image_list` 默认上限从 5 调整为 14（`seedream_mcp/utils/validation.py`）。
- 补充测试：
  - `test_multi_image_fusion_accepts_up_to_14_images`
  - `test_multi_image_fusion_rejects_more_than_14_images`
  - 文件：`tests/test_client_refactor.py`

### P0-02 `size` 未支持宽高像素值

- 文档要求：`size` 支持两类输入：
  - 分辨率档位（如 2K/4K）
  - 宽高像素值（如 `2560x1440`，并受模型约束）
- 当前实现：
  - `seedream_mcp/utils/validation.py` 的 `validate_size` 仅允许 `1K/2K/4K`
- 影响：官方允许的像素尺寸调用被本地拦截，造成能力缺失。
- 建议修复：
  - `validate_size_for_model` 增加像素值解析和模型分支校验。
  - 保留现有档位校验，同时新增 `^\d+x\d+$` 规则分支。
  - 按模型限制校验总像素和宽高比范围。
- 验收标准：
  - 合法像素值可通过并发起请求。
  - 非法像素值给出明确、可定位的错误信息。

调整记录（2026-02-10）：

- `validate_size` 新增 `<宽>x<高>` 像素格式支持，并统一规范化输出（`seedream_mcp/utils/validation.py`）。
- `validate_size_for_model` 新增像素尺寸的模型约束校验：
  - `doubao-seedream-4.5/4.0`：总像素范围 `[2560x1440, 4096x4096]`。
  - `doubao-seedream-3.0/doubao-seededit-3.0`：总像素范围 `[512x512, 2048x2048]`。
- `SeedreamConfig.default_size` 校验支持像素值（`seedream_mcp/config.py`）。
- CLI 参数 `--default-size` 放宽为字符串输入，支持像素值（`seedream_mcp/server.py`）。
- 补充测试（文件 `tests/test_size_validation.py`）：
  - 像素值正向校验
  - 大小写 `X` 规范化
  - 越界拒绝
  - 配置层像素默认值可用

### P1-01 SSE 未处理 `partial_failed` 事件

- 文档要求：流式事件包括：
  - `image_generation.partial_succeeded`
  - `image_generation.partial_failed`
  - `image_generation.completed`
- 当前实现：
  - `seedream_mcp/client.py` 仅处理 `partial_succeeded` 与 `completed`
  - `partial_failed` 未纳入结果结构
- 影响：流式过程中单图失败信息丢失，调用方无法获取失败明细与错误码。
- 建议修复：
  - 在 SSE 解析中补充 `partial_failed` 分支，透传 `image_index`、`error.code`、`error.message`。
  - 统一 data 项结构，保证成功项与失败项可区分且可追踪。
- 验收标准：
  - 模拟 `partial_failed` 事件后，返回结果包含失败项与错误信息。

调整记录（2026-02-10）：

- 新增 `_format_sse_failed_event`，在 SSE 解析中支持 `image_generation.partial_failed`（`seedream_mcp/client.py`）。
- 失败事件透传 `error.code`、`error.message`、`image_index`、`type` 字段。
- 响应文本格式补充失败项渲染（`seedream_mcp/tools/core/common.py`）。
- 补充测试 `test_call_api_parses_sse_partial_failed_event`（`tests/test_client_refactor.py`）。

### P1-02 `watermark` 默认值与文档不一致

- 文档默认：`watermark=true`。
- 当前实现：
  - `seedream_mcp/config.py` 默认 `SEEDREAM_DEFAULT_WATERMARK=false`
  - `SeedreamConfig.default_watermark=False`
  - `SeedreamClient.*` 方法参数默认 `watermark=False`
- 结论：该差异为项目既定策略，保持默认无水印，不作为缺陷处理。
- 落地要求：
  - 对应测试保留默认无水印断言，防止后续误改。

调整记录（2026-02-10）：

- 在 README 环境变量说明处新增“默认无水印为项目有意设计，与官方默认不同”的明确声明（`README.md`）。

### P1-03 `max_images` 默认值与文档不一致

- 文档默认：`sequential_image_generation_options.max_images=15`。
- 当前实现：
  - `seedream_mcp/tools/core/schemas.py` 默认 4
  - `seedream_mcp/client.py` 默认 4
  - `seedream_mcp/tools/impl/sequential_generation.py` 默认 4
- 影响：不传该参数时生成数量策略偏离官方预期。
- 建议修复（两选一）：
  - 方案 A：统一默认值改为 15。
  - 方案 B：保留 4 作为产品策略，并在文档明确“显式偏离官方默认值”。
- 验收标准：
  - 代码默认值、文档说明、测试断言三者一致。

调整记录（2026-02-10）：

- `SequentialGenerationInput.max_images` 默认值由 4 改为 15（`seedream_mcp/tools/core/schemas.py`）。
- `SeedreamClient.sequential_generation` 默认值由 4 改为 15（`seedream_mcp/client.py`）。
- 工具层默认值同步为 15（`seedream_mcp/tools/impl/sequential_generation.py`）。
- README 工具参数说明同步更新默认值（`README.md`）。
- 补充测试：
  - `test_sequential_generation_default_max_images_is_15`
  - 文件：`tests/test_sequential_generation_limits.py`

### P2-01 组图参考图上限表达不直观

- 文档语义：参考图最多 14，且“参考图数量 + 生成数量 <= 15”。
- 当前实现：
  - 顺序生图输入校验允许参考图最多 15，再由总和校验兜底
- 影响：虽然多数场景不会放过非法请求，但规则分散、可读性一般。
- 建议修复：
  - 将参考图硬上限改为 14，再叠加总和限制。
  - 错误信息中同时输出“参考图上限”和“总和上限”。
- 验收标准：
  - 校验规则与文档描述一致，错误提示可直接指导调用方修正参数。

调整记录（2026-02-10）：

- 组图参考图上限统一为 14（`seedream_mcp/tools/core/schemas.py`、`seedream_mcp/client.py`）。
- `validate_sequential_image_limit` 增强为双重约束：
  - 参考图数量 <= 14
  - 参考图数量 + 生成数量 <= 15
  - 文件：`seedream_mcp/utils/validation.py`
- README 参数说明同步明确“参考图最多 14”。
- 补充测试（`tests/test_sequential_generation_limits.py`）：
  - `test_sequential_generation_reference_images_max_14_ok`
  - `test_sequential_generation_reference_images_exceed_14`
