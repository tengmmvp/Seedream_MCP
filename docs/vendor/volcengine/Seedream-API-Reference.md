`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`

本文介绍图片生成模型的调用 API，包括输入输出参数、取值范围、注意事项等信息，供您使用接口时查阅字段含义。

**模型能力**

- **Seedream 5.5new**
  - 图层拆分：支持将单张图片拆解为底图（1 张）+ 多个图层输出（最多 99 个图层）；支持控制最多输出的图片总数。

  - 富文本 OCR：图层拆分时，支持识别并返回规整文字的文本内容和样式信息（字体、颜色、粗体、斜体、下划线、删除线、阴影和半透明）。

  - 原生 RGBA 图：支持通过文生图或图生图，直接生成带透明通道的 RGBA 图片。

  - 生成单图（不支持配置 `sequential_image_generation`）
    - 多图生图：输入多张参考图片（2\-10）+ 文本提示词，生成单张图片。

    - 单图生图：输入单张参考图片 + 文本提示词，生成单张图片。

    - 文生图：输入文本提示词，生成单张图片。

  - 暂不支持组图生成、联网搜索、流式输出。

- **Seedream 5.0 pronew**、**Seedream 5.0 flashnew**
  - 图层拆分：支持将单张图片拆解为底图（1 张）+ 多个图层输出（最多 16 个图层）。

  - 交互编辑：支持通过坐标、框选、箭头等多种方式指定编辑位置，精准编辑图片。

  - 生成单图（不支持配置 `sequential_image_generation`）
    - 多图生图：输入多张参考图片（2\-10）+ 文本提示词，生成单张图片。

    - 单图生图：输入单张参考图片 + 文本提示词，生成单张图片。

    - 文生图：输入文本提示词，生成单张图片。

  - 暂不支持组图生成、联网搜索、流式输出。

- **Seedream 5.0 lite**、**Seedream 4.5 / 4.0**
  - 生成组图（组图：基于您输入的内容，生成的一组内容关联的图片；需配置 `sequential_image_generation` 为 `auto`）
    - 多图生组图：输入多张参考图片（2\-14）+ 文本提示词，生成一组内容关联的图片（输入的参考图数量 + 最终生成的图片数量 ≤ 15 张）。

    - 单图生组图：输入单张参考图片 + 文本提示词，生成一组内容关联的图片（最多生成 14 张图片）。

    - 文生组图：输入文本提示词，生成一组内容关联的图片（最多生成 15 张图片）。

  - 生成单图（配置 `sequential_image_generation` 为 `disabled`）
    - 多图生图：输入多张参考图片（2\-14）+ 文本提示词，生成单张图片。

    - 单图生图：输入单张参考图片 + 文本提示词，生成单张图片。

    - 文生图：输入文本提示词，生成单张图片。

## 鉴权

