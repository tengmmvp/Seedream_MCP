# Seedream MCP 工具 API 文档

## 概述

本文档详细描述了 Seedream MCP 工具提供的所有 API 接口和参数规范。

## 工具列表

### 1. seedream_text_to_image

**描述**: 使用 Seedream 根据文本提示词生成图像

**输入参数**:

| 参数名          | 类型    | 必需 | 默认值   | 描述                                        |
| --------------- | ------- | ---- | -------- | ------------------------------------------- |
| prompt          | string  | 是   | -        | 图像生成的文本提示词，建议不超过 600 个字符 |
| size            | string  | 否   | "1K"     | 生成图像的尺寸，可选值：1K、2K、4K          |
| watermark       | boolean | 否   | true     | 是否在生成的图像上添加水印                  |
| response_format | string  | 否   | "url"    | 响应格式，可选值：url、b64_json             |
| auto_save       | boolean | 否   | 全局配置 | 是否自动保存图片到本地                      |
| save_path       | string  | 否   | -        | 自定义保存路径，不指定则使用默认路径        |
| custom_name     | string  | 否   | -        | 自定义文件名前缀                            |

**输入 Schema**:

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "图像生成的文本提示词，建议不超过600个字符",
      "maxLength": 600
    },
    "size": {
      "type": "string",
      "description": "生成图像的尺寸",
      "enum": ["1K", "2K", "4K"],
      "default": "1K"
    },
    "watermark": {
      "type": "boolean",
      "description": "是否在生成的图像上添加水印",
      "default": true
    },
    "response_format": {
      "type": "string",
      "description": "响应格式：url返回图像URL，b64_json返回base64编码",
      "enum": ["url", "b64_json"],
      "default": "url"
    },
    "auto_save": {
      "type": "boolean",
      "description": "是否自动保存图片到本地，默认使用全局配置"
    },
    "save_path": {
      "type": "string",
      "description": "自定义保存路径，不指定则使用默认路径"
    },
    "custom_name": {
      "type": "string",
      "description": "自定义文件名前缀"
    }
  },
  "required": ["prompt"]
}
```

**输出格式**:

```text
✅ 文生图任务完成

📝 提示词: [用户输入的提示词]
📏 尺寸: [图像尺寸]

🖼️ 生成的图像:
  1. 图像URL: [图像链接]
  2. 本地路径: [本地文件路径] (如果启用自动保存)
  3. Markdown引用: ![图片描述](本地路径) (如果启用自动保存)

📊 使用统计:
  • 提示词令牌数: [数量]
  • 总令牌数: [数量]
  • 费用: [费用信息]
```

### 2. seedream_image_to_image

**描述**: 使用 Seedream 根据输入图像和文本提示词生成新图像

**输入参数**:

| 参数名          | 类型    | 必需 | 默认值   | 描述                                        |
| --------------- | ------- | ---- | -------- | ------------------------------------------- |
| prompt          | string  | 是   | -        | 图像生成的文本提示词，建议不超过 600 个字符 |
| image           | string  | 是   | -        | 输入图像的 URL 或本地文件路径               |
| size            | string  | 否   | "1K"     | 生成图像的尺寸，可选值：1K、2K、4K          |
| watermark       | boolean | 否   | true     | 是否在生成的图像上添加水印                  |
| response_format | string  | 否   | "url"    | 响应格式，可选值：url、b64_json             |
| auto_save       | boolean | 否   | 全局配置 | 是否自动保存图片到本地                      |
| save_path       | string  | 否   | -        | 自定义保存路径，不指定则使用默认路径        |
| custom_name     | string  | 否   | -        | 自定义文件名前缀                            |

**输入 Schema**:

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "图像生成的文本提示词，建议不超过600个字符",
      "maxLength": 600
    },
    "image": {
      "type": "string",
      "description": "输入图像的URL或本地文件路径"
    },
    "size": {
      "type": "string",
      "description": "生成图像的尺寸",
      "enum": ["1K", "2K", "4K"],
      "default": "1K"
    },
    "watermark": {
      "type": "boolean",
      "description": "是否在生成的图像上添加水印",
      "default": true
    },
    "response_format": {
      "type": "string",
      "description": "响应格式：url返回图像URL，b64_json返回base64编码",
      "enum": ["url", "b64_json"],
      "default": "url"
    }
  },
  "required": ["prompt", "image"]
}
```

**输出格式**:

```text
✅ 图生图任务完成

📝 提示词: [用户输入的提示词]
🖼️ 输入图像: [输入图像信息]
📏 尺寸: [图像尺寸]

🎨 生成的图像:
  1. 图像URL: [图像链接]

📊 使用统计:
  • 提示词令牌数: [数量]
  • 总令牌数: [数量]
  • 费用: [费用信息]
```

### 3. seedream_multi_image_fusion

**描述**: 使用 Seedream 将多张图像融合生成新图像

**输入参数**:

