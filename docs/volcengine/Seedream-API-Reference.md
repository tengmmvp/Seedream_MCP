`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`

本文介绍 Doubao Seedream 5.0 pro、Doubao Seedream 5.0 lite、Doubao Seedream 4.5 及 Doubao Seedream 4.0 图片生成模型的调用 API，包括输入输出参数、取值范围、注意事项等信息，供您使用接口时查阅字段含义。

**模型能力**

- **Seedream 5.0 pro<mark><sup>new</sup></mark>**
  - 图层拆分：支持将单张图片拆解为底图（1 张）+ 多个图层输出（最多 16 个图层）。

  - 交互编辑：支持通过坐标、框选、箭头等多种方式指定编辑位置，精准编辑图片。

  - 生成单图（不支持配置 `sequential_image_generation`）
    - 多图生图：输入多张参考图片（2\-10）+ 文本提示词，生成单张图片。

    - 单图生图：输入单张参考图片 + 文本提示词，生成单张图片。

    - 文生图：输入文本提示词，生成单张图片。

  - 暂不支持组图生成、联网搜索、流式输出。

- **Seedream 5.0 lite** 、 **Seedream 4.5 / 4.0**
  - 生成组图（组图：基于您输入的内容，生成的一组内容关联的图片；需配置 `sequential_image_generation` 为 `auto`）
    - 多图生组图：输入多张参考图片（2\-14）+ 文本提示词，生成一组内容关联的图片（输入的参考图数量 + 最终生成的图片数量 ≤ 15 张）。

    - 单图生组图：输入单张参考图片 + 文本提示词，生成一组内容关联的图片（最多生成 14 张图片）。

    - 文生组图：输入文本提示词，生成一组内容关联的图片（最多生成 15 张图片）。

  - 生成单图（配置 `sequential_image_generation` 为 `disabled`）
    - 多图生图：输入多张参考图片（2\-14）+ 文本提示词，生成单张图片。

    - 单图生图：输入单张参考图片 + 文本提示词，生成单张图片。

    - 文生图：输入文本提示词，生成单张图片。

&nbsp;

<span id=".6Ym05p2D"></span>

## 鉴权

