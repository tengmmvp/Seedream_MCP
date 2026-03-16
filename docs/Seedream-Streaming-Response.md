Doubao\-seedream\-5.0\-lite/4.5/4.0 支持流式输出模式。当您调用图片生成API 并将 **stream** 设置为 `true` 时，服务器会在生成响应的过程中，通过 Server\-Sent Events（SSE）实时向客户端推送事件。本节内容介绍服务器会推送的各类事件。
<span id="ScH1WJFo"></span>

## image_generation.partial_succeeded

> 当前仅 doubao\-seedream\-5.0\-lite/4.5/4.0 支持流式响应。

在流式响应模式下，当任意图片生成成功时返回该事件。

---

<span id="WlFg0rZV"></span>

### 参数说明

**type** `string`
此处应为` image_generation.partial_succeeded`。

---

**model** `string`
本次请求使用的模型 ID ，格式为`<模型名称>-<版本>`。

---

**created** `integer`
本次请求创建时间的 Unix 时间戳（秒）。

---

**image_index** `integer`
本次生图请求中，本次事件对应图片在请求中的序号。
从 `0`开始累加，不管生图是否成功，即在` image_generation.partial_succeeded`、`image_generation.partial_failed` 事件，均会自动累加 1。

---

**url ** `string`
本次事件对应图片的下载 URL。当请求中配置字段 **response_format** 为 `url` 时返回。

---

**b64_json ** `string`
本次事件对应图片的 Base64 编码。当请求中配置字段 **response_format** 为 `b64_json` 时返回。

---

**size** `string`
图像的宽高像素值，格式`<宽像素>×<高像素>`，如 `2048×2048`。

---

<span id="NavZ7gku"></span>

### 返回示例

```Shell
{
  "type": "image_generation.partial_succeeded",
  "model": "doubao-seedream-5-0-260128",
  "created": 1589478378,
  "image_index": 0,
  "url": "https://...",
  "size": "2048×2048"
}
```

---

<span id="DvFWgMPz"></span>

## image_generation.partial_failed

> 当前仅 doubao\-seedream\-5.0\-lite/4.5/4.0 支持流式响应。

在流式返回模式下，当任意图片生成失败时返回该事件。

- 若失败原因为审核不通过：仍会继续请求下一个图片生成任务，即不影响同请求内其他图片的生成流程。
- 若失败原因为内部服务异常（500）：不会继续请求下一个图片生成任务。

---

<span id="ECrzr71c"></span>

### 参数说明

**type** `string`
此处应为 `image_generation.partial_failed`。

---

**model** `string`
本次请求使用的模型 ID ，格式为`<模型名称>-<版本>`。

---

**created** `integer`
本次请求创建时间的 Unix 时间戳（秒）。

---

**image_index** `integer`
本次生图请求中，本次事件对应图片在请求中的序号。
从 `0`开始累加，不管图片是否生成成功，即在`image_generation.partial_succeeded`、`image_generation.partial_failed` 事件，均会自动累加 1。

---

**error** `object`
本次生图请求中，本次事件对应的错误原因。

属性

---

error.**code** `string`
请参见[错误码](https://www.volcengine.com/docs/82379/1299023)。

---

error.**message** `string`
错误提示信息

<span id="UZPzLDle"></span>

###

<span id="UZPzLDle"></span>

### 返回示例

```Shell
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

---

<span id="2EAlVxN9"></span>

## image_generation.completed

> 当前仅 doubao\-seedream\-5.0\-lite/4.5/4.0 支持流式响应。

请求的所有图片（无论成功或失败）均处理完毕后返回，是该流式返回的最后一个响应事件。

---

<span id="jTlFAfRr"></span>

### 参数说明

**type** `string`
此处应为 `image_generation.completed`。

---

**model** `string`
本次请求使用的模型 ID ，格式为`<模型名称>-<版本>`。

---

**created** `integer`
本次请求创建时间的 Unix 时间戳（秒）。

---

**tools** `array of object`
本次请求，配置的模型调用工具。

属性

---

tools.**type ** `string`
配置的调用工具类型。

- web_search：联网搜索工具。

---

**usage** `object`
本次请求的用量信息。

属性

---

usage.**generated_images ** `integer`
模型成功生成的图片张数，不包含生成失败的图片。
仅对成功生成图片按张数进行计费。

---

usage.**output_tokens** `integer`
模型生成的图片花费的 token 数量。
计算逻辑为：计算`sum(图片长*图片宽)/256` ，然后取整。

---

usage.**total_tokens** `integer`
本次请求消耗的总 token 数量。
当前不计算输入 token，故与 **output_tokens** 值一致。

---

usage.**tool_usage ** `object`
使用工具的用量信息。

属性

---

usage.tool_usage.**web_search ** `integer`
调用联网搜索工具次数，仅开启联网搜索时返回。

&nbsp;
<span id="2c1Ftf57"></span>

### 返回示例

```Shell
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

---

<span id="9nq19QPQ"></span>

## **error**

> 本次请求如发生错误，对应的错误信息。

---

<span id="1C2zU5ht"></span>

### 参数说明

**error ** `object`
本次请求错误，返回的错误信息。

属性

---

error.**code** `string`
请参见[错误码](https://www.volcengine.com/docs/82379/1299023)。

---

error.**message** `string`
错误提示信息。

&nbsp;
<span id="gNZSpgbA"></span>

### 返回示例

```Shell
"error": {
  "code":"BadRequest"，
  "message":"The request failed because it is missing one or multiple required parameters. Request ID: {id}"
}
```

&nbsp;