| 参数名          | 类型    | 必需 | 默认值   | 描述                                            |
| --------------- | ------- | ---- | -------- | ----------------------------------------------- |
| prompt          | string  | 是   | -        | 图像生成的文本提示词，建议不超过 600 个字符     |
| images          | array   | 是   | -        | 输入图像的 URL 或本地文件路径列表（2-5 张图像） |
| size            | string  | 否   | "1K"     | 生成图像的尺寸，可选值：1K、2K、4K              |
| watermark       | boolean | 否   | true     | 是否在生成的图像上添加水印                      |
| response_format | string  | 否   | "url"    | 响应格式，可选值：url、b64_json                 |
| auto_save       | boolean | 否   | 全局配置 | 是否自动保存图片到本地                          |
| save_path       | string  | 否   | -        | 自定义保存路径，不指定则使用默认路径            |
| custom_name     | string  | 否   | -        | 自定义文件名前缀                                |

**输入 Schema**:

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "图像生成的文本提示词，建议不超过600个字符",
      "maxLength": 600
    },
    "images": {
      "type": "array",
      "description": "输入图像的URL或本地文件路径列表（2-5张图像）",
      "items": {
        "type": "string",
        "description": "图像URL或文件路径"
      },
      "minItems": 2,
      "maxItems": 5
    },
    "size": {
      "type": "string",
      "description": "生成图像的尺寸",
      "enum": ["1K", "2K", "4K"],
      "default": "1K"
    },
    "watermark": {
      "type": "boolean",
      "description": "是否在生成的图像上添加水印",
      "default": true
    },
    "response_format": {
      "type": "string",
      "description": "响应格式：url返回图像URL，b64_json返回base64编码",
      "enum": ["url", "b64_json"],
      "default": "url"
    }
  },
  "required": ["prompt", "images"]
}
```

**输出格式**:

```text
✅ 多图融合任务完成

📝 提示词: [用户输入的提示词]
🖼️ 输入图像数量: [数量]张
📏 尺寸: [图像尺寸]

📥 输入图像:
  1. 文件: [文件名1]
  2. 文件: [文件名2]
  ...

🎨 融合生成的图像:
  1. 图像URL: [图像链接]

📊 使用统计:
  • 提示词令牌数: [数量]
  • 总令牌数: [数量]
  • 费用: [费用信息]

💡 融合说明:
  • 多图融合将输入的多张图像进行智能融合
  • 生成的图像会综合所有输入图像的特征
  • 文本提示词用于指导融合的风格和内容
```

### 4. seedream_sequential_generation

**描述**: 使用 Seedream 连续生成多张图像（组图生成）

**输入参数**:

| 参数名          | 类型    | 必需 | 默认值   | 描述                                        |
| --------------- | ------- | ---- | -------- | ------------------------------------------- |
| prompt          | string  | 是   | -        | 图像生成的文本提示词，建议不超过 600 个字符 |
| max_images      | integer | 否   | 4        | 最大生成图像数量（1-10）                    |
| size            | string  | 否   | "1K"     | 生成图像的尺寸，可选值：1K、2K、4K          |
| watermark       | boolean | 否   | true     | 是否在生成的图像上添加水印                  |
| response_format | string  | 否   | "url"    | 响应格式，可选值：url、b64_json             |
| auto_save       | boolean | 否   | 全局配置 | 是否自动保存图片到本地                      |
| save_path       | string  | 否   | -        | 自定义保存路径，不指定则使用默认路径        |
| custom_name     | string  | 否   | -        | 自定义文件名前缀                            |

**输入 Schema**:

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "图像生成的文本提示词，建议不超过600个字符",
      "maxLength": 600
    },
    "max_images": {
      "type": "integer",
      "description": "最大生成图像数量",
      "minimum": 1,
      "maximum": 10,
      "default": 4
    },
    "size": {
      "type": "string",
      "description": "生成图像的尺寸",
      "enum": ["1K", "2K", "4K"],
      "default": "1K"
    },
    "watermark": {
      "type": "boolean",
      "description": "是否在生成的图像上添加水印",
      "default": true
    },
    "response_format": {
      "type": "string",
      "description": "响应格式：url返回图像URL，b64_json返回base64编码",
      "enum": ["url", "b64_json"],
      "default": "url"
    }
  },
  "required": ["prompt"]
}
```

**输出格式**:

```text
✅ 组图生成任务完成

📝 提示词: [用户输入的提示词]
🔢 请求生成数量: [数量]张
📏 尺寸: [图像尺寸]

🎨 实际生成图像: [实际数量]张

📷 图像 1:
  • URL: [图像链接1]

📷 图像 2:
  • URL: [图像链接2]

...

📊 使用统计:
  • 提示词令牌数: [数量]
  • 总令牌数: [数量]
  • 费用: [费用信息]

💡 组图生成说明:
  • 组图生成会基于同一个提示词生成多张不同的图像
  • 每张图像都是独立生成的，会有不同的视觉效果
  • 适用于需要多个设计方案或创意选择的场景
  • 可以从生成的多张图像中选择最满意的结果
```