本接口支持鉴权方式如下，详情请参见 [Base URL 及鉴权](https://docs.volcengine.com/docs/82379/1298459)。

- API Key 鉴权，请在 [API Key 管理](https://console.volcengine.com/ark/region:cn-beijing/apiKey) 页面，获取长效 API Key。

---

<span id="request-parameters"></span>

## 请求参数

<span id=".Ym9keS3lj4LmlbA="></span>

### Body 参数

**model** `string` `必选` | 模型 ID

您需要调用的模型的 ID（Model ID），[开通模型服务](https://console.volcengine.com/ark/region:cn-beijing/openManagement?LLM=%7B%7D&OpenTokenDrawer=false)，并 [查询 Model ID](https://docs.volcengine.com/docs/82379/1330310#9df4d9fd)。

您也可通过 Endpoint ID 来调用模型，获得限流、计费类型（前付费 / 后付费）、运行状态查询、监控、安全等高级能力，可参考 [获取 Endpoint ID](https://docs.volcengine.com/docs/82379/1099522)。

**prompt** `string` | 提示词

用于生成图像或指定图层拆分意图的提示词。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

- <div data-tips="true" data-tips-type="tip"><strong>提示词语言支持</strong> ：所有模型均支持中英文提示词；</div>
  - <div data-tips="true" data-tips-type="tip"><code>Seedream 5.0 pro</code> 额外支持俄语、阿拉伯语、菲律宾语、泰语、土耳其语、韩语、马来语、西班牙语、葡萄牙语、印尼语、法语、德语、越南语、日语。</div>

- <div data-tips="true" data-tips-type="tip"><strong>提示词字数建议</strong> ：中文提示词不超过 300 字，英文提示词不超过 600 词。字数过多信息容易分散，模型可能因此忽略细节，只关注重点，造成图片缺失部分元素。</div>

**图片生成场景 ** **`必选`**

用于描述生成图像的内容，模型根据提示词生成对应图像。（查看提示词指南：[Seedream 4.0-5.0 提示词指南](https://docs.volcengine.com/docs/82379/1829186)）

**图层拆分场景 ** **`可选`**

用于指定图层拆分意图，模型根据提示词意图识别并拆分指定元素。

若不传入提示词，模型将自动识别图片中的所有主要元素并拆分为独立图层。

**image** `string / string[]` | 参考图片

输入的图片信息，支持 URL 或 Base64 编码。

- 图片 URL：请确保图片 URL 可被访问。

- Base64 编码：请遵循此格式 `data:image/<图片格式>;base64,<Base64 编码>`。注意 `<图片格式>` 需小写，如 `data:image/png;base64,<base64_image>`。

**图片生成场景 ** **`可选`**

Seedream 5.0 pro 最多支持传入 10 张参考图；Seedream 5.0 lite / 4.5 / 4.0 最多支持传入 14 张参考图。

**单张图片传入要求** ：

- 图片格式：jpeg、png、webp、bmp、tiff、gif、heic、heif

- 宽高比（宽 / 高）范围：[1/16, 16]

- 宽高长度（px）\> 14

- 大小：不超过 30MB

- 总像素：[196, `6000×6000`（3600万）]（对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制）

**图层拆分场景 ** **`必选`**

当开启图层拆分模式时（`layer_decomposition` 为 `true`），`image` 为必选参数，且仅支持输入单张图片（传入多张报错）。

**单张图片传入要求** ：

- 图片格式：png、jpeg

- 宽高比：[1/16, 16]

- 大小：不超过 30MB

- 总像素（宽×高）范围：[`512×512`（262144）, `6000×6000`（3600万）]（对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制）

**layer_decomposition<mark><sup>new</sup></mark>** `boolean` `默认值 false` | 图层拆分开关

控制是否开启图层拆分功能。

图层拆分会将单张图片的主体、背景、文字等内容自动拆解为 1 张底图和最多 16 个可独立编辑的图层。每个图层为带透明通道的 PNG 图片。

- `true`：图层拆分模式。模型将输入图拆解为一张底图和多个图层。

- `false`：图片生成模式，不进行图层拆分。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">图层拆分模式使用说明</div>

- <div data-tips="true" data-tips-type="tip">仅支持输入单张待拆分图，传入多张报错。</div>

- <div data-tips="true" data-tips-type="tip">任一图层生成失败，整体请求报错，不支持部分成功。</div>

- <div data-tips="true" data-tips-type="tip">若提示词要求的拆分数量超过上限，可能导致部分图层信息丢失。</div>

- <div data-tips="true" data-tips-type="tip">响应结构体 <code>data</code> 中，将返回每个产出图层的位置和内容信息，包括图层顺序（<code>z_index</code>）、边界框信息（<code>bounding_box</code>）、名称（<code>name</code>）和描述（<code>description</code>）。</div>

**模型支持** ：

- `Seedream 5.0 pro`

**size** `string` | 图像尺寸

指定生成图像的尺寸信息。对于不同模型和不同场景，参数的配置方式、可选分辨率、默认值、总像素取值范围、宽高比取值范围不同，请展开以下各模型对应说明查看。

**Seedream 5.0 pro（图片生成场景）**

支持以下两种方式，不可混用：

- 方式 1（推荐）：指定分辨率档位，并在 prompt 中用自然语言描述图片宽高比、图片形状或图片用途，最终由模型判断生成图片的大小。
  - 默认值：`2K`

  - 可选值：`1K`、`1.5K`、`2K`

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">价格说明</div>

<div data-tips="true" data-tips-type="warning"><code>1.5K</code> 与 <code>1K</code> 价格相同（详情参见 <a href="https://docs.volcengine.com/docs/82379/1544106#457edfd0">模型价格</a>），且图片生成效果更优。</div>

- 方式 2：指定宽高像素值（`宽x高`）。
  - 总像素取值范围：[`1280x720`（921600）, `2048x2048x1.1025`（4624220）]

  - 宽高比取值范围：[1/16, 16]

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。</div>

- <div data-tips="true" data-tips-type="tip"><strong>有效示例</strong> ：<code>2048x1024</code>。总像素值 2048x1024=2097152，符合 [921600, 4624220] 的区间要求；宽高比 2048/1024=2，符合 [1/16, 16] 的区间要求，故该示例值有效。</div>

- <div data-tips="true" data-tips-type="tip"><strong>无效示例</strong> ：<code>512x512</code>。总像素值 512x512=262144，未达到 921600 的最低要求，故该示例值无效。</div>

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

**Seedream 5.0 pro（图层拆分场景）**

仅支持通过指定分辨率档位的方式设置。输出图的分辨率规则如下：

- **底图** ：输出底图的分辨率和 `size` 指定的分辨率一致；输出底图和原待拆分图的宽高比一致。

- **各图层** ：输出图层的分辨率和 `size` 指定的分辨率接近；每个输出图层和其在原图中的宽高比一致。

`size` 的默认值与可选值：

- 默认值：`auto`

- 可选值：`1K`、`1.5K`、`2K`、`auto`（根据输入图的尺寸和宽高比进行输出）

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">价格说明</div>

<div data-tips="true" data-tips-type="warning"><code>1.5K</code> 与 <code>1K</code> 价格相同（详情参见 <a href="https://docs.volcengine.com/docs/82379/1544106#457edfd0">模型价格</a>）。</div>

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">auto 适配规则</div>

<div data-tips="true" data-tips-type="tip">auto 模式下，模型将根据输入图片中底图和每个图层的原始尺寸进行输出：</div>

- <div data-tips="true" data-tips-type="tip">若输入图片中底图和每个图层的原始尺寸在 [<code>1280x720</code>（921600）, <code>2048x2048x1.1025</code>（4624220）] 之间，按照原尺寸输出底图与每个图层；且各自保持其在原图中的宽高比。</div>

- <div data-tips="true" data-tips-type="tip">若输入图片中底图和每个图层的原始尺寸小于 1K，按 1K 输出底图与每个图层；且各自保持其在原图中的宽高比。</div>

- <div data-tips="true" data-tips-type="tip">若输入图片中底图和每个图层的原始尺寸大于 2K，按 2K 输出底图与每个图层；且各自保持其在原图中的宽高比。</div>

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。</div>

- <div data-tips="true" data-tips-type="tip"><strong>有效示例</strong> ：<code>3750x1250</code>。总像素值 3750x1250=4687500，符合 [3686400, 16777216] 的区间要求；宽高比 3750/1250=3，符合 [1/16, 16] 的区间要求，故该示例值有效。</div>

- <div data-tips="true" data-tips-type="tip"><strong>无效示例</strong> ：<code>1500x1500</code>。总像素值 1500x1500=2250000，未达到 3686400 的最低要求；宽高 1500/1500=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。</div>

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。</div>

- <div data-tips="true" data-tips-type="tip"><strong>有效示例</strong> ：<code>3750x1250</code>。总像素值 3750x1250=4687500，符合 [3686400, 16777216] 的区间要求；宽高比 3750/1250=3，符合 [1/16, 16] 的区间要求，故该示例值有效。</div>

- <div data-tips="true" data-tips-type="tip"><strong>无效示例</strong> ：<code>1500x1500</code>。总像素值 1500x1500=2250000，未达到 3686400 的最低要求；宽高 1500/1500=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。</div>

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

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">采用方式 2 时，需同时满足总像素取值范围和宽高比取值范围。其中，总像素是对单张图宽度和高度的像素乘积限制，而不是对宽度或高度的单独值进行限制。</div>

- <div data-tips="true" data-tips-type="tip"><strong>有效示例</strong> ：<code>1600x600</code>。总像素值 1600x600=960000，符合 [921600, 16777216] 的区间要求；宽高比 1600/600=8/3，符合 [1/16, 16] 的区间要求，故该示例值有效。</div>

- <div data-tips="true" data-tips-type="tip"><strong>无效示例</strong> ：<code>800x800</code>。总像素值 800x800=640000，未达到 921600 的最低要求；宽高 800/800=1，虽符合 [1/16, 16] 的区间要求，但未同时满足两项限制，故该示例值无效。</div>

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

- `fast`：快速模式，生成内容的耗时更短，效果略低于标准模式；Seedream 5.0 lite / 4.5 当前不支持。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">如您的业务对生成时延较为敏感，推荐使用 <code>fast</code> 模式以节省等待时间。</div>

**output_format** `string` `默认值 jpeg` | 图像格式

指定生成图像的文件格式。可选值：

- `png`

- `jpeg`。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

- <div data-tips="true" data-tips-type="warning">图层拆分场景下，<code>output_format</code> 仅控制底图的输出格式，图层始终以 <code>png</code> 格式输出。</div>

**模型支持** ：

- `Seedream 5.0 pro`

- `Seedream 5.0 lite`

**background<mark><sup>new</sup></mark>** `string` `默认值 opaque` | 图片透明通道

用于控制是否生成带透明通道的图片。可选值：

- `transparent`：透明背景模式，输出带有透明背景的图。

- `opaque`：不透明背景模式，生成常规的实体背景图。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">使用限制</div>

- <div data-tips="true" data-tips-type="warning">仅支持图生图场景，且只支持输入 <strong>1 张带透明通道</strong> 的图片；</div>

- <div data-tips="true" data-tips-type="warning">透明背景模式下，输出图片默认为 <code>png</code> 格式，若同时配置 <code>output_format</code> 为 <code>jpeg</code>，将触发报错；</div>

- <div data-tips="true" data-tips-type="warning">若传入了不支持透明通道的文件格式（如 <code>jpeg</code>），将触发报错。</div>

**模型支持** ：

- `Seedream 5.0 pro`

**response_format** `string` `默认值 url` | 返回格式

指定生成图像的返回格式。支持以下两种返回方式：

- `url`：返回图片下载链接， **链接在图片生成后 24 小时内有效，请及时下载图片** 。

- `b64_json`：以 Base64 编码字符串的 JSON 格式返回图像数据。

**sequential_image_generation** `string` `默认值 disabled` | 组图模式

控制是否关闭组图功能（组图：基于您输入的内容，生成的一组内容关联的图片）。

- `auto`：自动判断模式，模型会根据用户提供的提示词自主判断是否返回组图以及组图包含的图片数量。

- `disabled`：关闭组图功能，模型只会生成一张图。

组图输出示例详见 [Seedream 图像创作教程 - 组图输出](https://docs.volcengine.com/docs/82379/1824121#ec79cfda)。

**模型支持** ：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**sequential_image_generation_options** `object` | 组图配置

组图功能的配置。仅当 `sequential_image_generation` 为 `auto` 时生效。

**模型支持** ：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**max_images** `integer` `默认值 15` | 最大生成数量

`sequential_image_generation_options.max_images`

指定本次请求，最多可生成的图片数量。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">实际可生成的图片数量，除受到 <code>max_images</code> 影响外，还受到输入的参考图数量影响。 <strong>输入的参考图数量+最终生成的图片数量≤15 张</strong> 。</div>

**取值范围** ：`[1, 15]`

**stream** `boolean` `默认值 false` | 流式输出开关

控制是否开启流式输出模式。

- `false`：非流式输出模式，等待所有图片全部生成结束后再一次性返回所有信息。

- `true`：流式输出模式，即时返回每张图片输出的结果。在生成单图和组图的场景下，流式输出模式均生效。

流式输出示例详见 [Seedream 图像创作教程 - 流式输出](https://docs.volcengine.com/docs/82379/1824121#e5bef0d7)。

**模型支持** ：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**tools** `object[]` | 工具配置

配置模型要调用的工具。

**模型支持** ：

- `Seedream 5.0 lite`

**type** `string` `必选` | 工具类型

`tools.type`

指定使用的工具类型。

- `web_search`：联网搜索功能。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

- <div data-tips="true" data-tips-type="tip">开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等），提升生成图片的时效性，但也会增加一定的时延。</div>

- <div data-tips="true" data-tips-type="tip">实际搜索次数可通过字段 <code>usage.tool_usage.web_search</code> 查询，如果为 0 表示未搜索。</div>

**watermark** `boolean` `默认值 true` | 水印开关

是否在生成的图片中添加水印。

- `false`：不添加水印。

- `true`：在图片右下角添加“AI 生成”字样的水印标识。

&nbsp;

<span id=".5ZON5bqU5Y-C5pWw"></span>

## 响应参数

<span id=".6Z2e5rWB5byP6LCD55So5ZON5bqU"></span>

### 非流式调用响应

**created** `integer` | 创建时间

本次请求创建时间的 Unix 时间戳（秒）

**model** `string` | 模型 ID

本次请求使用的模型 ID（模型名称\-版本）

**data** `object[]` | 图像数据

输出图像的信息。不同任务场景返回字段有所区别，其中 `url`、`b64_json`、`size`、`output_format` 为公共返回字段。

- 组图生成场景，若某张图生成失败，额外返回该图片的错误信息（`error`）。

- 图层拆分场景，额外返回每个图层的叠放顺序（`z_index`）、名称（`name`）、描述（`description`）、边界框坐标（`bounding_box`）。

**url** `string` | 图片 URL

`data.url`

图片 URL，当 `response_format` 指定为 `url` 时返回。该链接将在生成后 **24 小时内失效** ，请务必及时保存图像。

推荐配置火山引擎 TOS 提供的数据订阅功能，将您的模型推理产物自动转存到自己的 TOS 桶中，便于长期备份或二次加工。详细介绍请参见 [TOS 数据订阅](https://docs.volcengine.com/docs/6349/1366744)。

**b64_json** `string` | 图片 Base64 数据

`data.b64_json`

图片的 Base64 信息，当 `response_format` 指定为 `b64_json` 时返回

**size** `string` | 图像尺寸

`data.size`

图像的宽高像素值，格式 `<宽像素>x<高像素>`，例如 `2048x2048`。

**output_format<mark><sup>new</sup></mark>** `string` | 输出格式

`data.output_format`

输出图像的格式信息（`png` 或 `jpeg`）。

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">注意</div>

- <div data-tips="true" data-tips-type="warning">图层拆分场景下，<code>output_format</code> 仅控制底图的输出格式，图层始终以 <code>png</code> 格式输出。</div>

**模型支持** ：

- `Seedream 5.0 pro`

**图层拆分场景**

**z_index<mark><sup>new</sup></mark>** `integer` | 图层叠放顺序

`data.z_index`

图层的叠放顺序索引。底图固定为 `0`，图层从 `1` 开始递增，数值越大越靠上层。

**模型支持** ：

- `Seedream 5.0 pro`

**name<mark><sup>new</sup></mark>** `string` | 图层名称

`data.name`

当前图层拆分元素的名称/标签。由模型根据拆分出的主体特征自动生成，用于标识该图层的主体内容。

**模型支持** ：

- `Seedream 5.0 pro`

**description<mark><sup>new</sup></mark>** `string` | 图层描述

`data.description`

当前图层拆分元素的详细描述。由模型生成的语义描述，提供比 `name` 更丰富的图层特征（如颜色、状态、材质等）。

**模型支持** ：

- `Seedream 5.0 pro`

**bounding_box<mark><sup>new</sup></mark>** `object` | 图层边界框

`data.bounding_box`

输出图层在输出底图坐标系中所占区域的边界框信息。仅图层返回该字段；底图覆盖整张画布，不返回该字段。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">使用说明</div>

- <div data-tips="true" data-tips-type="tip"><code>bounding_box</code> 表示图层在待拆分图中的相对位置与宽高比，等比缩放至输出底图坐标系后的坐标。</div>
  - <div data-tips="true" data-tips-type="tip">若需将图层还原到输出底图中，建议优先使用 <code>absolute</code> 坐标。</div>
  - <div data-tips="true" data-tips-type="tip">若需将图层还原到任意自定义的画布中，可使用 <code>normalized</code> 坐标。</div>

**模型支持** ：

- `Seedream 5.0 pro`

**absolute** `array` | 绝对坐标

`data.bounding_box.absolute`

输出图层边界框在输出底图坐标系中的绝对像素坐标（单位：像素），输出底图左上角坐标为 `(0, 0)`。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`：图层左边界到输出底图左边界的水平距离。

- `top`：图层上边界到输出底图上边界的垂直距离。

- `right`：图层右边界到输出底图左边界的水平距离。

- `bottom`：图层下边界到输出底图上边界的垂直距离。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">坐标示例</div>

<div data-tips="true" data-tips-type="tip">例如 <code>[225, 442, 796, 1414]</code> 表示图层左上角坐标为 <code>(225, 442)</code>，右下角坐标为 <code>(796, 1414)</code>。</div>

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">使用 bounding_box.absolute 还原图层</div>

<div data-tips="true" data-tips-type="warning">使用 <code>bounding_box.absolute</code> 可将输出图层还原到其在输出底图坐标系中的边界框区域。计算方式如下：</div>

- <div data-tips="true" data-tips-type="warning">图层在输出底图中的左上角坐标：<code>x = left</code>，<code>y = top</code>。</div>

- <div data-tips="true" data-tips-type="warning">图层在输出底图中的占用宽度：<code>w = right - left</code>。</div>

- <div data-tips="true" data-tips-type="warning">图层在输出底图中的占用高度：<code>h = bottom - top</code>。</div>

<div data-tips="true" data-tips-type="warning">将输出图层缩放至 <code>w × h</code>，再放置到输出底图的 <code>(x, y)</code> 位置。多个图层需要组合时，按 <code>z_index</code> 从小到大依次叠放。</div>

**normalized** `array` | 归一化坐标

`data.bounding_box.normalized`

图层边界框在输出底图坐标系中的归一化坐标，由绝对坐标按输出底图宽高换算至 `[0, 1000]` 的离散整数区间得到。格式为由 4 个整数构成的数组：`[left, top, right, bottom]`。

- `left`、`right`：图层左右边界在底图宽度方向上的相对位置。`0` 表示底图最左侧，`1000` 表示底图最右侧。

- `top`、`bottom`：图层上下边界在底图高度方向上的相对位置。`0` 表示底图最上方，`1000` 表示底图最下方。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">坐标示例</div>

<div data-tips="true" data-tips-type="tip">例如 <code>[220, 432, 777, 1000]</code> 表示：图层左边界距离底图左边界 22.0% 底图宽度，上边界距离底图上边界 43.2% 底图高度，右边界距离底图左边界 77.7% 底图宽度，下边界与底图下边缘对齐。</div>

<div data-tips="true" data-tips-type="warning" data-tips-is-title="true">使用 bounding_box.normalized 还原图层</div>

<div data-tips="true" data-tips-type="warning">使用 <code>bounding_box.normalized</code> 可将输出图层还原到目标画布中的边界框区域。设目标画布的宽高为 <code>W × H</code>：</div>

- <div data-tips="true" data-tips-type="warning">图层在目标画布中的左上角坐标：<code>x = left / 1000 × W</code>，<code>y = top / 1000 × H</code>。</div>

- <div data-tips="true" data-tips-type="warning">图层在目标画布中的占用宽度：<code>w = (right - left) / 1000 × W</code>。</div>

- <div data-tips="true" data-tips-type="warning">图层在目标画布中的占用高度：<code>h = (bottom - top) / 1000 × H</code>。</div>

<div data-tips="true" data-tips-type="warning">将输出图层缩放至 <code>w × h</code>，再放置到目标画布的 <code>(x, y)</code> 位置。归一化坐标为整数，换算后可能存在取整误差。</div>

**组图生成场景**

**error** `object` | 单图错误信息

`data.error`

某张图片生成失败时返回的错误信息。其他成功生成的图片不受影响

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">组图生成过程中某张图生成失败时：</div>

- <div data-tips="true" data-tips-type="tip">若失败原因为审核不通过：仍会继续请求下一个图片生成任务，即不影响同请求内其他图片的生成流程。</div>

- <div data-tips="true" data-tips-type="tip">若失败原因为内部服务异常（500）：不会继续请求下一个图片生成任务。</div>

**模型支持** ：

- `Seedream 5.0 lite`

- `Seedream 4.5`

- `Seedream 4.0`

**code** `string` | 错误码

`data.error.code`

错误码，请参见 [错误码](https://docs.volcengine.com/docs/82379/1299023)

**message** `string` | 错误消息

`data.error.message`

错误提示信息，便于排查问题

**error** `object` | 错误信息

本次请求顶层错误信息。当整个请求未能生成任何图片时返回

**code** `string` | 错误码

`error.code`

错误码，请参见 [错误码](https://docs.volcengine.com/docs/82379/1299023)

**message** `string` | 错误消息

`error.message`

错误提示信息，便于排查问题

**tools** `object[]` | 工具调用列表

本次请求中配置并被模型调用的工具列表

**模型支持** ：

- `Seedream 5.0 lite`

**type** `string` | 工具类型

`tools.type`

指定使用的工具类型。

- `web_search`：联网搜索功能。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

- <div data-tips="true" data-tips-type="tip">开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等），提升生成图片的时效性，但也会增加一定的时延。</div>

- <div data-tips="true" data-tips-type="tip">实际搜索次数可通过字段 <code>usage.tool_usage.web_search</code> 查询，如果为 0 表示未搜索。</div>

**usage** `object` | 用量信息

本次请求的用量信息，包括生成图片数量、消耗的 token 数量等

**generated_images** `integer` | 成功生成图片数

`usage.generated_images`

模型成功生成的图片张数，不包含生成失败的图片。仅对成功生成图片按张数进行计费

**input_images<mark><sup>new</sup></mark>** `integer` | 输入图片数

`usage.input_images`

输入模型的图片张数。

**模型支持** ：

- `Seedream 5.0 pro`

**output_tokens** `integer` | 输出 token 数

`usage.output_tokens`

模型生成的图片花费的 token 数量。计算逻辑为：`sum(图片长 * 图片宽) / 256` 后取整

**tool_usage** `object` | 工具用量

`usage.tool_usage`

使用工具的用量信息

**模型支持** ：

- `Seedream 5.0 lite`

**web_search** `integer` | 联网搜索次数

`usage.tool_usage.web_search`

调用联网搜索工具的次数，仅开启联网搜索时返回。如果为 0 表示未搜索

**total_tokens** `integer` | 总 token 数

`usage.total_tokens`

本次请求消耗的总 token 数量。当前不计算输入 token，故与 `output_tokens` 值一致

&nbsp;

<span id=".5rWB5byP6LCD55So5ZON5bqU"></span>

### 流式调用响应

字段结构详见 [图片生成流式响应事件](https://docs.volcengine.com/docs/82379/1824137)。
