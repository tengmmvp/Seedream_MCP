---
name: seedream-image-generation
description: Seedream 图像生成 MCP 服务器的使用指南，覆盖文生图、图生图、多图融合、组图生成与图层拆分。当用户要求生成图片、画图、改图、换风格、融合多张图、制作连环画或故事书、拆分图层、生成透明背景或原生 RGBA 素材、识别图层文字，或需要调用 text_to_image、image_to_image、multi_image_fusion、sequential_generation、layer_decompose、browse_images、get_model_info 工具，确认当前模型能力与尺寸档位，排查 401/402/403/413/429 报错，以及找回已保存的图片时使用本技能。Use when generating or editing images via the Seedream MCP server.
---

# Seedream 图像生成指南

## 何时使用本技能

- 用户要求生成图片、画图、作画（文生图）
- 用户要求修改图片、换风格、换背景、去水印元素（图生图）
- 用户要求把多张图片融合、合成、拼贴（多图融合）
- 用户要求制作连环画、故事书、分镜组图（组图生成）
- 用户要求拆分图层、生成透明背景或原生 RGBA 素材、识别图层文字（图层拆分）
- 用户询问当前模型与能力，或要找回之前生成的图片

## 环境与前置

- 本 skill 假定客户端已连接 Seedream MCP 服务器并完成鉴权配置
- 生效模型由服务器配置决定，单次调用不可切换
- 不确定当前配置时，先调用 `get_model_info` 查询当前模型的能力快照，或读资源 `seedream://server/info` 确认默认尺寸与自动保存开关

## 工具速查

| 工具                    | 用途                 | 必需参数                    | 关键限制                |
| ----------------------- | -------------------- | --------------------------- | ----------------------- |
| `text_to_image`         | 文生图               | `prompt`                    | 无                      |
| `image_to_image`        | 图生图、编辑         | `prompt`、`image`           | 透明背景需当前模型支持  |
| `multi_image_fusion`    | 多张参考图融合       | `prompt`、`image`（2 张起） | 用"图1/图2"引用各输入图 |
| `sequential_generation` | 一次生成一组连贯组图 | `prompt`                    | 组图需当前模型支持      |
| `layer_decompose`       | 拆分图层与富文字识别 | `image`                     | 图层拆分需当前模型支持  |
| `browse_images`         | 浏览已保存图片       | 无（全部可选）              | 只读，不访问网络        |
| `get_model_info`        | 查询当前模型能力     | 无                          | 只读，不访问网络        |

## 模型差异

模型由部署方经服务器配置选定，以下为各档定位，单次调用不可切换：

- `doubao-seedream-5.5`（默认）：图层拆分（1 张底图 + 至多 99 个图层）、富文本 OCR、透明背景与原生 RGBA 的最新旗舰档，多轮编辑一致性最强
- `doubao-seedream-5.0-pro`：画质最高的旗舰档，支持图层拆分（至多 16 个图层）与透明背景
- `doubao-seedream-5.0`：能力面最全的均衡档，支持组图、联网搜索与流式输出
- `doubao-seedream-5.0-flash`：生成最快、单价最低的极速档，支持图层拆分与透明背景
- `doubao-seedream-4.5` / `doubao-seedream-4.0`：上一代模型

当前模型的准确能力以调用 `get_model_info` 工具为准，全部模型清单与能力声明可读 `seedream://models/info` 资源，不要凭记忆复述像素区间、档位清单等数值。

## 提示词写法

- 用连贯自然语言写明主体 + 行为 + 环境，再以短语补充风格、色彩、光影、构图等美学元素
- 建议不超过 300 个汉字或 600 个英文单词；超出后信息分散，模型可能忽略细节
- 组图与多图融合场景给每张图独立的画面描述，保持叙事顺序
- 服务器内置四个风格预设 prompt：`seedream_style_anime`、`seedream_style_realistic`、`seedream_style_watercolor`、`seedream_style_oil_painting`，可作为风格后缀参考

## 关键参数规则

- 模型：由服务器配置决定，单次调用不可切换；参数是否受当前模型支持先查 `get_model_info`，不支持时报错会附支持的模型清单，转告用户调整服务器配置
- `size`：档位（`1K`/`1.5K`/`2K`/`3K`/`4K`）或 `宽x高` 像素；省略时使用服务器默认尺寸（未另行配置时为 `2K`）；图层拆分工具仅接受 `1K/1.5K/2K` 档位或 `auto`，省略时默认 `auto`
- `watermark`：默认不加水印
- `optimize_prompt_options`：`standard` 或 `fast`，`fast` 需当前模型支持
- `output_format`：在 `jpeg`/`png` 中选择，需当前模型支持；`response_format`：默认 `url`
- `background`（文生图、图生图、多图融合、组图生成）：`transparent`/`opaque`/`auto` 三值，需当前模型支持，`transparent` 与 `output_format=jpeg` 互斥
- `stream`：需当前模型支持；开启时 `request_count` 须为 1
- `tools`：`[{"type": "web_search"}]` 开启联网搜索，需当前模型支持
- `request_count`：1-10 张候选图；组图场景语义为"每次产出一组"；图层拆分场景每次产出一次拆分结果
- `sequential_image_generation_options.max_images`（组图）：1-15，省略时自动取 15 减去参考图数量
- 图层拆分用独立的 `layer_decompose` 工具：输出 1 张底图 + 多张透明 PNG 图层（Seedream 5.5 至多 99 张、5.0 Pro 与 5.0 Flash 至多 16 张），`prompt` 可缺省由模型自动拆分，需当前模型支持
- `layer_decomposition_options`（图层拆分，仅 Seedream 5.5）：`max_images`（含底图 2-100，默认 30）与 `text_recognition`（对规整文字图层返回富文本 OCR 结果）
- `auto_save`/`save_path`/`custom_name`：控制单次保存行为，见下节

## 图片的保存与复用

- 自动保存默认开启：生成结果自动下载到图片目录（`<数据根目录>/.seedream/images`，数据根目录取 `SEEDREAM_DATA_ROOT` 配置值或工作根目录，工作根目录取 MCP Roots 首项、`SEEDREAM_WORKSPACE_ROOT`、进程启动目录或用户主目录）的 `<日期>/<工具名>/` 子目录，文件名含 prompt 词干便于检索
- API 返回的图片 URL 仅保留 24 小时，过期即失效；引用历史图片一律使用本地保存路径
- 保存路径可直接作为 `image` 参数回流：先 `browse_images` 定位历史图，再以该路径作参考图生成新图
- 保存目录默认 30 天自动清理、总量 10GB 上限，重要图片请让用户另行归档

## 深入阅读

以下参考文件按需读取，不必预先加载：

- 多步工作流（连环画/故事书端到端、图层拆分与再合成、风格一致性迭代）：[references/workflows.md](references/workflows.md)，经 MCP 资源读取时 URI 为 `skill://seedream-image-generation/references/workflows.md`
- 故障排查（错误码对策、常见失败模式、输入与配额约束）：[references/troubleshooting.md](references/troubleshooting.md)，经 MCP 资源读取时 URI 为 `skill://seedream-image-generation/references/troubleshooting.md`
