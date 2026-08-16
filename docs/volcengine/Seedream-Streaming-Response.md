当请求时设置 `stream: true`，图片生成 API 会以 Server\-Sent Events（SSE）的形式实时推送每张图片的生成结果。本节内容介绍服务器会推送的各类事件。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

<div data-tips="true" data-tips-type="tip">流式输出的示例代码及返回示例，详见 <a href="https://www.volcengine.com/docs/82379/2123275">流式输出</a> 教程。</div>

<span id="error"></span>

## error

> 顶层错误事件 schema —— 当请求整体失败（如缺少必填参数、鉴权失败等）时返回。

**error** `object` | 错误信息

`error.error`

本次请求的错误对象，含错误码与可读信息。

**code** `string` | 错误码

`error.error.code`

错误码，请参见 [错误码](https://www.volcengine.com/docs/82379/1299023)

**message** `string` | 错误消息

`error.error.message`

错误提示信息，便于排查问题

error 响应示例

```JSON
"error": {
  "code":"BadRequest"，
  "message":"The request failed because it is missing one or multiple required parameters. Request ID: {id}"
}
```

&nbsp;

<span id="image-generation-completed"></span>

## image_generation.completed

> 流式响应结束事件 schema，携带本次请求汇总信息。

**created** `integer` | 事件时间

`image_generation.completed.created`

本次事件创建时间的 Unix 时间戳（秒）。

**model** `string` | 模型 ID

`image_generation.completed.model`

本次请求使用的模型 ID，格式为 `<模型名称>-<版本>`。

**type** `string` | 事件类型

`image_generation.completed.type`

事件类型，取值固定为 `image_generation.completed`。

**tools** `object[]` | 工具调用列表

`image_generation.completed.tools`

本次请求中配置并被模型调用的工具列表，仅在工具被实际调用时返回。

**type** `string` | 工具类型

`image_generation.completed.tools.type`

指定使用的工具类型。

- `web_search`：联网搜索功能。

<div data-tips="true" data-tips-type="tip" data-tips-is-title="true">说明</div>

- <div data-tips="true" data-tips-type="tip">开启联网搜索后，模型会根据用户的提示词自主判断是否搜索互联网内容（如商品、天气等），提升生成图片的时效性，但也会增加一定的时延。</div>

- <div data-tips="true" data-tips-type="tip">实际搜索次数可通过字段 <code>usage.tool_usage.web_search</code> 查询，如果为 0 表示未搜索。</div>

**usage** `object` | 用量信息

`image_generation.completed.usage`

本次请求的用量信息，包括生成图片数量、消耗的 token 数量等。

**generated_images** `integer` | 成功生成图片数

`image_generation.completed.usage.generated_images`

模型成功生成的图片张数，不包含生成失败的图片。仅对成功生成图片按张数进行计费

**input_images** `integer` | 输入图片数

`image_generation.completed.usage.input_images`

输入模型的图片张数。

**模型支持** ：

- `Seedream 5.0 pro`

**output_tokens** `integer` | 输出 token 数

`image_generation.completed.usage.output_tokens`

模型生成的图片花费的 token 数量。计算逻辑为：`sum(图片长 * 图片宽) / 256` 后取整

**tool_usage** `object` | 工具用量

`image_generation.completed.usage.tool_usage`

使用工具的用量信息

**模型支持** ：

- `Seedream 5.0 lite`

**web_search** `integer` | 联网搜索次数

`image_generation.completed.usage.tool_usage.web_search`

调用联网搜索工具的次数，仅开启联网搜索时返回。如果为 0 表示未搜索

**total_tokens** `integer` | 总 token 数

`image_generation.completed.usage.total_tokens`

本次请求消耗的总 token 数量。当前不计算输入 token，故与 `output_tokens` 值一致

image_generation.completed 响应示例

```JSON
{
  "type": "image_generation.completed",
  "model": "doubao-seedream-5-0-260128",
  "created": 1589478378,
  "tools": [
         {
             "type": "web_search",
         }
     ],
  "usage": {
      "generated_images": 2,
      "output_tokens": xx,
      "total_tokens": xx,
      "tool_usage":{
        "web_search":1
    }
  }
}
```

&nbsp;

<span id="image-generation-partial-failed"></span>

## image_generation.partial_failed

> 单张图片生成失败的流式事件 schema。

**created** `integer` | 事件时间

`image_generation.partial_failed.created`

本次事件创建时间的 Unix 时间戳（秒）。

**model** `string` | 模型 ID

`image_generation.partial_failed.model`

本次请求使用的模型 ID，格式为 `<模型名称>-<版本>`。

**type** `string` | 事件类型

`image_generation.partial_failed.type`

事件类型，取值固定为 `image_generation.partial_failed`。

**error** `object` | 错误信息

`image_generation.partial_failed.error`

该张图片生成失败的具体错误信息。

**code** `string` | 错误码

`image_generation.partial_failed.error.code`

错误码，请参见 [错误码](https://www.volcengine.com/docs/82379/1299023)

**message** `string` | 错误消息

`image_generation.partial_failed.error.message`

错误提示信息，便于排查问题

**image_index** `integer` | 图片序号

`image_generation.partial_failed.image_index`

失败图片在组图中的位置（从 0 开始计数）。

image_generation.partial_failed 响应示例

```JSON
{
  "type": "image_generation.partial_failed",
  "model": "doubao-seedream-5-0-260128",
  "created": 1589478378,
  "image_index": 2,
  "error": {
      "code":"OutputImageSensitiveContentDetected"，
      "message":"The request failed because the output image may contain sensitive information."
  }
}
```

&nbsp;

<span id="image-generation-partial-succeeded"></span>

## image_generation.partial_succeeded

> 单张图片生成成功的流式事件 schema。

**created** `integer` | 事件时间

`image_generation.partial_succeeded.created`

本次事件创建时间的 Unix 时间戳（秒）。

**model** `string` | 模型 ID

`image_generation.partial_succeeded.model`

本次请求使用的模型 ID，格式为 `<模型名称>-<版本>`。

**type** `string` | 事件类型

`image_generation.partial_succeeded.type`

事件类型，取值固定为 `image_generation.partial_succeeded`。

**b64_json** `string` | 图片 Base64 数据

`image_generation.partial_succeeded.b64_json`

该张图片的 Base64 编码字符串，仅当请求参数 `response_format=b64_json` 时返回。

**image_index** `integer` | 图片序号

`image_generation.partial_succeeded.image_index`

当前事件对应的图片在组图中的位置（从 0 开始计数）。

**size** `string` | 图像尺寸

`image_generation.partial_succeeded.size`

实际生成图像的尺寸，格式为 `<宽像素>x<高像素>`，例如 `2048x2048`。

**url** `string` | 图片 URL

`image_generation.partial_succeeded.url`

该张图片的下载链接，仅当请求参数 `response_format=url` 时返回。链接在生成后 **24 小时内有效** ，请及时下载或转存。

image_generation.partial_succeeded 响应示例

```JSON
{
  "type": "image_generation.partial_succeeded",
  "model": "doubao-seedream-5-0-260128",
  "created": 1589478378,
  "image_index": 0,
  "url": "https://...",
  "size": "2048×2048"
}
```