本接口支持鉴权方式如下，详情请参见 [Base URL 及鉴权](https://ark.volcengine.com/region:cn-beijing/docs/base-url-and-authentication)。

- API Key 鉴权，请在 [API Key 管理](https://ark.volcengine.com/region:cn-beijing/apiKey) 页面，获取长效 API Key。

---

## 请求参数

### Body 参数

**model** `string` `必选` | 模型 ID

您需要调用的模型的 ID（Model ID），[开通模型服务](https://ark.volcengine.com/region:cn-beijing/openManagement?LLM=%7B%7D&OpenTokenDrawer=false)，并 [查询 Model ID](https://ark.volcengine.com/region:cn-beijing/docs/model-list#9df4d9fd)。

您也可通过 Endpoint ID 来调用模型，获得限流、计费类型（前付费 / 后付费）、运行状态查询、监控、安全等高级能力，可参考 [获取 Endpoint ID](https://ark.volcengine.com/region:cn-beijing/docs/online-inference-standard)。

**prompt** `string` | 提示词

用于生成图像或指定图层拆分意图的提示词。

> **说明**

- > **提示词语言支持**：所有模型均支持中英文提示词；
  - > `Seedream 5.5` / `Seedream 5.0 pro` / `Seedream 5.0 flash` 额外支持俄语、阿拉伯语、菲律宾语、泰语、土耳其语、韩语、马来语、西班牙语、葡萄牙语、印尼语、法语、德语、越南语、日语。

- > **提示词字数建议**：中文提示词不超过 300 字，英文提示词不超过 600 词。字数过多信息容易分散，模型可能因此忽略细节，只关注重点，造成图片缺失部分元素。

**图片生成场景 ** **`必选`**

用于描述生成图像的内容，模型根据提示词生成对应图像。（查看提示词指南：[Seedream 4.0-5.0 提示词指南](https://ark.volcengine.com/region:cn-beijing/docs/seedream-4-0-5-0-prompt-guide)）

**图层拆分场景 ** **`可选`**

用于指定图层拆分意图，模型根据提示词意图识别并拆分指定元素。

若不传入提示词，模型将自动识别图片中的所有主要元素并拆分为独立图层。

**image** `string / string[]` | 参考图片

输入的图片信息，支持 URL 或 Base64 编码。

- 图片 URL：请确保图片 URL 可被访问。

- Base64 编码：请遵循此格式 `data:image/<图片格式>;base64,<Base64 编码>`。注意 `<图片格式>` 需小写，如 `data:image/png;base64,<base64_image>`。

**图片生成场景 ** **`可选`**

Seedream 5.5 / 5.0 pro / 5.0 flash 最多支持传入 10 张参考图；Seedream 5.0 lite / 4.5 / 4.0 最多支持传入 14 张参考图。

**单张图片传入要求**：

- 图片格式：jpeg、png、webp、bmp、tiff、gif、heic、heif

- 宽高比（宽 / 高）范围：[1/16, 16]

- 宽高长度（px）\> 14

- 大小：不超过 30MB

- 总像素：[196, `6000×6000`（36M）]（对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制）

**图层拆分场景 ** **`必选`**

Seedream 5.5 / 5.0 pro / 5.0 flash 在开启图层拆分模式时（`layer_decomposition` 为 `true`），`image` 为必选参数，且仅支持输入单张图片（传入多张报错）。

**单张图片传入要求**：

- 图片格式：png、jpeg

- 宽高比：[1/16, 16]

- 大小：不超过 30MB

- 总像素（宽×高）范围：[`512×512`（262144）, `6000×6000`（36M）]（对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制）

**layer_decompositionnew** `boolean` `默认值 false` | 图层拆分开关

控制是否开启图层拆分功能。

图层拆分会将单张图片的主体、背景、文字等内容自动拆解为 1 张底图和多个可独立编辑的图层。每个图层为带透明通道的 PNG 图片。

- `true`：图层拆分模式。模型将输入图拆解为一张底图和多个图层。

- `false`：图片生成模式，不进行图层拆分。

> **图层拆分模式使用说明**

- > 仅支持输入单张待拆分图，传入多张报错。

- > 任一图层生成失败，整体请求报错，不支持部分成功。

- > 若提示词要求的拆分数量超过上限，可能导致部分图层信息丢失。

- > 响应结构体 `data` 中，将返回每个产出图层的位置和内容信息，包括图层顺序（`z_index`）、边界框信息（`bounding_box`）、名称（`name`）和描述（`description`）。

**模型支持**：

- **Seedream 5.5**：最多输出 1 张底图和 99 个图层；支持设置最大输出图片数（含底图）和识别规整文字的富文本 OCR。

- **Seedream 5.0 pro**：最多输出 1 张底图和 16 个图层

- **Seedream 5.0 flash**：最多输出 1 张底图和 16 个图层

**layer_decomposition_optionsnew** `object` | 图层拆分配置

图层拆分功能的配置。仅当 `layer_decomposition` 为 `true` 时生效。

**模型支持**：

- `Seedream 5.5`

**max_imagesnew** `integer` `默认值 30` | 最大输出图片数

`layer_decomposition_options.max_images`

设置本次请求最多生成的图片总数，包含底图和图层。

**取值范围**：`[2, 100]`

> **说明**

- > 每次请求将按 `max_images` 值预扣减 IPM ，若实际生成图片数量不足则释放多扣额度。

- > 当提示词未说明如何拆分或描述不清晰时，模型会结合 `max_images` 和输入图片的复杂度合理规划拆分结果；传入的 `max_images` 值越大，拆分结果通常越细。

**text_recognitionnew** `boolean` `默认值 false` | 富文本 OCR 返回开关

`layer_decomposition_options.text_recognition`

控制是否对规整文字图层额外返回富文本 OCR 结果。支持识别文本内容和文本样式（加粗、斜体、下划线、删除线、阴影和半透明）。

- `true`：通过 `data[].text_recognition` 返回富文本 OCR 结果。

- `false`：不返回富文本 OCR 结果。

> **注意**

- > 支持识别的字体范围，详见 [Seedream 5.5 OCR 支持字体](https://ark.volcengine.com/region:cn-beijing/docs/ocr-supported-fonts)。

- > 非文字图层，以及艺术字、手写体等非规整文字图层，不返回 OCR 结果。

**size** `string` | 图像尺寸

指定生成图像的尺寸信息。对于不同模型和不同场景，参数的配置方式、可选分辨率、默认值、总像素取值范围、宽高比取值范围不同，请展开以下各模型对应说明查看。

**Seedream 5.5（图片生成场景）**

支持以下两种方式，不可混用：

- 方式 1（推荐）：指定分辨率档位，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 默认值：`2K`

  - 可选值：`1K`、`1.5K`、`2K`

- 方式 2：指定宽高像素值（`宽x高`）。
  - 总像素取值范围：[`1280x720`（921600）, `2048x2048x1.1025`（4624220）]

  - 宽高比取值范围：[1/16, 16]

> **说明**

> 采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。

- > **有效示例**：`2880x1440`。总像素值 2880x1440=4147200，符合 [921600, 4624220] 的区间要求；宽高比 2880/1440=2，符合 [1/16, 16] 的区间要求，故该示例值有效。

- > **无效示例**：`512x512`。总像素值 512x512=262144，未达到 921600 的最低要求，故该示例值无效。

使用方式 1 时，模型实际映射的宽高像素参考值（不限于以下标准值，仅列常见）：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 1K     | 1:1    | 1024x1024  |
|        | 4:3    | 1152x864   |
|        | 3:4    | 864x1152   |
|        | 16:9   | 1424x800   |
|        | 9:16   | 800x1424   |
|        | 3:2    | 1248x832   |
|        | 2:3    | 832x1248   |
|        | 21:9   | 1568x672   |
| 1.5K   | 1:1    | 1536x1536  |
|        | 4:3    | 1792x1344  |
|        | 3:4    | 1344x1792  |
|        | 16:9   | 2048x1152  |
|        | 9:16   | 1152x2048  |
|        | 3:2    | 1872x1248  |
|        | 2:3    | 1248x1872  |
|        | 21:9   | 2352x1008  |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2368x1776  |
|        | 3:4    | 1776x2368  |
|        | 16:9   | 2816x1584  |
|        | 9:16   | 1584x2816  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |

**Seedream 5.5（图层拆分场景）**

仅支持指定分辨率档位。输出图的分辨率规则如下：

- **底图**：输出底图的分辨率和 `size` 指定的分辨率一致；输出底图和原待拆分图的宽高比一致。

- **各图层**：输出图层的分辨率和 `size` 指定的分辨率接近；每个输出图层和其在原图中的宽高比一致。

`size` 的默认值与可选值：

- 默认值：`auto`

- 可选值：`1K`、`1.5K`、`2K`、`auto`（根据输入图的尺寸和宽高比进行输出）

> **auto 适配规则**

> auto 模式下，模型将根据输入图片中底图和每个图层的原始尺寸进行输出：

- > 若原始尺寸在 [`1280x720`（921600）, `2048x2048x1.1025`（4624220）] 之间，按照原尺寸输出，并保持其在原图中的宽高比。

- > 若原始尺寸小于 1K，按 1K 输出，并保持其在原图中的宽高比。

- > 若原始尺寸大于 2K，按 2K 输出，并保持其在原图中的宽高比。

模型实际映射的宽高像素参考值（不限于以下标准值，仅列常见）：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 1K     | 1:1    | 1024x1024  |
|        | 4:3    | 1152x864   |
|        | 3:4    | 864x1152   |
|        | 16:9   | 1424x800   |
|        | 9:16   | 800x1424   |
|        | 3:2    | 1248x832   |
|        | 2:3    | 832x1248   |
|        | 21:9   | 1568x672   |
| 1.5K   | 1:1    | 1536x1536  |
|        | 4:3    | 1792x1344  |
|        | 3:4    | 1344x1792  |
|        | 16:9   | 2048x1152  |
|        | 9:16   | 1152x2048  |
|        | 3:2    | 1872x1248  |
|        | 2:3    | 1248x1872  |
|        | 21:9   | 2352x1008  |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2368x1776  |
|        | 3:4    | 1776x2368  |
|        | 16:9   | 2816x1584  |
|        | 9:16   | 1584x2816  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |

**Seedream 5.0 pro / Seedream 5.0 flash（图片生成场景）**

支持以下两种方式，不可混用：

- 方式 1（推荐）：指定分辨率档位，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 默认值：`2K`

  - 可选值：`1K`、`1.5K`、`2K`

- 方式 2：指定宽高像素值（`宽x高`）。
  - 总像素取值范围：[`1280x720`（921600）, `2048x2048x1.1025`（4624220）]

  - 宽高比取值范围：[1/16, 16]

> **说明**

> 采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。

- > **有效示例**：`2048x1024`。总像素值 2048x1024=2097152，符合 [921600, 4624220] 的区间要求；宽高比 2048/1024=2，符合 [1/16, 16] 的区间要求，故该示例值有效。

- > **无效示例**：`512x512`。总像素值 512x512=262144，未达到 921600 的最低要求，故该示例值无效。

使用方式 1 时，模型实际映射的宽高像素参考值（不限于以下标准值，仅列常见）：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 1K     | 1:1    | 1024x1024  |
|        | 4:3    | 1152x864   |
|        | 3:4    | 864x1152   |
|        | 16:9   | 1424x800   |
|        | 9:16   | 800x1424   |
|        | 3:2    | 1248x832   |
|        | 2:3    | 832x1248   |
|        | 21:9   | 1568x672   |
| 1.5K   | 1:1    | 1536x1536  |
|        | 4:3    | 1792x1344  |
|        | 3:4    | 1344x1792  |
|        | 16:9   | 2048x1152  |
|        | 9:16   | 1152x2048  |
|        | 3:2    | 1872x1248  |
|        | 2:3    | 1248x1872  |
|        | 21:9   | 2352x1008  |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2368x1776  |
|        | 3:4    | 1776x2368  |
|        | 16:9   | 2816x1584  |
|        | 9:16   | 1584x2816  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |

**Seedream 5.0 pro / Seedream 5.0 flash（图层拆分场景）**

仅支持通过指定分辨率档位的方式设置。输出图的分辨率规则如下：

- **底图**：输出底图的分辨率和 `size` 指定的分辨率一致；输出底图和原待拆分图的宽高比一致。

- **各图层**：输出图层的分辨率和 `size` 指定的分辨率接近；每个输出图层和其在原图中的宽高比一致。

`size` 的默认值与可选值：

- 默认值：`auto`

- 可选值：`1K`、`1.5K`、`2K`、`auto`（根据输入图的尺寸和宽高比进行输出）

> **auto 适配规则**

> auto 模式下，模型将根据输入图片中底图和每个图层的原始尺寸进行输出：

- > 若输入图片中底图和每个图层的原始尺寸在 [`1280x720`（921600）, `2048x2048x1.1025`（4624220）] 之间，按照原尺寸输出底图与每个图层；且各自保持其在原图中的宽高比。

- > 若输入图片中底图和每个图层的原始尺寸小于 1K，按 1K 输出底图与每个图层；且各自保持其在原图中的宽高比。

- > 若输入图片中底图和每个图层的原始尺寸大于 2K，按 2K 输出底图与每个图层；且各自保持其在原图中的宽高比。

模型实际映射的宽高像素参考值（不限于以下标准值，仅列常见）：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 1K     | 1:1    | 1024x1024  |
|        | 4:3    | 1152x864   |
|        | 3:4    | 864x1152   |
|        | 16:9   | 1424x800   |
|        | 9:16   | 800x1424   |
|        | 3:2    | 1248x832   |
|        | 2:3    | 832x1248   |
|        | 21:9   | 1568x672   |
| 1.5K   | 1:1    | 1536x1536  |
|        | 4:3    | 1792x1344  |
|        | 3:4    | 1344x1792  |
|        | 16:9   | 2048x1152  |
|        | 9:16   | 1152x2048  |
|        | 3:2    | 1872x1248  |
|        | 2:3    | 1248x1872  |
|        | 21:9   | 2352x1008  |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2368x1776  |
|        | 3:4    | 1776x2368  |
|        | 16:9   | 2816x1584  |
|        | 9:16   | 1584x2816  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |

**Seedream 5.0 lite**

支持以下两种方式，不可混用：

- 方式 1：指定分辨率，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 可选值：`2K`、`3K`、`4K`

- 方式 2：指定生成图像的宽高像素值。
  - 默认值：`2048x2048`

  - 总像素取值范围：[`2560x1440`（3686400）, `4096x4096`（16777216）]

  - 宽高比取值范围：[1/16, 16]

> **说明**

> 采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。

- > **有效示例**：`3750x1250`。总像素值 3750x1250=4687500，符合 [3686400, 16777216] 的区间要求；宽高比 3750/1250=3，符合 [1/16, 16] 的区间要求，故该示例值有效。

- > **无效示例**：`1500x1500`。总像素值 1500x1500=2250000，未达到 3686400 的最低要求；宽高 1500/1500=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。

采用方式 1 时，模型实际映射的宽高像素参考值：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2304x1728  |
|        | 3:4    | 1728x2304  |
|        | 16:9   | 2848x1600  |
|        | 9:16   | 1600x2848  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |
| 3K     | 1:1    | 3072x3072  |
|        | 4:3    | 3456x2592  |
|        | 3:4    | 2592x3456  |
|        | 16:9   | 4096x2304  |
|        | 9:16   | 2304x4096  |
|        | 3:2    | 3744x2496  |
|        | 2:3    | 2496x3744  |
|        | 21:9   | 4704x2016  |
| 4K     | 1:1    | 4096x4096  |
|        | 4:3    | 4704x3520  |
|        | 3:4    | 3520x4704  |
|        | 16:9   | 5504x3040  |
|        | 9:16   | 3040x5504  |
|        | 3:2    | 4992x3328  |
|        | 2:3    | 3328x4992  |
|        | 21:9   | 6240x2656  |

**Seedream 4.5**

支持以下两种方式，不可混用：

- 方式 1：指定分辨率，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 可选值：`2K`、`4K`

- 方式 2：指定生成图像的宽高像素值。
  - 默认值：`2048x2048`

  - 总像素取值范围：[`2560x1440`（3686400）, `4096x4096`（16777216）]

  - 宽高比取值范围：[1/16, 16]

> **说明**

> 采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。

- > **有效示例**：`3750x1250`。总像素值 3750x1250=4687500，符合 [3686400, 16777216] 的区间要求；宽高比 3750/1250=3，符合 [1/16, 16] 的区间要求，故该示例值有效。

- > **无效示例**：`1500x1500`。总像素值 1500x1500=2250000，未达到 3686400 的最低要求；宽高 1500/1500=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。

采用方式 1 时，模型实际映射的宽高像素参考值：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2304x1728  |
|        | 3:4    | 1728x2304  |
|        | 16:9   | 2848x1600  |
|        | 9:16   | 1600x2848  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |
| 4K     | 1:1    | 4096x4096  |
|        | 4:3    | 4704x3520  |
|        | 3:4    | 3520x4704  |
|        | 16:9   | 5504x3040  |
|        | 9:16   | 3040x5504  |
|        | 3:2    | 4992x3328  |
|        | 2:3    | 3328x4992  |
|        | 21:9   | 6240x2656  |

**Seedream 4.0**

支持以下两种方式，不可混用：

- 方式 1：指定分辨率，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 可选值：`1K`、`2K`、`4K`

- 方式 2：指定生成图像的宽高像素值。
  - 默认值：`2048x2048`

  - 总像素取值范围：[`1280x720`（921600）, `4096x4096`（16777216）]

  - 宽高比取值范围：[1/16, 16]

> **说明**

> 采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。

- > **有效示例**：`1600x600`。总像素值 1600x600=960000，符合 [921600, 16777216] 的区间要求；宽高比 1600/600=8/3，符合 [1/16, 16] 的区间要求，故该示例值有效。

- > **无效示例**：`800x800`。总像素值 800x800=640000，未达到 921600 的最低要求；宽高 800/800=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。

采用方式 1 时，模型实际映射的宽高像素参考值：

| 分辨率 | 宽高比 | 宽高像素值 |
| ------ | ------ | ---------- |
| 1K     | 1:1    | 1024x1024  |
|        | 4:3    | 1152x864   |
|        | 3:4    | 864x1152   |
|        | 16:9   | 1280x720   |
|        | 9:16   | 720x1280   |
|        | 3:2    | 1248x832   |
|        | 2:3    | 832x1248   |
|        | 21:9   | 1512x648   |
| 2K     | 1:1    | 2048x2048  |
|        | 4:3    | 2304x1728  |
|        | 3:4    | 1728x2304  |
|        | 16:9   | 2848x1600  |
|        | 9:16   | 1600x2848  |
|        | 3:2    | 2496x1664  |
|        | 2:3    | 1664x2496  |
|        | 21:9   | 3136x1344  |
| 4K     | 1:1    | 4096x4096  |
|        | 4:3    | 4704x3520  |
|        | 3:4    | 3520x4704  |
|        | 16:9   | 5504x3040  |
|        | 9:16   | 3040x5504  |
|        | 3:2    | 4992x3328  |
|        | 2:3    | 3328x4992  |
|        | 21:9   | 6240x2656  |

**optimize_prompt_options** `object` | 提示词优化配置

提示词优化功能的配置。

**mode** `string` `默认值 standard` | 优化模式

`optimize_prompt_options.mode`

设置提示词优化功能使用的模式。

- `standard`：标准模式，生成内容的质量更高，耗时较长。

- `fast`：快速模式，生成内容的耗时更短，效果略低于标准模式。

**模型支持**：

- **Seedream 5.5**：默认值 `standard`；可选值 `standard`

- **Seedream 5.0 pro**：默认值 `standard`；可选值 `standard`、`fast`

- **Seedream 5.0 flash**：默认值 `standard`；可选值 `standard`

- **Seedream 5.0 lite**：默认值 `standard`；可选值 `standard`

- **Seedream 4.5**：默认值 `standard`；可选值 `standard`

- **Seedream 4.0**：默认值 `standard`；可选值 `standard`、`fast`

**output_format** `string` | 图像格式

指定生成图像的文件格式。可选值：`png`、`jpeg`。

> **注意**

- > 图层拆分场景下，`output_format` 仅控制底图的输出格式，图层始终以 `png` 格式输出。

- > 当 `output_format` 为 `jpeg`，且 `background` 为 `transparent` 时，请求报错。

**模型支持**：

- **Seedream 5.5**：默认值 `png`；可选值 `png`、`jpeg`

- **Seedream 5.0 pro**：默认值 `jpeg`；可选值 `png`、`jpeg`

- **Seedream 5.0 flash**：默认值 `jpeg`；可选值 `png`、`jpeg`

- **Seedream 5.0 lite**：默认值 `jpeg`；可选值 `png`、`jpeg`

**backgroundnew** `string` | 透明通道模式

控制生成图片是否带 Alpha 通道（透明度通道）。可选值：

- auto：模型根据提示词和 `output_format` 自动判断。若设置输出格式为 `jpeg` ，输出图片不带透明通道。

- `transparent`：输出带透明通道的图片（RGBA 图）。

- `opaque`：输出不带透明通道的图片（RGB 图）。

> **使用说明**

- > 图层拆分场景不支持传入该参数，传入将报错。

- > 使用 `transparent` 模式时，不同模型的要求如下：

> | 模型           | Seedream 5.5                     | Seedream 5.0 pro / 5.0 flash      |
> | -------------- | -------------------------------- | --------------------------------- |
> | 文生 RGBA 图片 | 支持                             | 不支持                            |
> | 图生 RGBA 图片 | 支持<br><br>最多输入 10 张参考图 | 支持<br><br>仅支持输入 1 张参考图 |
> | 输入图要求     | 无需带透明通道                   | 必须带透明通道                    |

**模型支持**：

- **Seedream 5.5**：默认值 `auto`；可选值 `auto`、`transparent`、`opaque`

- **Seedream 5.0 pro**：默认值 `opaque`；可选值 `transparent`、`opaque`

- **Seedream 5.0 flash**：默认值 `opaque`；可选值 `transparent`、`opaque`

**response_format** `string` `默认值 url` | 返回格式

指定生成图像的返回格式。支持以下两种返回方式：

- `url`：返回图片下载链接，**链接在图片生成后 24 小时内有效，请及时下载图片**。

- `b64_json`：以 Base64 编码字符串的 JSON 格式返回图像数据。

**sequential_image_generation** `string` `默认值 disabled` | 组图模式

控制是否关闭组图功能（组图：基于您输入的内容，生成的一组内容关联的图片）。

- `auto`：自动判断模式，模型会根据用户提供的提示词自主判断是否返回组图以及组图包含的图片数量。

- `disabled`：关闭组图功能，模型只会生成一张图。

组图输出示例详见 [Seedream 图像创作教程 - 组图输出](https://ark.volcengine.com/region:cn-beijing/docs/image-generation-tutorial#ec79cfda)。

**模型支持**：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**sequential_image_generation_options** `object` | 组图配置

组图功能的配置。仅当 `sequential_image_generation` 为 `auto` 时生效。

**模型支持**：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**max_images** `integer` `默认值 15` | 最大生成数量

`sequential_image_generation_options.max_images`

指定本次请求，最多可生成的图片数量。

**取值范围**：`[1, 15]`

> **说明**

- > 每次请求将按 `max_images` 值预扣减 IPM ，若实际生成图片数量不足则释放多扣额度。

- > 实际可生成的图片数量，除受到 `max_images` 影响外，还受到输入的参考图数量影响。**输入的参考图数量+最终生成的图片数量≤15 张**。

**stream** `boolean` `默认值 false` | 流式输出开关

控制是否开启流式输出模式。

- `false`：非流式输出模式，等待所有图片全部生成结束后再一次性返回所有信息。

- `true`：流式输出模式，即时返回每张图片输出的结果。在生成单图和组图的场景下，流式输出模式均生效。

流式输出示例详见 [Seedream 图像创作教程 - 流式输出](https://ark.volcengine.com/region:cn-beijing/docs/image-generation-tutorial#e5bef0d7)。

**模型支持**：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**tools** `object[]` | 工具配置

配置模型要调用的工具。

**模型支持**：

- `Seedream 5.0 lite`

**type** `string` `必选` | 工具类型

`tools.type`

指定使用的工具类型。

- `web_search`：联网搜索功能。

> **说明**

- > 开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等），提升生成图片的时效性，但也会增加一定的时延。

- > 实际搜索次数可通过字段 `usage.tool_usage.web_search` 查询，如果为 0 表示未搜索。

**watermark** `boolean` `默认值 true` | 水印开关

是否在生成的图片中添加水印。

- `false`：不添加水印。

- `true`：在图片右下角添加“AI 生成”字样的水印标识。

**safety_identifiernew** `string` | 终端用户标识

终端用户的唯一标识符，用于协助平台检测您的应用中可能违反火山方舟使用政策的用户。该标识符为英文字符串，需保证对单个用户固定且唯一，长度不超过 64 个字符。

推荐传入对用户名、用户 ID 或邮箱进行哈希处理后生成的字符串，避免泄露用户隐私信息。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

## 响应参数

### 非流式调用响应

**created** `integer` | 创建时间

本次请求创建时间的 Unix 时间戳（秒）

**model** `string` | 模型 ID

本次请求使用的模型 ID（模型名称\-版本）

**data** `object[]` | 图像数据

输出图像的信息。不同任务场景返回字段有所区别。

图像返回格式（`url` 或 `b64_json`）、图像尺寸（`size`）、输出格式（`output_format`）、背景类型（`background`）为公共返回字段。

- **组图生成场景**：若某张图生成失败，返回该图片的错误信息（`error`）。

- **图层拆分场景**：返回每个图层的叠放顺序（`z_index`）、名称（`name`）、描述（`description`）、边界框坐标（`bounding_box`）。Seedream 5.5 可额外返回规整文字图层的富文本 OCR 结果（`text_recognition`）。

**公共字段**

**url** `string` | 图片 URL

`data.url`

图片 URL，当 `response_format` 指定为 `url` 时返回。该链接将在生成后 **24 小时内失效**，请务必及时保存图像。

推荐配置火山引擎 TOS 提供的数据订阅功能，将您的模型推理产物自动转存到自己的 TOS 桶中，便于长期备份或二次加工。详细介绍请参见 [TOS 数据订阅](https://docs.volcengine.com/docs/torch-object-storage/data-subscription?lang=zh)。

**b64_json** `string` | 图片 Base64 数据

`data.b64_json`

图片的 Base64 信息，当 `response_format` 指定为 `b64_json` 时返回

**size** `string` | 图像尺寸

`data.size`

图像的宽高像素值，格式 `<宽像素>x<高像素>`，例如 `2048x2048`。

**output_formatnew** `string` | 输出格式

`data.output_format`

输出图像的格式信息（`png` 或 `jpeg`）。

> **说明**

- > 图层拆分场景下，`output_format` 仅控制底图的输出格式，图层始终以 `png` 格式输出。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**backgroundnew** `string` | 实际透明度类型

`data.background`

生成图片的实际透明度类型，可能与请求中的 `background` 取值不同。

- `transparent`：图片存在透明或半透明像素。

- `opaque`：图片整体不透明。

**模型支持**：

- `Seedream 5.5`

**特殊字段（图层拆分场景）**

**z_indexnew** `integer` | 图层叠放顺序

`data.z_index`

图层的叠放顺序索引。底图固定为 `0`，图层从 `1` 开始递增，数值越大越靠上层。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**namenew** `string` | 图层名称

`data.name`

当前图层拆分元素的名称/标签。由模型根据拆分出的主体特征自动生成，用于标识该图层的主体内容。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**descriptionnew** `string` | 图层描述

`data.description`

当前图层拆分元素的详细描述。由模型生成的语义描述，提供比 `name` 更丰富的图层特征（如颜色、状态、材质等）。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**bounding_boxnew** `object` | 图层边界框

`data.bounding_box`

输出图层在输出底图坐标系中所占区域的边界框信息。仅图层返回该字段；底图覆盖整张画布，不返回该字段。

> **使用说明**

- > `bounding_box` 表示图层在待拆分图中的相对位置与宽高比，等比缩放至输出底图坐标系后的坐标。
  - > 若需将图层还原到输出底图中，建议优先使用 `absolute` 坐标。

  - > 若需将图层还原到任意自定义的画布中，可使用 `normalized` 坐标。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**absolute** `array` | 绝对坐标

`data.bounding_box.absolute`

输出图层边界框在输出底图坐标系中的绝对像素坐标（单位：像素），输出底图左上角坐标为 `(0, 0)`。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`：图层左边界到输出底图左边界的水平距离。

- `top`：图层上边界到输出底图上边界的垂直距离。

- `right`：图层右边界到输出底图左边界的水平距离。

- `bottom`：图层下边界到输出底图上边界的垂直距离。

> **坐标示例**

> 例如 `[225, 442, 796, 1414]` 表示图层左上角坐标为 `(225, 442)`，右下角坐标为 `(796, 1414)`。

> **使用 bounding_box.absolute 还原图层**

> 使用 `bounding_box.absolute` 可将输出图层还原到其在输出底图坐标系中的边界框区域。计算方式如下：

- > 图层在输出底图中的左上角坐标：`x = left`，`y = top`。

- > 图层在输出底图中的占用宽度：`w = right - left`。

- > 图层在输出底图中的占用高度：`h = bottom - top`。

> 将输出图层缩放至 `w × h`，再放置到输出底图的 `(x, y)` 位置。多个图层需要组合时，按 `z_index` 从小到大依次叠放。

**normalized** `array` | 归一化坐标

`data.bounding_box.normalized`

图层边界框在输出底图坐标系中的归一化坐标，由绝对坐标按输出底图宽高换算至 `[0, 1000]` 的离散整数区间得到。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`、`right`：图层左右边界在底图宽度方向上的相对位置。`0` 表示底图最左侧，`1000` 表示底图最右侧。

- `top`、`bottom`：图层上下边界在底图高度方向上的相对位置。`0` 表示底图最上方，`1000` 表示底图最下方。

> **坐标示例**

> 例如 `[220, 432, 777, 1000]` 表示：图层左边界距离底图左边界 22.0% 底图宽度，上边界距离底图上边界 43.2% 底图高度，右边界距离底图左边界 77.7% 底图宽度，下边界与底图下边缘对齐。

> **使用 bounding_box.normalized 还原图层**

> 使用 `bounding_box.normalized` 可将输出图层还原到目标画布中的边界框区域。设目标画布的宽高为 `W × H`：

- > 图层在目标画布中的左上角坐标：`x = left / 1000 × W`，`y = top / 1000 × H`。

- > 图层在目标画布中的占用宽度：`w = (right - left) / 1000 × W`。

- > 图层在目标画布中的占用高度：`h = (bottom - top) / 1000 × H`。

> 将输出图层缩放至 `w × h`，再放置到目标画布的 `(x, y)` 位置。归一化坐标为整数，换算后可能存在取整误差。

**text_recognitionnew** `object` | 富文本 OCR 结果

`data.text_recognition`

当前规整文字图层的富文本 OCR 结果，包含完整文本、逐行位置和各文字片段的视觉样式。

> **返回条件**

> 同时满足以下条件时返回：

- > `layer_decomposition` 为 `true`。

- > `layer_decomposition_options.text_recognition` 为 `true`。

- > 当前图层的文字被模型判定为**规整文字**。

> 非文字图层，以及艺术字、手写体等非规整文字图层，不返回该字段。

**模型支持**：

- `Seedream 5.5`

**text** `string` | 完整文本

`data.text_recognition.text`

当前文字图层的完整纯文本。按从上到下、从左到右的顺序排列，行间以换行符 `\n` 分隔。

**lines** `object[]` | 文字行

`data.text_recognition.lines`

按顺序排列的文字行数组。

> **说明**

- > 若当前文字图层包含多行文字，`lines[]` 中会包含多个元素，每个元素对应一行文字。

- > 每个元素包含该行在底图中的位置（`bounding_box`）和样式片段列表（`runs[]`）。

**bounding_box** `object` | 文字行边界框

`data.text_recognition.lines.bounding_box`

当前文字行在输出底图坐标系中的边界框信息，包含绝对坐标和归一化坐标。坐标格式及换算规则与 `data.bounding_box` 的对应字段一致。

**absolute** `array` | 绝对坐标

`data.text_recognition.lines.bounding_box.absolute`

文字行边界框在输出底图坐标系中的绝对像素坐标（单位：像素），输出底图左上角坐标为 `(0, 0)`。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`：文字行左边界到输出底图左边界的水平距离。

- `top`：文字行上边界到输出底图上边界的垂直距离。

- `right`：文字行右边界到输出底图左边界的水平距离。

- `bottom`：文字行下边界到输出底图上边界的垂直距离。

**normalized** `array` | 归一化坐标

`data.text_recognition.lines.bounding_box.normalized`

文字行边界框在输出底图坐标系中的归一化坐标，由绝对坐标按输出底图宽高换算至 `[0, 1000]` 的离散整数区间得到。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`、`right`：文字行左右边界在底图宽度方向上的相对位置。`0` 表示底图最左侧，`1000` 表示底图最右侧。

- `top`、`bottom`：文字行上下边界在底图高度方向上的相对位置。`0` 表示底图最上方，`1000` 表示底图最下方。

**runs** `object[]` | 样式片段

`data.text_recognition.lines.runs`

当前文字行的样式片段数组。

> **说明**

- > 整行文字样式一致时，`runs[]` 仅包含一个元素。

- > 同一行文字的字体、颜色、粗体等样式发生变化时，`runs[]` 中会包含多个元素。

**text** `string` | 片段文本

`data.text_recognition.lines.runs.text`

当前元素对应的文字片段，不包含换行符。

**style** `object` | 视觉样式

`data.text_recognition.lines.runs.style`

当前文字片段的近似视觉样式，包括相似字体、文字颜色、粗体、斜体、下划线、删除线、阴影和半透明。

> **说明**

> 样式信息由模型自动识别，可能与原始设计文件中的精确属性有偏差。

**similar_font_family** `string` | 相似字体

`data.text_recognition.lines.runs.style.similar_font_family`

模型根据文字外观匹配出的最相似字体名称。

> **说明**

- > 不能保证与原字体完全一致。

- > 无法识别或置信度不足时返回 `unknown`。

- > 支持识别的字体范围，详见 [Seedream 5.5 OCR 支持字体](https://ark.volcengine.com/region:cn-beijing/docs/ocr-supported-fonts)。

**foreground_color** `string` | 文字颜色

`data.text_recognition.lines.runs.style.foreground_color`

当前文字片段的颜色，使用大写十六进制 `#RRGGBB` 格式，不包含 Alpha（透明度）通道。

**bold** `boolean` | 粗体

`data.text_recognition.lines.runs.style.bold`

模型是否识别到当前文字片段使用粗体样式。

- `true`：模型识别到粗体样式。

- `false`：模型未识别到粗体样式。

**italic** `boolean` | 斜体

`data.text_recognition.lines.runs.style.italic`

模型是否识别到当前文字片段使用斜体样式。仅字形本身向右倾斜时返回 `true`；文字所在图层整体旋转或因透视产生倾斜，不视为斜体。

- `true`：模型识别到斜体样式。

- `false`：模型未识别到斜体样式。

**underline** `boolean` | 下划线

`data.text_recognition.lines.runs.style.underline`

模型是否识别到当前文字片段带有文字下划线。独立的分割线、表格线或装饰横线不视为文字下划线。

- `true`：模型识别到下划线样式。

- `false`：模型未识别到下划线样式。

**strikethrough** `boolean` | 删除线

`data.text_recognition.lines.runs.style.strikethrough`

模型是否识别到当前文字片段带有删除线。

- `true`：模型识别到删除线样式。

- `false`：模型未识别到删除线样式。

**shadow** `boolean` | 阴影

`data.text_recognition.lines.runs.style.shadow`

模型是否识别到当前文字片段带有明显的外投影、阴影或发光偏移。文字边缘的常规抗锯齿过渡不视为阴影。

- `true`：模型识别到阴影样式。

- `false`：模型未识别到阴影样式。

**translucent** `boolean` | 半透明

`data.text_recognition.lines.runs.style.translucent`

模型是否识别到文字墨迹本身明显半透明，并能透出下方背景纹理或底色。实心的浅灰色、浅蓝色等浅色文字不视为半透明。

- `true`：模型识别到半透明样式。

- `false`：模型未识别到半透明样式。

**特殊字段（组图生成场景）**

**error** `object` | 单图错误信息

`data.error`

某张图片生成失败时返回的错误信息。其他成功生成的图片不受影响

> **说明**

> 组图生成过程中某张图生成失败时：

- > 若失败原因为审核不通过：仍会继续请求下一个图片生成任务，即不影响同请求内其他图片的生成流程。

- > 若失败原因为内部服务异常（500）：不会继续请求下一个图片生成任务。

**模型支持**：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**code** `string` | 错误码

`data.error.code`

错误码，请参见 [错误码](https://ark.volcengine.com/region:cn-beijing/docs/error-codes)

**message** `string` | 错误消息

`data.error.message`

错误提示信息，便于排查问题

**error** `object` | 错误信息

本次请求顶层错误信息。当整个请求未能生成任何图片时返回

**code** `string` | 错误码

`error.code`

错误码，请参见 [错误码](https://ark.volcengine.com/region:cn-beijing/docs/error-codes)

**message** `string` | 错误消息

`error.message`

错误提示信息，便于排查问题

**safety_identifiernew** `string` | 终端用户标识

终端用户的唯一标识符。若请求中设置了 `safety_identifier`，接口将原样返回该字段。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**tools** `object[]` | 工具调用列表

本次请求中配置并被模型调用的工具列表

**模型支持**：

- `Seedream 5.0 lite`

**type** `string` | 工具类型

`tools.type`

指定使用的工具类型。

- `web_search`：联网搜索功能。

> **说明**

- > 开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等），提升生成图片的时效性，但也会增加一定的时延。

- > 实际搜索次数可通过字段 `usage.tool_usage.web_search` 查询，如果为 0 表示未搜索。

**usage** `object` | 用量信息

本次请求的用量信息，包括生成图片数量、消耗的 token 数量等

**generated_images** `integer` | 成功生成图片数

`usage.generated_images`

模型成功生成的图片张数，不包含生成失败的图片。仅对成功生成图片按张数进行计费

**input_imagesnew** `integer` | 输入图片数

`usage.input_images`

输入模型的图片张数。

**模型支持**：

- `Seedream 5.5`

- `Seedream 5.0 pro`

- `Seedream 5.0 flash`

**output_tokens** `integer` | 输出 token 数

`usage.output_tokens`

模型生成的图片花费的 token 数量。计算逻辑为：`sum(图片长 * 图片宽) / 256` 后取整

**tool_usage** `object` | 工具用量

`usage.tool_usage`

使用工具的用量信息

**模型支持**：

- `Seedream 5.0 lite`

**web_search** `integer` | 联网搜索次数

`usage.tool_usage.web_search`

调用联网搜索工具的次数，仅开启联网搜索时返回。如果为 0 表示未搜索

**total_tokens** `integer` | 总 token 数

`usage.total_tokens`

本次请求消耗的总 token 数量。当前不计算输入 token，故与 `output_tokens` 值一致

### 流式调用响应

字段结构详见 [图片生成流式响应事件](https://ark.volcengine.com/region:cn-beijing/docs/image-generation-streaming-responses)。

```mixin-react
return (<ApiSample data={"{\"config\":{\"inputName\":\"Request\",\"outputName\":\"Response\",\"envItems\":[{\"label\":\"ARK_API_KEY\",\"value\":\"ARK_API_KEY\"}]},\"type\":\"multiple\",\"data\":[{\"title\":\"图层拆分\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"prompt\\\": \\\"将图片进行精确图层分离，需分离的文字坐标为<bbox>180 64 812 198</bbox>、<bbox>757 210 939 280</bbox>、<bbox>63 212 320 282</bbox>、<bbox>178 714 826 810</bbox>、<bbox>814 819 949 894</bbox>、<bbox>326 824 669 930</bbox>；鹦鹉的坐标为<bbox>347 305 642 997</bbox>。\\\",\\n    \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_layer_input.png\\\",\\n    \\\"layer_decomposition\\\": true,\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\": \\\"jpeg\\\",\\n    \\\"response_format\\\": \\\"url\\\",\\n    \\\"watermark\\\": true\\n  }'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1784696685,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\",\\n            \\\"output_format\\\": \\\"jpeg\\\",\\n            \\\"z_index\\\": 0\\n        },\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1273x265\\\",\\n            \\\"output_format\\\": \\\"png\\\",\\n            \\\"z_index\\\": 1,\\n            \\\"bounding_box\\\": {\\n                \\\"absolute\\\": [383, 120, 1655, 384],\\n                \\\"normalized\\\": [187, 59, 808, 188]\\n            },\\n            \\\"name\\\": \\\"Seedream标题文字\\\",\\n            \\\"description\\\": \\\"黄色大号衬线字体的Seedream标题文字\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"input_images\\\": 1,\\n        \\\"generated_images\\\": 8,\\n        \\\"output_tokens\\\": 23107,\\n        \\\"total_tokens\\\": 23107\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"Ey9kPtZv\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n\\n# Install SDK: python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\n\\nclient = Ark(\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    api_key=os.getenv(\\\"ARK_API_KEY\\\"),\\n)\\n\\nresponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"将图片进行精确图层分离，需分离的文字坐标为<bbox>180 64 812 198</bbox>、<bbox>757 210 939 280</bbox>、<bbox>63 212 320 282</bbox>、<bbox>178 714 826 810</bbox>、<bbox>814 819 949 894</bbox>、<bbox>326 824 669 930</bbox>；鹦鹉的坐标为<bbox>347 305 642 997</bbox>。\\\",\\n    image=\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_layer_input.png\\\",\\n    layer_decomposition=True,\\n    size=\\\"2K\\\",\\n    output_format=\\\"jpeg\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True,\\n)\\n\\nfor item in response.data:\\n    print(item)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1784696685,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\",\\n            \\\"output_format\\\": \\\"jpeg\\\",\\n            \\\"z_index\\\": 0\\n        },\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1273x265\\\",\\n            \\\"output_format\\\": \\\"png\\\",\\n            \\\"z_index\\\": 1,\\n            \\\"bounding_box\\\": {\\n                \\\"absolute\\\": [383, 120, 1655, 384],\\n                \\\"normalized\\\": [187, 59, 808, 188]\\n            },\\n            \\\"name\\\": \\\"Seedream标题文字\\\",\\n            \\\"description\\\": \\\"黄色大号衬线字体的Seedream标题文字\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"input_images\\\": 1,\\n        \\\"generated_images\\\": 8,\\n        \\\"output_tokens\\\": 23107,\\n        \\\"total_tokens\\\": 23107\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"7Y5z7q4b\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                .dispatcher(new Dispatcher())\\n                .connectionPool(new ConnectionPool(5, 1, TimeUnit.SECONDS))\\n                .apiKey(System.getenv(\\\"ARK_API_KEY\\\"))\\n                .build();\\n\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest request = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-5-261028\\\")\\n                .prompt(\\\"将图片进行精确图层分离，需分离的文字坐标为<bbox>180 64 812 198</bbox>、<bbox>757 210 939 280</bbox>、<bbox>63 212 320 282</bbox>、<bbox>178 714 826 810</bbox>、<bbox>814 819 949 894</bbox>、<bbox>326 824 669 930</bbox>；鹦鹉的坐标为<bbox>347 305 642 997</bbox>。\\\")\\n                .image(CreateImageGenerationRequestImage.ofString(\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_layer_input.png\\\"))\\n                .layerDecomposition(true)\\n                .size(\\\"2K\\\")\\n                .outputFormat(OutputFormat.JPEG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .build();\\n\\n        try {\\n            ImageGenerationResponse response = service.generateImages(request);\\n            response.getData().forEach(System.out::println);\\n        } finally {\\n            service.shutdownExecutor();\\n        }\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1784696685,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\",\\n            \\\"output_format\\\": \\\"jpeg\\\",\\n            \\\"z_index\\\": 0\\n        },\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1273x265\\\",\\n            \\\"output_format\\\": \\\"png\\\",\\n            \\\"z_index\\\": 1,\\n            \\\"bounding_box\\\": {\\n                \\\"absolute\\\": [383, 120, 1655, 384],\\n                \\\"normalized\\\": [187, 59, 808, 188]\\n            },\\n            \\\"name\\\": \\\"Seedream标题文字\\\",\\n            \\\"description\\\": \\\"黄色大号衬线字体的Seedream标题文字\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"input_images\\\": 1,\\n        \\\"generated_images\\\": 8,\\n        \\\"output_tokens\\\": 23107,\\n        \\\"total_tokens\\\": 23107\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"inyvXGjY\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n\\t\\\"context\\\"\\n\\t\\\"fmt\\\"\\n\\t\\\"os\\\"\\n\\n\\t\\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n\\tmodel \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n\\tclient := arkruntime.NewClientWithApiKey(\\n\\t\\tos.Getenv(\\\"ARK_API_KEY\\\"),\\n\\t\\tarkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n\\t)\\n\\trequest := &model.CreateImageGenerationRequest{\\n\\t\\tModel:              \\\"doubao-seedream-5-5-261028\\\",\\n\\t\\tPrompt:             model.NewOptString(\\\"将图片进行精确图层分离，需分离的文字坐标为<bbox>180 64 812 198</bbox>、<bbox>757 210 939 280</bbox>、<bbox>63 212 320 282</bbox>、<bbox>178 714 826 810</bbox>、<bbox>814 819 949 894</bbox>、<bbox>326 824 669 930</bbox>；鹦鹉的坐标为<bbox>347 305 642 997</bbox>。\\\"),\\n\\t\\tImage:              model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_layer_input.png\\\"})),\\n\\t\\tLayerDecomposition: model.NewOptBool(true),\\n\\t\\tSize:               model.NewOptString(\\\"2K\\\"),\\n\\t\\tOutputFormat:       model.NewOptOutputFormat(model.OutputFormatJpeg),\\n\\t\\tResponseFormat:     model.NewOptResponseFormat(model.ResponseFormatURL),\\n\\t\\tWatermark:          model.NewOptBool(true),\\n\\t}\\n\\tresponse, err := client.GenerateImages(context.Background(), request)\\n\\tif err != nil {\\n\\t\\tfmt.Fprintln(os.Stderr, err)\\n\\t\\tos.Exit(1)\\n\\t}\\n\\tfor _, item := range response.Data {\\n\\t\\tfmt.Printf(\\\"%+v\\\\n\\\", item)\\n\\t}\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1784696685,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\",\\n            \\\"output_format\\\": \\\"jpeg\\\",\\n            \\\"z_index\\\": 0\\n        },\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1273x265\\\",\\n            \\\"output_format\\\": \\\"png\\\",\\n            \\\"z_index\\\": 1,\\n            \\\"bounding_box\\\": {\\n                \\\"absolute\\\": [383, 120, 1655, 384],\\n                \\\"normalized\\\": [187, 59, 808, 188]\\n            },\\n            \\\"name\\\": \\\"Seedream标题文字\\\",\\n            \\\"description\\\": \\\"黄色大号衬线字体的Seedream标题文字\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"input_images\\\": 1,\\n        \\\"generated_images\\\": 8,\\n        \\\"output_tokens\\\": 23107,\\n        \\\"total_tokens\\\": 23107\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"DSkY3RRA\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    api_key=os.getenv(\\\"ARK_API_KEY\\\"),\\n)\\n\\nresponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"将图片进行精确图层分离，需分离的文字坐标为<bbox>180 64 812 198</bbox>、<bbox>757 210 939 280</bbox>、<bbox>63 212 320 282</bbox>、<bbox>178 714 826 810</bbox>、<bbox>814 819 949 894</bbox>、<bbox>326 824 669 930</bbox>；鹦鹉的坐标为<bbox>347 305 642 997</bbox>。\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"jpeg\\\",\\n    response_format=\\\"url\\\",\\n    extra_body={\\n        \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_layer_input.png\\\",\\n        \\\"layer_decomposition\\\": True,\\n        \\\"watermark\\\": True,\\n    },\\n)\\n\\nfor item in response.data:\\n    print(item)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1784696685,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\",\\n            \\\"output_format\\\": \\\"jpeg\\\",\\n            \\\"z_index\\\": 0\\n        },\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1273x265\\\",\\n            \\\"output_format\\\": \\\"png\\\",\\n            \\\"z_index\\\": 1,\\n            \\\"bounding_box\\\": {\\n                \\\"absolute\\\": [383, 120, 1655, 384],\\n                \\\"normalized\\\": [187, 59, 808, 188]\\n            },\\n            \\\"name\\\": \\\"Seedream标题文字\\\",\\n            \\\"description\\\": \\\"黄色大号衬线字体的Seedream标题文字\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"input_images\\\": 1,\\n        \\\"generated_images\\\": 8,\\n        \\\"output_tokens\\\": 23107,\\n        \\\"total_tokens\\\": 23107\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"NQm0dfYo\"}],\"key\":\"vObJVXec\"},{\"title\":\"交互编辑\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"prompt\\\": \\\"根据手绘草图对图像进行编辑。在左下角标记区域添加一叠真实的杂志或艺术画册，并在右侧标记区域添加一个带杯碟的陶瓷杯咖啡。移除所有草图线条。保持构图不变。让新添加的物体自然融入原有场景中。\\\",\\n    \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_input2.png\\\",\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\": \\\"png\\\",\\n    \\\"response_format\\\": \\\"url\\\",\\n    \\\"watermark\\\": true\\n  }'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 18000,\\n        \\\"total_tokens\\\": 18000\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"Hl0mLspH\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n\\n# Install SDK: python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\n\\nclient = Ark(\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    api_key=os.getenv(\\\"ARK_API_KEY\\\"),\\n)\\n\\nresponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"根据手绘草图对图像进行编辑。在左下角标记区域添加一叠真实的杂志或艺术画册，并在右侧标记区域添加一个带杯碟的陶瓷杯咖啡。移除所有草图线条。保持构图不变。让新添加的物体自然融入原有场景中。\\\",\\n    image=\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_input2.png\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True,\\n)\\n\\nprint(response.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 18000,\\n        \\\"total_tokens\\\": 18000\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"qHLUN2U2\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                .dispatcher(new Dispatcher())\\n                .connectionPool(new ConnectionPool(5, 1, TimeUnit.SECONDS))\\n                .apiKey(System.getenv(\\\"ARK_API_KEY\\\"))\\n                .build();\\n\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest request = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-5-261028\\\")\\n                .prompt(\\\"根据手绘草图对图像进行编辑。在左下角标记区域添加一叠真实的杂志或艺术画册，并在右侧标记区域添加一个带杯碟的陶瓷杯咖啡。移除所有草图线条。保持构图不变。让新添加的物体自然融入原有场景中。\\\")\\n                .image(CreateImageGenerationRequestImage.ofString(\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_input2.png\\\"))\\n                .size(\\\"2K\\\")\\n                .outputFormat(OutputFormat.PNG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .build();\\n\\n        try {\\n            ImageGenerationResponse response = service.generateImages(request);\\n            System.out.println(response.getData().get(0).getUrl());\\n        } finally {\\n            service.shutdownExecutor();\\n        }\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 18000,\\n        \\\"total_tokens\\\": 18000\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"VJpVhnIA\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n\\t\\\"context\\\"\\n\\t\\\"fmt\\\"\\n\\t\\\"os\\\"\\n\\n\\t\\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n\\tmodel \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n\\tclient := arkruntime.NewClientWithApiKey(\\n\\t\\tos.Getenv(\\\"ARK_API_KEY\\\"),\\n\\t\\tarkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n\\t)\\n\\trequest := &model.CreateImageGenerationRequest{\\n\\t\\tModel:          \\\"doubao-seedream-5-5-261028\\\",\\n\\t\\tPrompt:         model.NewOptString(\\\"根据手绘草图对图像进行编辑。在左下角标记区域添加一叠真实的杂志或艺术画册，并在右侧标记区域添加一个带杯碟的陶瓷杯咖啡。移除所有草图线条。保持构图不变。让新添加的物体自然融入原有场景中。\\\"),\\n\\t\\tImage:          model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_input2.png\\\"})),\\n\\t\\tSize:           model.NewOptString(\\\"2K\\\"),\\n\\t\\tOutputFormat:   model.NewOptOutputFormat(model.OutputFormatPNG),\\n\\t\\tResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n\\t\\tWatermark:      model.NewOptBool(true),\\n\\t}\\n\\tresponse, err := client.GenerateImages(context.Background(), request)\\n\\tif err != nil {\\n\\t\\tfmt.Fprintln(os.Stderr, err)\\n\\t\\tos.Exit(1)\\n\\t}\\n\\tfmt.Println(response.Data[0].URL.Or(\\\"\\\"))\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 18000,\\n        \\\"total_tokens\\\": 18000\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"UCiSGOjk\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    api_key=os.getenv(\\\"ARK_API_KEY\\\"),\\n)\\n\\nresponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"根据手绘草图对图像进行编辑。在左下角标记区域添加一叠真实的杂志或艺术画册，并在右侧标记区域添加一个带杯碟的陶瓷杯咖啡。移除所有草图线条。保持构图不变。让新添加的物体自然融入原有场景中。\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    extra_body={\\n        \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream_50_pro_input2.png\\\",\\n        \\\"watermark\\\": True,\\n    },\\n)\\n\\nprint(response.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"2048x2048\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 18000,\\n        \\\"total_tokens\\\": 18000\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"iGvXHdpf\"}],\"key\":\"Dq9GCf6o\"},{\"title\":\"文生图\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"prompt\\\": \\\"充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。\\\",\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"QG4WCXm0\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"Vy9GvNax\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        // Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-5-261028\\\") // Replace with Model ID\\n                .prompt(\\\"充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。\\\")\\n\\n                .size(\\\"2K\\\")\\n                .outputFormat(OutputFormat.PNG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .build();\\n        ImageGenerationResponse imagesResponse = service.generateImages(generateRequest);\\n        System.out.println(imagesResponse.getData().get(0).getUrl());\\n\\n        service.shutdownExecutor();\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"zuvjR0n2\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"os\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        // Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n\\n    generateReq := &model.CreateImageGenerationRequest{\\n       Model:          \\\"doubao-seedream-5-5-261028\\\", // Replace with Model ID\\n       Prompt:         model.NewOptString(\\\"充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。\\\"),\\n\\n       Size:           model.NewOptString(\\\"2K\\\"),\\n       OutputFormat: model.NewOptOutputFormat(outputFormat),\\n       ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n       Watermark:      model.NewOptBool(true),\\n    }\\n\\n    imagesResponse, err := client.GenerateImages(ctx, generateReq)\\n    if err != nil {\\n       fmt.Printf(\\\"generate images error: %v\\\\n\\\", err)\\n       return\\n    }\\n\\n    fmt.Printf(\\\"%s\\\\n\\\", imagesResponse.Data[0].URL.Or(\\\"\\\"))\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"4RD0Lt8D\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    extra_body={\\n        \\\"watermark\\\": True,\\n    },\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"AlHvHHw3\"}],\"key\":\"2iQEE5pR\"},{\"title\":\"图生图\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"prompt\\\": \\\"保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。\\\",\\n    \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imageToimage.png\\\",\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"aD0qMPsE\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。\\\",\\n    image=\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imageToimage.png\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"yfHJM29J\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-5-261028\\\") // Replace with Model ID\\n                .prompt(\\\"保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。\\\")\\n\\n                .image(CreateImageGenerationRequestImage.ofString(\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imageToimage.png\\\"))\\n\\n                .size(\\\"2K\\\")\\n                .outputFormat(OutputFormat.PNG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .build();\\n\\n        ImageGenerationResponse imagesResponse = service.generateImages(generateRequest);\\n        System.out.println(imagesResponse.getData().get(0).getUrl());\\n\\n        service.shutdownExecutor();\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"S5IboPRh\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"os\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n    generateReq := &model.CreateImageGenerationRequest{\\n       Model:          \\\"doubao-seedream-5-5-261028\\\",\\n       Prompt:         model.NewOptString(\\\"保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。\\\"),\\n\\n       Image: model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imageToimage.png\\\"})),\\n\\n       Size:           model.NewOptString(\\\"2K\\\"),\\n       OutputFormat: model.NewOptOutputFormat(outputFormat),\\n       ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n       Watermark:      model.NewOptBool(true),\\n    }\\n\\n    imagesResponse, err := client.GenerateImages(ctx, generateReq)\\n    if err != nil {\\n       fmt.Printf(\\\"generate images error: %v\\\\n\\\", err)\\n       return\\n    }\\n\\n    fmt.Printf(\\\"%s\\\\n\\\", imagesResponse.Data[0].URL.Or(\\\"\\\"))\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"eQ0fukSn\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"保持模特姿势和液态服装的流动形状不变。将服装材质从银色金属改为完全透明的清水（或玻璃）。透过液态水流，可以看到模特的皮肤细节。光影从反射变为折射。\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    extra_body = {\\n        \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imageToimage.png\\\",\\n        \\\"watermark\\\": True\\n    }\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"EPwM7dHb\"}],\"key\":\"MjNYdciz\"},{\"title\":\"多图融合\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"prompt\\\": \\\"将图1的服装换为图2的服装\\\",\\n    \\\"image\\\": [\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimage_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imagesToimage_2.png\\\"],\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"XF68NnPP\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"将图1的服装换为图2的服装\\\",\\n    image=[\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimage_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imagesToimage_2.png\\\"],\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"jg5uszhK\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-5-261028\\\") // Replace with Model ID\\n                .prompt(\\\"将图1的服装换为图2的服装\\\")\\n\\n                .image(CreateImageGenerationRequestImage.ofList(Arrays.asList(\\n                    \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimage_1.png\\\",\\n                    \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imagesToimage_2.png\\\"\\n                )))\\n                .size(\\\"2K\\\")\\n                .outputFormat(OutputFormat.PNG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .build();\\n        ImageGenerationResponse imagesResponse = service.generateImages(generateRequest);\\n        System.out.println(imagesResponse.getData().get(0).getUrl());\\n\\n        service.shutdownExecutor();\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"fRdgkJ46\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"os\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n    generateReq := &model.CreateImageGenerationRequest{\\n       Model:          \\\"doubao-seedream-5-5-261028\\\",\\n       Prompt:         model.NewOptString(\\\"将图1的服装换为图2的服装\\\"),\\n\\n       Image:         model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\n           \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimage_1.png\\\",\\n           \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imagesToimage_2.png\\\",\\n\\n\\n       })),\\n       Size:           model.NewOptString(\\\"2K\\\"),\\n       OutputFormat: model.NewOptOutputFormat(outputFormat),\\n       ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n       Watermark:      model.NewOptBool(true),\\n    }\\n\\n    imagesResponse, err := client.GenerateImages(ctx, generateReq)\\n    if err != nil {\\n       fmt.Printf(\\\"generate images error: %v\\\\n\\\", err)\\n       return\\n    }\\n\\n    fmt.Printf(\\\"%s\\\\n\\\", imagesResponse.Data[0].URL.Or(\\\"\\\"))\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"CMq7Q8fp\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key: https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-5-261028\\\",\\n    prompt=\\\"将图1的服装换为图2的服装\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    extra_body = {\\n        \\\"image\\\": [\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimage_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_5_imagesToimage_2.png\\\"],\\n        \\\"watermark\\\": True,\\n    }\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-5-261028\\\",\\n    \\\"created\\\": 1757323224,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"1760x2368\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": 16280,\\n        \\\"total_tokens\\\": 16280\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"apYIZvl3\"}],\"key\":\"LnQVUs9a\"},{\"title\":\"多参考图生组图\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"prompt\\\": \\\"生成3张女孩和奶牛玩偶在游乐园开心地坐过山车的图片，涵盖早晨、中午、晚上\\\",\\n    \\\"image\\\": [\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_2.png\\\"],\\n    \\\"sequential_image_generation\\\": \\\"auto\\\",\\n    \\\"sequential_image_generation_options\\\": {\\n        \\\"max_images\\\": 3\\n    },\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757388756,\\n  \\\"data\\\": [\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    }\\n  ],\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48960,\\n    \\\"total_tokens\\\": 48960\\n  }\\n}\",\"outputLanguage\":\"\",\"key\":\"75o77UOb\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\nfrom arkruntime.types.images import SequentialImageGenerationOptionsParam\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-0-260128\\\",\\n    prompt=\\\"生成3张女孩和奶牛玩偶在游乐园开心地坐过山车的图片，涵盖早晨、中午、晚上\\\",\\n    image=[\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_2.png\\\"],\\n    size=\\\"2K\\\",\\n    sequential_image_generation=\\\"auto\\\",\\n    sequential_image_generation_options=SequentialImageGenerationOptionsParam(max_images=3),\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    watermark=True\\n)\\n\\n# Iterate through all image data\\nfor image in imagesResponse.data:\\n    # Output the current image's URL and size\\n    print(f\\\"URL: {image.url}, Size: {image.size}\\\")\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757388756,\\n  \\\"data\\\": [\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    }\\n  ],\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48960,\\n    \\\"total_tokens\\\": 48960\\n  }\\n}\",\"outputLanguage\":\"\",\"key\":\"DLjUuUEA\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        SequentialImageGenerationOptions sequentialImageGenerationOptions = new SequentialImageGenerationOptions();\\n        sequentialImageGenerationOptions.setMaxImages(3);\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                 .model(\\\"doubao-seedream-5-0-260128\\\") // Replace with Model ID\\n                 .prompt(\\\"生成3张女孩和奶牛玩偶在游乐园开心地坐过山车的图片，涵盖早晨、中午、晚上\\\")\\n                 .image(CreateImageGenerationRequestImage.ofList(Arrays.asList(\\n                     \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_1.png\\\",\\n                     \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_2.png\\\"\\n                 )))\\n                 .outputFormat(OutputFormat.PNG)\\n                 .size(\\\"2K\\\")\\n                 .sequentialImageGeneration(SequentialImageGenerationMode.AUTO)\\n                 .sequentialImageGenerationOptions(sequentialImageGenerationOptions)\\n\\n                 .responseFormat(ResponseFormat.URL)\\n                 .stream(false)\\n                 .watermark(true)\\n                 .build();\\n        ImageGenerationResponse imagesResponse = service.generateImages(generateRequest);\\n\\n        // Iterate through all image data\\n        if (imagesResponse != null && imagesResponse.getData() != null) {\\n            for (int i = 0; i < imagesResponse.getData().size(); i++) {\\n                // Retrieve image information\\n                String url = imagesResponse.getData().get(i).getUrl();\\n                String size = imagesResponse.getData().get(i).getSize();\\n                System.out.printf(\\\"Image %d:%n\\\", i + 1);\\n                System.out.printf(\\\"  URL: %s%n\\\", url);\\n                System.out.printf(\\\"  Size: %s%n\\\", size);\\n                System.out.println();\\n            }\\n\\n\\n            service.shutdownExecutor();\\n        }\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757388756,\\n  \\\"data\\\": [\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    }\\n  ],\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48960,\\n    \\\"total_tokens\\\": 48960\\n  }\\n}\",\"outputLanguage\":\"\",\"key\":\"6sFBEV0P\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"os\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n    var sequentialImageGeneration model.SequentialImageGenerationMode = \\\"auto\\\"\\n    maxImages := 3\\n    generateReq := &model.CreateImageGenerationRequest{\\n       Model:          \\\"doubao-seedream-5-0-260128\\\",\\n       Prompt:         model.NewOptString(\\\"生成3张女孩和奶牛玩偶在游乐园开心地坐过山车的图片，涵盖早晨、中午、晚上\\\"),\\n       Image:         model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\n           \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_1.png\\\",\\n           \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_2.png\\\",\\n       })),\\n\\n       Size:           model.NewOptString(\\\"2K\\\"),\\n       OutputFormat: model.NewOptOutputFormat(outputFormat),\\n       ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n       Watermark:      model.NewOptBool(true),\\n       SequentialImageGeneration: model.NewOptSequentialImageGenerationMode(sequentialImageGeneration),\\n       SequentialImageGenerationOptions: model.NewOptSequentialImageGenerationOptions(model.SequentialImageGenerationOptions{\\n          MaxImages: model.NewOptInt32(int32(maxImages)),\\n       }),\\n    }\\n\\n    resp, err := client.GenerateImages(ctx, generateReq)\\n    if err != nil {\\n        fmt.Printf(\\\"call GenerateImages error: %v\\\\n\\\", err)\\n        return\\n    }\\n\\n    if resp.Error.IsSet() {\\n        fmt.Printf(\\\"API returned error: %s - %s\\\\n\\\", resp.Error.Value.Code, resp.Error.Value.Message)\\n        return\\n    }\\n\\n    // Output the generated image information\\n    fmt.Printf(\\\"Generated %d images:\\\\n\\\", len(resp.Data))\\n    for i, image := range resp.Data {\\n        var url string\\n        if image.URL.IsSet() {\\n            url = image.URL.Or(\\\"\\\")\\n        } else {\\n            url = \\\"N/A\\\"\\n        }\\n        fmt.Printf(\\\"Image %d: Size: %s, URL: %s\\\\n\\\", i+1, image.Size.Or(\\\"\\\"), url)\\n    }\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757388756,\\n  \\\"data\\\": [\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    }\\n  ],\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48960,\\n    \\\"total_tokens\\\": 48960\\n  }\\n}\",\"outputLanguage\":\"\",\"key\":\"H3VRDIpp\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    model=\\\"doubao-seedream-5-0-260128\\\",\\n    prompt=\\\"生成3张女孩和奶牛玩偶在游乐园开心地坐过山车的图片，涵盖早晨、中午、晚上\\\",\\n    size=\\\"2K\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    extra_body={\\n        \\\"image\\\": [\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_1.png\\\", \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imagesToimages_2.png\\\"],\\n        \\\"watermark\\\": True,\\n        \\\"sequential_image_generation\\\": \\\"auto\\\",\\n        \\\"sequential_image_generation_options\\\": {\\n            \\\"max_images\\\": 3\\n        },\\n    }\\n)\\n\\n# Iterate through all image data\\nfor image in imagesResponse.data:\\n    # Output the current image's URL and size\\n    print(f\\\"URL: {image.url}, Size: {image.size}\\\")\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757388756,\\n  \\\"data\\\": [\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    },\\n    {\\n      \\\"url\\\": \\\"https://...\\\",\\n      \\\"size\\\": \\\"2720x1536\\\"\\n    }\\n  ],\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48960,\\n    \\\"total_tokens\\\": 48960\\n  }\\n}\",\"outputLanguage\":\"\",\"key\":\"7r2BnltZ\"}],\"key\":\"tM03hpbB\"},{\"title\":\"流式输出\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"prompt\\\": \\\"参考图1，生成四图片，图中人物分别带着墨镜，骑着摩托，带着帽子，拿着棒棒糖\\\",\\n    \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imageToimages_1.png\\\",\\n    \\\"sequential_image_generation\\\": \\\"auto\\\",\\n    \\\"sequential_image_generation_options\\\": {\\n        \\\"max_images\\\": 4\\n    },\\n    \\\"size\\\": \\\"2K\\\",\\n    \\\"stream\\\": true,\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"event: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396757,\\n  \\\"image_index\\\": 0,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396785,\\n  \\\"image_index\\\": 1,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"image_index\\\": 2,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.completed\\ndata: {\\n  \\\"type\\\": \\\"image_generation.completed\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48672,\\n    \\\"total_tokens\\\": 48672\\n  }\\n}\\n\\ndata: [DONE]\",\"outputLanguage\":\"\",\"key\":\"GXopU1AL\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\nfrom arkruntime.types.images import SequentialImageGenerationOptionsParam\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nif __name__ == \\\"__main__\\\":\\n    stream = client.images.generate(\\n        # Replace with Model ID\\n        model=\\\"doubao-seedream-5-0-260128\\\",\\n        prompt=\\\"参考图1，生成四图片，图中人物分别带着墨镜，骑着摩托，带着帽子，拿着棒棒糖\\\",\\n        image=\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imageToimages_1.png\\\",\\n        size=\\\"2K\\\",\\n        sequential_image_generation=\\\"auto\\\",\\n        sequential_image_generation_options=SequentialImageGenerationOptionsParam(max_images=4),\\n        output_format=\\\"png\\\",\\n        response_format=\\\"url\\\",\\n        stream=True,\\n        watermark=True\\n    )\\n    for event in stream:\\n        if event is None:\\n            continue\\n        if event.type == \\\"image_generation.partial_failed\\\":\\n            print(f\\\"Stream generate images error: {event.error}\\\")\\n            if event.error is not None and event.error.code.equal(\\\"InternalServiceError\\\"):\\n                break\\n        elif event.type == \\\"image_generation.partial_succeeded\\\":\\n            if event.error is None and event.url:\\n                print(f\\\"recv.Size: {event.size}, recv.Url: {event.url}\\\")\\n        elif event.type == \\\"image_generation.completed\\\":\\n            if event.error is None:\\n                print(\\\"Final completed event:\\\")\\n                print(\\\"recv.Usage:\\\", event.usage)\\n        elif event.type == \\\"image_generation.partial_image\\\":\\n            print(f\\\"Partial image index={event.partial_image_index}, size={len(event.b64_json)}\\\")\",\"inputLanguage\":\"python\",\"outputCode\":\"event: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396757,\\n  \\\"image_index\\\": 0,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396785,\\n  \\\"image_index\\\": 1,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"image_index\\\": 2,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.completed\\ndata: {\\n  \\\"type\\\": \\\"image_generation.completed\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48672,\\n    \\\"total_tokens\\\": 48672\\n  }\\n}\\n\\ndata: [DONE]\",\"outputLanguage\":\"\",\"key\":\"RHvZ3Pcw\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        SequentialImageGenerationOptions sequentialImageGenerationOptions = new SequentialImageGenerationOptions();\\n        sequentialImageGenerationOptions.setMaxImages(4);\\n        // CreateImageGenerationRequestImage requires ark-runtime 0.6.0 or later.\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                 .model(\\\"doubao-seedream-5-0-260128\\\") //Replace with Model ID .\\n                 .prompt(\\\"参考图1，生成四图片，图中人物分别带着墨镜，骑着摩托，带着帽子，拿着棒棒糖\\\")\\n                 .image(CreateImageGenerationRequestImage.ofString(\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imageToimages_1.png\\\"))\\n                 .size(\\\"2K\\\")\\n                 .sequentialImageGeneration(SequentialImageGenerationMode.AUTO)\\n                 .sequentialImageGenerationOptions(sequentialImageGenerationOptions)\\n                 .outputFormat(OutputFormat.PNG)\\n                 .responseFormat(ResponseFormat.URL)\\n                 .stream(true)\\n                 .watermark(true)\\n                 .build();\\n        System.out.println(generateRequest.toString());\\n\\n        service.streamGenerateImages(generateRequest)\\n                .doOnError(Throwable::printStackTrace)\\n                .blockingForEach(\\n                        choice -> {\\n                            if (choice == null) return;\\n                            if (ImageGenerationStreamEventType.IMAGE_GENERATION_PARTIAL_FAILED.equals(choice.getType())) {\\n                                if (choice.getError() != null) {\\n                                    System.err.println(\\\"Stream generate images error: \\\" + choice.getError());\\n                                    if (choice.getError().getCode() != null && choice.getError().getCode().equals(\\\"InternalServiceError\\\")) {\\n                                        throw new RuntimeException(\\\"Server error, terminating stream.\\\");\\n                                    }\\n                                }\\n                            }\\n                            else if (ImageGenerationStreamEventType.IMAGE_GENERATION_PARTIAL_SUCCEEDED.equals(choice.getType())) {\\n                                if (choice.getError() == null && choice.getUrl() != null && !choice.getUrl().isEmpty()) {\\n                                    System.out.printf(\\\"recv.Size: %s, recv.Url: %s%n\\\", choice.getSize(), choice.getUrl());\\n                                }\\n                            }\\n                            else if (ImageGenerationStreamEventType.IMAGE_GENERATION_COMPLETED.equals(choice.getType())) {\\n                                if (choice.getError() == null && choice.getUsage() != null) {\\n                                    System.out.println(\\\"recv.Usage: \\\" + choice.getUsage().toString());\\n                                }\\n                            }\\n                        }\\n                );\\n        service.shutdownExecutor();\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"event: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396757,\\n  \\\"image_index\\\": 0,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396785,\\n  \\\"image_index\\\": 1,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"image_index\\\": 2,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.completed\\ndata: {\\n  \\\"type\\\": \\\"image_generation.completed\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48672,\\n    \\\"total_tokens\\\": 48672\\n  }\\n}\\n\\ndata: [DONE]\",\"outputLanguage\":\"\",\"key\":\"NGYM0oMj\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"io\\\"\\n    \\\"os\\\"\\n    \\\"strings\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n    var sequentialImageGeneration model.SequentialImageGenerationMode = \\\"auto\\\"\\n    maxImages := 4\\n    generateReq := &model.CreateImageGenerationRequest{\\n       Model:          \\\"doubao-seedream-5-0-260128\\\",\\n       Prompt:         model.NewOptString(\\\"参考图1，生成四图片，图中人物分别带着墨镜，骑着摩托，带着帽子，拿着棒棒糖\\\"),\\n       Image: model.NewOptCreateImageGenerationRequestImage(model.NewStringArrayCreateImageGenerationRequestImage([]string{\\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imageToimages_1.png\\\"})),\\n       Size:           model.NewOptString(\\\"2K\\\"),\\n       OutputFormat: model.NewOptOutputFormat(outputFormat),\\n       ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n       Watermark:      model.NewOptBool(true),\\n       SequentialImageGeneration: model.NewOptSequentialImageGenerationMode(sequentialImageGeneration),\\n       SequentialImageGenerationOptions: model.NewOptSequentialImageGenerationOptions(model.SequentialImageGenerationOptions{\\n          MaxImages: model.NewOptInt32(int32(maxImages)),\\n       }),\\n    }\\n\\n    stream, err := client.GenerateImagesStreaming(ctx, generateReq)\\n    if err != nil {\\n       fmt.Printf(\\\"call GenerateImagesStreaming error: %v\\\\n\\\", err)\\n       return\\n    }\\n    defer stream.Close()\\n    for {\\n       recv, err := stream.Recv()\\n       if err == io.EOF {\\n          break\\n       }\\n       if err != nil {\\n          fmt.Printf(\\\"Stream generate images error: %v\\\\n\\\", err)\\n          break\\n       }\\n       if recv.Type == model.ImageGenerationStreamEventTypeImageGenerationPartialFailed {\\n          fmt.Printf(\\\"Stream generate images error: %v\\\\n\\\", recv.Error)\\n          if strings.EqualFold(recv.Error.Value.Code, \\\"InternalServiceError\\\") {\\n             break\\n          }\\n       }\\n       if recv.Type == model.ImageGenerationStreamEventTypeImageGenerationPartialSucceeded {\\n          if !recv.Error.IsSet() && recv.URL.IsSet() {\\n             fmt.Printf(\\\"recv.Size: %s, recv.Url: %s\\\\n\\\", recv.Size.Or(\\\"\\\"), recv.URL.Or(\\\"\\\"))\\n          }\\n       }\\n       if recv.Type == model.ImageGenerationStreamEventTypeImageGenerationCompleted {\\n          if !recv.Error.IsSet() {\\n             fmt.Printf(\\\"recv.Usage: %v\\\\n\\\", recv.Usage.Or(model.Usage{}))\\n          }\\n       }\\n    }\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"event: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396757,\\n  \\\"image_index\\\": 0,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396785,\\n  \\\"image_index\\\": 1,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"image_index\\\": 2,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.completed\\ndata: {\\n  \\\"type\\\": \\\"image_generation.completed\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48672,\\n    \\\"total_tokens\\\": 48672\\n  }\\n}\\n\\ndata: [DONE]\",\"outputLanguage\":\"\",\"key\":\"6WM5mwVu\"},{\"title\":\"Python-OpenAI\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"openai\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\nfrom openai import OpenAI\\n\\nclient = OpenAI(\\n    # The base URL for model invocation .\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nif __name__ == \\\"__main__\\\":\\n    stream = client.images.generate(\\n        model=\\\"doubao-seedream-5-0-260128\\\",\\n        prompt=\\\"参考图1，生成四图片，图中人物分别带着墨镜，骑着摩托，带着帽子，拿着棒棒糖\\\",\\n        size=\\\"2K\\\",\\n        output_format=\\\"png\\\",\\n        response_format=\\\"b64_json\\\",\\n        stream=True,\\n        extra_body={\\n            \\\"image\\\": \\\"https://arkdocs.tos-cn-beijing.volces.com/images/image-generation/seedream4_imageToimages_1.png\\\",\\n            \\\"watermark\\\": True,\\n            \\\"sequential_image_generation\\\": \\\"auto\\\",\\n            \\\"sequential_image_generation_options\\\": {\\n                \\\"max_images\\\": 4\\n            },\\n        },\\n    )\\n    for event in stream:\\n        if event is None:\\n            continue\\n        elif event.type == \\\"image_generation.partial_succeeded\\\":\\n            if event.b64_json is not None:\\n                print(f\\\"size={len(event.b64_json)}, base_64={event.b64_json}\\\")\\n        elif event.type == \\\"image_generation.completed\\\":\\n            if event.usage is not None:\\n                print(\\\"Final completed event:\\\")\\n                print(\\\"recv.Usage:\\\", event.usage)\",\"inputLanguage\":\"python\",\"outputCode\":\"event: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396757,\\n  \\\"image_index\\\": 0,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396785,\\n  \\\"image_index\\\": 1,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.partial_succeeded\\ndata: {\\n  \\\"type\\\": \\\"image_generation.partial_succeeded\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"image_index\\\": 2,\\n  \\\"url\\\": \\\"https://...\\\",\\n  \\\"size\\\": \\\"2496x1664\\\"\\n}\\n\\nevent: image_generation.completed\\ndata: {\\n  \\\"type\\\": \\\"image_generation.completed\\\",\\n  \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n  \\\"created\\\": 1757396825,\\n  \\\"usage\\\": {\\n    \\\"generated_images\\\": 3,\\n    \\\"output_tokens\\\": 48672,\\n    \\\"total_tokens\\\": 48672\\n  }\\n}\\n\\ndata: [DONE]\",\"outputLanguage\":\"\",\"key\":\"V44woIBe\"}],\"key\":\"TcdeOeA4\"},{\"title\":\"联网搜索\",\"children\":[{\"title\":\"cURL\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"value\":[1]}},\"inputCode\":\"curl https://ark.cn-beijing.volces.com/api/v3/images/generations \\\\\\n  -H \\\"Content-Type: application/json\\\" \\\\\\n  -H \\\"Authorization: Bearer $ARK_API_KEY\\\" \\\\\\n  -d '{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"prompt\\\": \\\"制作一张上海未来5日的天气预报图，采用现代扁平化插画风格，清晰展示每日天气、温度和穿搭建议。 整体为横向排版，标题为“上海未来5日天气预报”，包含5个等宽的垂直卡片，从左到右依次排列。 整体风格为现代、干净、友好的扁平化矢量插画风格，线条清晰，色彩柔和。人物形象采用年轻男女的卡通插画，表情自然，姿态放松，服装细节清晰。\\\",\\n    \\\"size\\\": \\\"2048x2048\\\",\\n    \\\"tools\\\": [\\n      {\\n          \\\"type\\\": \\\"web_search\\\"\\n      }\\n  ],\\n    \\\"output_format\\\":\\\"png\\\",\\n    \\\"response_format\\\": \\\"url\\\",\\n    \\\"watermark\\\": true\\n}'\",\"inputLanguage\":\"bash\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx,\\n        \\\"tool_usage\\\": {\\n            \\\"web_search\\\": 1 // 实际搜索次数\\n        }\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"67Hj3OGX\"},{\"title\":\"Python\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"arkruntime\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"import os\\n# Install SDK:  python -m pip install --upgrade arkruntime\\nfrom arkruntime import Ark\\nfrom arkruntime.types.images import ToolParam\\n\\nclient = Ark(\\n    # The base URL for model invocation\\n    base_url=\\\"https://ark.cn-beijing.volces.com/api/v3\\\",\\n    # Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n    api_key=os.getenv('ARK_API_KEY'),\\n)\\n\\nimagesResponse = client.images.generate(\\n    # Replace with Model ID\\n    model=\\\"doubao-seedream-5-0-260128\\\",\\n    prompt=\\\"制作一张上海未来5日的天气预报图，采用现代扁平化插画风格，清晰展示每日天气、温度和穿搭建议。 整体为横向排版，标题为“上海未来5日天气预报”，包含5个等宽的垂直卡片，从左到右依次排列。 整体风格为现代、干净、友好的扁平化矢量插画风格，线条清晰，色彩柔和。人物形象采用年轻男女的卡通插画，表情自然，姿态放松，服装细节清晰。\\\",\\n    size=\\\"2048x2048\\\",\\n    output_format=\\\"png\\\",\\n    response_format=\\\"url\\\",\\n    tools=[ToolParam(type=\\\"web_search\\\")],\\n    watermark=True,\\n\\n)\\n\\nprint(imagesResponse.data[0].url)\",\"inputLanguage\":\"python\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx,\\n        \\\"tool_usage\\\": {\\n            \\\"web_search\\\": 1 // 实际搜索次数\\n        }\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"uU3JASYZ\"},{\"title\":\"Java\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"com.fasterxml.jackson.core:jackson-core\",\"packageVersion\":\"2.21.3\"},{\"packageName\":\"com.volcengine:ark-runtime\",\"packageVersion\":\"0.9.0\"}],\"value\":[1,2]}},\"inputCode\":\"package com.ark.sample;\\n\\n\\nimport com.volcengine.ark.runtime.models.images.*;\\nimport com.volcengine.ark.runtime.service.ArkService;\\nimport okhttp3.ConnectionPool;\\nimport okhttp3.Dispatcher;\\n\\nimport java.util.Arrays;\\nimport java.util.List;\\nimport java.util.concurrent.TimeUnit;\\n\\npublic class ImageGenerationsExample {\\n    public static void main(String[] args) {\\n        // Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n        String apiKey = System.getenv(\\\"ARK_API_KEY\\\");\\n        ConnectionPool connectionPool = new ConnectionPool(5, 1, TimeUnit.SECONDS);\\n        Dispatcher dispatcher = new Dispatcher();\\n        ArkService service = ArkService.builder()\\n                .baseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\")\\n                // The base URL for model invocation\\n                .dispatcher(dispatcher)\\n                .connectionPool(connectionPool)\\n                .apiKey(apiKey)\\n                .build();\\n\\n        // Create ContentGenerationTool\\n        Tool contentGenerationTool = new Tool();\\n        contentGenerationTool.setType(ToolType.WEB_SEARCH);\\n\\n        // Create list of tools\\n        List<Tool> tools = Arrays.asList(contentGenerationTool);\\n\\n        CreateImageGenerationRequest generateRequest = CreateImageGenerationRequest.builder()\\n                .model(\\\"doubao-seedream-5-0-260128\\\") // Replace with Model ID\\n                .prompt(\\\"制作一张上海未来5日的天气预报图，采用现代扁平化插画风格，清晰展示每日天气、温度和穿搭建议。 整体为横向排版，标题为“上海未来5日天气预报”，包含5个等宽的垂直卡片，从左到右依次排列。 整体风格为现代、干净、友好的扁平化矢量插画风格，线条清晰，色彩柔和。人物形象采用年轻男女的卡通插画，表情自然，姿态放松，服装细节清晰。\\\")\\n                .size(\\\"2048x2048\\\")\\n                .outputFormat(OutputFormat.PNG)\\n                .responseFormat(ResponseFormat.URL)\\n                .watermark(true)\\n                .tools(tools)\\n                .build();\\n\\n        ImageGenerationResponse imagesResponse = service.generateImages(generateRequest);\\n        System.out.println(imagesResponse.getData().get(0).getUrl());\\n\\n        service.shutdownExecutor();\\n    }\\n}\",\"inputLanguage\":\"java\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx,\\n        \\\"tool_usage\\\": {\\n            \\\"web_search\\\": 1 // 实际搜索次数\\n        }\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"3uMg6VGl\"},{\"title\":\"Go\",\"debug\":{\"openDebug\":true,\"debugParams\":{\"envs\":[\"ARK_API_KEY\"],\"packages\":[{\"packageName\":\"github.com/volcengine/volcengine-go-sdk\",\"packageVersion\":\"\"}],\"value\":[1,2]}},\"inputCode\":\"package main\\n\\nimport (\\n    \\\"context\\\"\\n    \\\"fmt\\\"\\n    \\\"os\\\"\\n\\n    \\\"github.com/volcengine/ark-runtime-go/arkruntime\\\"\\n    model \\\"github.com/volcengine/ark-runtime-go/arkruntime/model/images\\\"\\n)\\n\\nfunc main() {\\n    client := arkruntime.NewClientWithApiKey(\\n        // Get API Key：https://ark.volcengine.com/region:cn-beijing/apikey\\n        os.Getenv(\\\"ARK_API_KEY\\\"),\\n        // The base URL for model invocation\\n        arkruntime.WithBaseUrl(\\\"https://ark.cn-beijing.volces.com/api/v3\\\"),\\n    )\\n    ctx := context.Background()\\n    outputFormat := model.OutputFormatPNG\\n\\n    generateReq := &model.CreateImageGenerationRequest{\\n        Model:          \\\"doubao-seedream-5-0-260128\\\", // Replace with Model ID\\n        Prompt:         model.NewOptString(\\\"制作一张上海未来5日的天气预报图，采用现代扁平化插画风格，清晰展示每日天气、温度和穿搭建议。 整体为横向排版，标题为“上海未来5日天气预报”，包含5个等宽的垂直卡片，从左到右依次排列。 整体风格为现代、干净、友好的扁平化矢量插画风格，线条清晰，色彩柔和。人物形象采用年轻男女的卡通插画，表情自然，姿态放松，服装细节清晰。\\\"),\\n        Size:           model.NewOptString(\\\"2048x2048\\\"),\\n        OutputFormat: model.NewOptOutputFormat(outputFormat),\\n        ResponseFormat: model.NewOptResponseFormat(model.ResponseFormatURL),\\n        Watermark:      model.NewOptBool(true),\\n        Tools: []model.Tool{\\n            {\\n                Type: model.ToolTypeWebSearch,\\n            },\\n        },\\n    }\\n    imagesResponse, err := client.GenerateImages(ctx, generateReq)\\n    if err != nil {\\n       fmt.Printf(\\\"generate images error: %v\\\\n\\\", err)\\n       return\\n    }\\n\\n    fmt.Printf(\\\"%s\\\\n\\\", imagesResponse.Data[0].URL.Or(\\\"\\\"))\\n}\",\"inputLanguage\":\"go\",\"outputCode\":\"{\\n    \\\"model\\\": \\\"doubao-seedream-5-0-260128\\\",\\n    \\\"created\\\": 1757321139,\\n    \\\"data\\\": [\\n        {\\n            \\\"url\\\": \\\"https://...\\\",\\n            \\\"size\\\": \\\"3104x1312\\\"\\n        }\\n    ],\\n    \\\"usage\\\": {\\n        \\\"generated_images\\\": 1,\\n        \\\"output_tokens\\\": xxx,\\n        \\\"total_tokens\\\": xxx,\\n        \\\"tool_usage\\\": {\\n            \\\"web_search\\\": 1 // 实际搜索次数\\n        }\\n    }\\n}\",\"outputLanguage\":\"\",\"key\":\"fhuBf4rz\"}],\"key\":\"L5QqFtTi\"}]}"} />);
```
