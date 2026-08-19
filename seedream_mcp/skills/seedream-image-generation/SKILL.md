---
name: seedream-image-generation
description: Seedream 图像生成 MCP 服务器的使用指南，覆盖文生图、图生图、多图融合、组图生成与图层拆分。当用户要求生成图片、画图、改图、换风格、融合多张图、制作连环画或故事书、拆分图层、生成透明背景，或需要调用 text_to_image、image_to_image、multi_image_fusion、sequential_generation、browse_images 工具，选择模型与尺寸档位，排查 401/402/403/413/429 报错，以及找回已保存的图片时使用本技能。Use when generating or editing images via the Seedream MCP server.
---

# Seedream 图像生成指南

## 何时使用本技能

- 用户要求生成图片、画图、作画（文生图）
- 用户要求修改图片、换风格、换背景、去水印元素（图生图）
- 用户要求把多张图片融合、合成、拼贴（多图融合）
- 用户要求制作连环画、故事书、分镜组图（组图生成）
- 用户要求拆分图层、生成透明背景素材（仅 5.0 Pro）
- 用户询问选哪个模型、尺寸档位，或要找回之前生成的图片

## 环境与前置

- 本 skill 假定客户端已连接 Seedream MCP 服务器并完成鉴权配置
- 服务器默认模型 `doubao-seedream-5.0`，未显式指定 model 参数时使用默认值
- 不确定当前生效配置时，先读资源 `seedream://server/info` 确认模型、默认尺寸与自动保存开关

## 工具速查

| 工具                    | 用途                   | 必需参数                               | 关键限制                     |
| ----------------------- | ---------------------- | -------------------------------------- | ---------------------------- |
| `text_to_image`         | 文生图                 | `prompt`                               | 无                           |
| `image_to_image`        | 图生图、编辑、图层拆分 | `image`；`prompt` 仅图层拆分场景可缺省 | 图层拆分与透明背景仅 5.0 Pro |
| `multi_image_fusion`    | 多张参考图融合         | `image`（2 张起）、`prompt`            | 用"图1/图2"引用各输入图      |
| `sequential_generation` | 一次生成一组连贯组图   | `prompt`                               | 5.0 Pro 不支持组图           |
| `browse_images`         | 浏览已保存图片         | 无（全部可选）                         | 只读，不访问网络             |

## 模型选择

按结构性差异取舍，不要背参数值：

- `doubao-seedream-5.0`（默认）：能力面最全，组图、联网搜索、流式均支持
- `doubao-seedream-5.0-pro`：独占图层拆分与透明背景，支持 fast 档提示词优化；但没有组图、联网搜索、流式，可参考图数量更少、尺寸档位更少
- `doubao-seedream-4.5` / `doubao-seedream-4.0`：输出仅 jpeg，不支持提示词优化

完整能力数据以读取 `seedream://models/info` 资源为准，不要凭记忆复述像素区间、档位清单等数值。

## 提示词写法

- 用连贯自然语言写明主体 + 行为 + 环境，再以短语补充风格、色彩、光影、构图等美学元素
- 建议不超过 300 个汉字或 600 个英文单词；超出后信息分散，模型可能忽略细节
- 组图与多图融合场景给每张图独立的画面描述，保持叙事顺序
- 服务器内置四个风格预设 prompt：`seedream_style_anime`、`seedream_style_realistic`、`seedream_style_watercolor`、`seedream_style_oil_painting`，可作为风格后缀参考

## 关键参数规则

- `model`：省略时用服务器默认模型；图层拆分必须显式指定 5.0 Pro
- `size`：档位（`1K`/`1.5K`/`2K`/`3K`/`4K`）或 `宽x高` 像素；省略时默认 `2K`；图层拆分场景仅接受档位或 `auto`
- `watermark`：默认不加水印
- `optimize_prompt_options`：`standard` 或 `fast`；`fast` 仅 5.0 Pro 与 4.0 支持
- `response_format`：默认 `url`；`output_format` 仅 5.0 系列支持 jpeg/png 选择
- `stream`：5.0 Pro 不支持
- `tools`：`[{"type": "web_search"}]` 开启联网搜索，5.0 Pro 不支持
- `request_count`：1-10 张候选图；组图场景语义为"每次产出一组"
- `max_images`（组图）：1-15，省略时自动取 15 减去参考图数量
- `layer_decomposition`（图层拆分）：输出 1 张底图 + 至多 16 张透明 PNG 图层，仅 5.0 Pro 图生图
- `auto_save`/`save_path`/`custom_name`：控制单次保存行为，见下节

## 图片的保存与复用

- 自动保存默认开启：生成结果自动下载到 `<工作区根>/.seedream/images/<日期>/<工具名>/`，文件名含 prompt 词干便于检索
- API 返回的图片 URL 仅保留 24 小时，过期即失效；引用历史图片一律使用本地保存路径
- 保存路径可直接作为 `image` 参数回流：先 `browse_images` 定位历史图，再以该路径作参考图生成新图
- 保存目录默认 30 天自动清理、总量 10GB 上限，重要图片请让用户另行归档

## 深入阅读

以下参考文件按需读取，不必预先加载：

- 多步工作流（连环画/故事书端到端、图层拆分与再合成、风格一致性迭代）：[references/workflows.md](references/workflows.md)，经 MCP 资源读取时 URI 为 `skill://seedream-image-generation/references/workflows.md`
- 故障排查（错误码对策、常见失败模式、输入与配额约束）：[references/troubleshooting.md](references/troubleshooting.md)，经 MCP 资源读取时 URI 为 `skill://seedream-image-generation/references/troubleshooting.md`