## 错误处理

### 错误响应格式

当工具调用失败时，会返回错误信息：

```text
[工具名称]生成失败: [错误详情]
```

### 常见错误类型

1. **参数验证错误**

   - 提示词为空或过长
   - 图像路径无效
   - 参数类型不正确

2. **API 调用错误**

   - API 密钥无效
   - 网络连接失败
   - 服务器响应错误

3. **文件处理错误**
   - 文件不存在
   - 文件格式不支持
   - 文件大小超限

### 错误示例

```text
文生图生成失败: 提示词不能为空
图生图生成失败: 文件不存在: /path/to/image.jpg
多图融合生成失败: 图像数量不能少于2张
组图生成失败: API调用超时
```

## 数据类型说明

### 图像尺寸 (size)

- `1K`: 1024x1024 像素，适合快速预览
- `2K`: 2048x2048 像素，平衡质量和速度
- `4K`: 4096x4096 像素，最高质量

### 响应格式 (response_format)

- `url`: 返回图像的 HTTP URL 链接
- `b64_json`: 返回 base64 编码的图像数据

### 图像输入格式

支持的图像格式：

- JPEG (.jpg, .jpeg)
- PNG (.png)
- GIF (.gif)
- BMP (.bmp)
- WebP (.webp)

支持的输入方式：

- HTTP/HTTPS URL
- 本地文件路径（绝对路径或相对路径）

## 使用限制

### 参数限制

- 提示词最大长度：600 字符
- 多图融合最大图像数：5 张
- 组图生成最大数量：10 张
- 单个图像文件最大大小：50MB

### 性能建议

- 建议单次请求不要超过 5 张图像
- 大尺寸图像生成时间较长，请耐心等待
- 避免频繁发起相同参数的请求

### 安全注意事项

- 不要在提示词中包含敏感信息
- 确保输入图像内容合规
- 妥善保管 API 密钥，避免泄露

## 自动保存功能

### 功能概述

自动保存功能解决了生成图片 URL 在 24 小时后过期的问题，提供永久可用的本地图片存储。

### 自动保存参数

所有图像生成工具都支持以下自动保存参数：

| 参数名      | 类型    | 必需 | 默认值   | 描述                                 |
| ----------- | ------- | ---- | -------- | ------------------------------------ |
| auto_save   | boolean | 否   | 全局配置 | 是否自动保存图片到本地               |
| save_path   | string  | 否   | -        | 自定义保存路径，不指定则使用默认路径 |
| custom_name | string  | 否   | -        | 自定义文件名前缀                     |

### 输出格式扩展

启用自动保存后，所有工具的输出都会包含额外的本地存储信息：

```text
🖼️ 生成的图像:
  1. 图像URL: [原始图像链接]
  2. 本地路径: [本地文件路径]
  3. Markdown引用: ![图片描述](本地路径)

💾 保存信息:
  • 保存状态: 成功
  • 文件大小: [文件大小]
  • 保存时间: [保存时间]
```

### 文件命名规则

自动保存的文件使用以下命名规则：

```text
[custom_name_]YYYYMMDD_HHMMSS_[hash]_[size].[ext]
```

示例：

- `landscape_20240115_143022_abc123_2K.png`
- `20240115_143045_def456_4K.jpg`

### 目录结构

```text
[base_dir]/
├── YYYY-MM-DD/
│   ├── text_to_image/
│   ├── image_to_image/
│   ├── multi_image_fusion/
│   └── sequential_generation/
└── ...
```

### 配置环境变量

| 环境变量                            | 描述                 | 默认值   |
| ----------------------------------- | -------------------- | -------- |
| SEEDREAM_AUTO_SAVE_ENABLED          | 全局启用自动保存     | false    |
| SEEDREAM_AUTO_SAVE_BASE_DIR         | 保存基础目录         | ./images |
| SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT | 下载超时时间（秒）   | 30       |
| SEEDREAM_AUTO_SAVE_MAX_RETRIES      | 最大重试次数         | 3        |
| SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE    | 最大文件大小（字节） | 52428800 |
| SEEDREAM_AUTO_SAVE_MAX_CONCURRENT   | 最大并发下载数       | 5        |
| SEEDREAM_AUTO_SAVE_DATE_FOLDER      | 是否创建日期文件夹   | true     |
| SEEDREAM_AUTO_SAVE_CLEANUP_DAYS     | 自动清理天数         | 30       |

### 错误处理机制

自动保存失败时，工具会：

1. 记录错误日志
2. 返回原始图像 URL 作为备选
3. 在输出中标明保存失败状态

```text
💾 保存信息:
  • 保存状态: 失败 - 网络超时
  • 备选方案: 使用原始URL
```
