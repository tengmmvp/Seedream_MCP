<h1 align="center">Seedream Image MCP</h1>

<p align="center">
  <a href="./README.md">简体中文</a>
  ·
  <a href="./README.zh-TW.md">繁體中文</a>
  ·
  <a href="./README.en.md">English</a>
</p>

<div align="center">
  <img src="https://img.shields.io/github/v/release/tengmmvp/Seedream_MCP?display_name=tag&sort=semver&label=Release&style=for-the-badge&color=4C51BF" alt="Version"/>
  <img src="https://img.shields.io/pypi/v/seedream-image-mcp?label=PyPI&style=for-the-badge&color=F37720" alt="PyPI"/>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=for-the-badge" alt="Python"/>
  <img src="https://img.shields.io/badge/License-MIT-2DA44E.svg?style=for-the-badge" alt="License"/>
  <a href="https://zread.ai/tengmmvp/Seedream_MCP">
    <img src="https://img.shields.io/badge/Ask_Zread-_.svg?style=for-the-badge&color=00b0aa&labelColor=000000&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTQuOTYxNTYgMS42MDAxSDIuMjQxNTZDMS44ODgxIDEuNjAwMSAxLjYwMTU2IDEuODg2NjQgMS42MDE1NiAyLjI0MDFWNC45NjAxQzEuNjAxNTYgNS4zMTM1NiAxLjg4ODEgNS42MDAxIDIuMjQxNTYgNS42MDAxSDQuOTYxNTZDNS4zMTUwMiA1LjYwMDEgNS42MDE1NiA1LjMxMzU2IDUuNjAxNTYgNC45NjAxVjIuMjQwMUM1LjYwMTU2IDEuODg2NjQgNS4zMTUwMiAxLjYwMDEgNC45NjE1NiAxLjYwMDFaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00Ljk2MTU2IDEwLjM5OTlIMi4yNDE1NkMxLjg4ODEgMTAuMzk5OSAxLjYwMTU2IDEwLjY4NjQgMS42MDE1NiAxMS4wMzk5VjEzLjc1OTlDMS42MDE1NiAxNC4xMTM0IDEuODg4MSAxNC4zOTk5IDIuMjQxNTYgMTQuMzk5OUg0Ljk2MTU2QzUuMzE1MDIgMTQuMzk5OSA1LjYwMTU2IDE0LjExMzQgNS42MDE1NiAxMy43NTk5VjExLjAzOTlDNS42MDE1NiAxMC42ODY0IDUuMzE1MDIgMTAuMzk5OSA0Ljk2MTU2IDEwLjM5OTlaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik0xMy43NTg0IDEuNjAwMUgxMS4wMzg0QzEwLjY4NSAxLjYwMDEgMTAuMzk4NCAxLjg4NjY0IDEwLjM5ODQgMi4yNDAxVjQuOTYwMUMxMC4zOTg0IDUuMzEzNTYgMTAuNjg1IDUuNjAwMSAxMS4wMzg0IDUuNjAwMUgxMy43NTg0QzE0LjExMTkgNS42MDAxIDE0LjM5ODQgNS4zMTM1NiAxNC4zOTg0IDQuOTYwMVYyLjI0MDFDMTQuMzk4NCAxLjg4NjY0IDE0LjExMTkgMS42MDAxIDEzLjc1ODQgMS42MDAxWiIgZmlsbD0iI2ZmZiIvPgo8cGF0aCBkPSJNNCAxMkwxMiA0TDQgMTJaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00IDEyTDEyIDQiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8L3N2Zz4K&logoColor=ffffff" alt="Ask Zread"/>
  </a>
  <br><br>
  <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/doubao-seedream-5-0.jpeg" alt="Seedream MCP" width="500"/>
  <br><br>
  <b>基于火山引擎 Seedream 4.0、4.5 和 5.0 系列（含 5.0 Pro）API 的 MCP 工具，支持 AI 图像生成。</b>
</div>

---

<details>
<summary>本项目由 智谱 GLM Coding Plan 提供支持</summary>

<div align="center">
  <a href="https://www.bigmodel.cn/glm-coding?ic=GDEQEW52AC">
    <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/zhipu-glm-coding-plan.png" alt="Powered by 智谱 GLM Coding Plan · 智谱编码套餐" />
  </a>
</div>

</details>

---

## ⚡ 快速安装

### 1. 前置准备

安装 [uv](https://docs.astral.sh/uv/)（包含 `uvx` 命令）：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

在[火山引擎控制台](https://console.volcengine.com/)获取 API 密钥，通过环境变量 `ARK_API_KEY` 提供。

### 2. 一键启动

```bash
# 通过环境变量提供密钥（推荐）
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp

# 也可显式指定模型、尺寸等运行参数
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp --model doubao-seedream-5.0 --default-size 2K
```

`uvx` 自动从 [PyPI](https://pypi.org/project/seedream-image-mcp/) 拉取最新版本并在隔离环境运行——无需 clone 仓库、无需手动创建虚拟环境、无需安装依赖。

### 3. 可选：Docker Compose

```bash
# 下载 docker-compose.yml
curl -O https://raw.githubusercontent.com/tengmmvp/Seedream_MCP/main/docker-compose.yml

# 启动服务
ARK_API_KEY=your_api_key_here docker-compose up -d
```

## 🔧 客户端配置

> 推荐通过 `env` 注入 `ARK_API_KEY`，避免把密钥写进 `args`（命令行参数会出现在进程列表中，存在泄露风险）。

### Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "seedream": {
      "command": "uvx",
      "args": ["seedream-image-mcp"],
      "env": { "ARK_API_KEY": "your_api_key_here" }
    }
  }
}
```

<details>
<summary><b>其他客户端配置</b>（Claude Code · Cursor · Cline）</summary>

### Claude Code（命令行一键注册）

```bash
claude mcp add seedream --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
```

### Cursor

在项目根目录创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "seedream": {
      "command": "uvx",
      "args": ["seedream-image-mcp"],
      "env": { "ARK_API_KEY": "your_api_key_here" }
    }
  }
}
```

### Cline / 其他 stdio 客户端

通用配置（`command` + `args` + `env` 字段同上）。Cline 编辑 `cline_mcp_settings.json`：

```json
{
  "mcpServers": {
    "seedream": {
      "command": "uvx",
      "args": ["seedream-image-mcp"],
      "env": { "ARK_API_KEY": "your_api_key_here" }
    }
  }
}
```

</details>

> 需要指定模型/尺寸时，追加到 `args`，例如 `["seedream-image-mcp", "--model", "doubao-seedream-5.0"]`。

配置后重启对应客户端即可使用。

## ⚙️ 启动参数

```bash
# 认证与配置来源
--api-key TEXT                                     # API 密钥（可选，推荐用环境变量 ARK_API_KEY）
--config-file TEXT                                 # 自定义 .env 配置文件路径

# 模型与生成
--model [doubao-seedream-5.0-pro|doubao-seedream-5.0|doubao-seedream-5.0-lite|doubao-seedream-4.5|doubao-seedream-4.0]
                                                 # 模型选择 (默认: doubao-seedream-5.0)
--default-size [1K|2K|3K|4K|<宽>x<高>]            # 图像尺寸 (默认: 2K，需与所选模型兼容)
--watermark                                        # 启用水印
--no-watermark                                     # 关闭水印

# 连接与传输
--base-url TEXT                                    # API 基础 URL（默认按配置或内置默认值）
--transport [stdio|streamable-http]                # MCP 传输方式 (默认: stdio)
--host TEXT                                        # streamable-http 监听地址 (默认: 127.0.0.1；绑定非回环地址将触发安全告警)
--port INTEGER                                     # streamable-http 监听端口 (默认: 8000)
--stateless                                        # streamable-http 无状态模式，适合远程多客户端与负载均衡 (默认关闭)

# 安全
--auth-token TEXT                                  # Bearer 鉴权令牌（非回环绑定必须配置，也可用 SEEDREAM_HTTP_AUTH_TOKEN）
--ssl-certfile TEXT                                # TLS 证书文件（非回环绑定必须配置，防令牌明文传输；受信反向代理终结 TLS 时可用 --insecure-allow-non-tls 豁免）
--ssl-keyfile TEXT                                 # TLS 私钥文件，与 --ssl-certfile 配合
--insecure-allow-non-tls                           # 显式允许非回环明文运行（仅受信反向代理终结 TLS 场景）

# 日志
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # 日志级别
```

> **安全提示**：绑定 `localhost` 时服务将其视为回环地址，不强制 Bearer 鉴权与 TLS。部署方应确认 `localhost` 解析到 `127.0.0.1` 或 `::1`，容器与虚拟环境若修改 hosts 需特别注意；非回环绑定必须配置 Bearer 令牌与 TLS。

### 使用示例

```bash
# 基础使用
ARK_API_KEY=your_key uvx seedream-image-mcp

# 使用自定义配置文件
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# 切换其他模型（如 4.0 / 4.5）并指定尺寸与调试模式
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG

# 高精度生图（5.0 Pro；注意：不支持组图 / 联网搜索 / 流式输出，尺寸仅 1K/2K）
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-5.0-pro
```

## 📐 模型能力差异

各模型支持的能力与参数范围不同，选择模型时请留意：

| 能力 / 参数                | 5.0 Pro   | 5.0 / 5.0 Lite | 4.5       | 4.0          |
| -------------------------- | --------- | ------------ | --------- | ------------ |
| 文生图 / 图生图 / 多图生图 | ✅        | ✅           | ✅        | ✅           |
| 组图生成                   | ❌        | ✅           | ✅        | ✅           |
| 联网搜索                   | ❌        | ✅           | ❌        | ❌           |
| 流式输出                   | ❌        | ✅           | ✅        | ✅           |
| 输出格式（png/jpeg）       | ✅        | ✅           | ❌        | ❌           |
| 分辨率档位                 | 1K / 2K   | 2K / 3K / 4K | 2K / 4K   | 1K / 2K / 4K |
| 默认尺寸                   | 2048x2048 | 2048x2048    | 2048x2048 | 2048x2048    |
| 参考图上限                 | 10 张     | 14 张        | 14 张     | 14 张        |

> **提示**：默认模型为 **doubao-seedream-5.0**（与 5.0 Lite 等价），开箱即用全部能力。切换到 `doubao-seedream-5.0-pro` 后，组图、联网搜索、流式输出不可用，尺寸仅支持 `1K/2K`（默认 `2048x2048`），多图生图参考图上限降为 10 张。

## 🎨 功能特性

- **文生图**：文本生成图像
- **图文生图**：图像转换风格
- **多图融合**：融合多张图片
- **组图输出**：生成图像序列
- **图片浏览**：本地图片文件浏览

## 🛠️ 可用工具

<details>
<summary><b>1. <code>seedream_text_to_image</code></b> — 文生图</summary>

根据文本提示词生成图像

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 4.0 支持
- `size` (可选) - 图像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

</details>

<details>
<summary><b>2. <code>seedream_image_to_image</code></b> — 图文生图</summary>

根据输入图像和文本提示生成新图像

**参数：**

- `prompt` (必需) - 图像修改要求或风格转换指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 4.0 支持
- `image` (必需) - 输入图像的 URL 或本地文件路径
- `size` (可选) - 图像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

</details>

<details>
<summary><b>3. <code>seedream_multi_image_fusion</code></b> — 多图融合</summary>

将多张图像融合生成新图像

**参数：**

- `prompt` (必需) - 图像融合要求或风格指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 4.0 支持
- `image` (必需) - 输入图像 URL 或本地文件路径列表（2-14 张；5.0 Pro 最多 10 张）
- `size` (可选) - 图像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

</details>

<details>
<summary><b>4. <code>seedream_sequential_generation</code></b> — 组图输出</summary>

连续生成多张图像，支持文生组图、单图生组图、多图生组图（仅 5.0 Lite/4.5/4.0 支持；5.0 Pro 不支持组图）

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，应明确指明生成数量和内容，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 4.0 支持
- `image` (可选) - 参考图像，支持单张图片（字符串）或多张图片（数组）；参考图最多 14 张，且参考图数量与 max_images 之和不超过 15
- `size` (可选) - 图像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `max_images` (可选) - 最大生成图像数量，范围 1-15，默认 15
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

</details>

<details>
<summary><b>5. <code>seedream_browse_images</code></b> — 图片浏览</summary>

浏览工作区中的图片文件，获取文件路径用于图像生成

**参数：**

- `directory` (可选) - 要浏览的目录路径，默认当前目录
- `recursive` (可选) - 是否递归搜索子目录，默认`true`
- `max_depth` (可选) - 最大搜索深度，范围 1-10，默认 3
- `limit` (可选) - 返回的最大文件数量，范围 1-200，默认 50
- `offset` (可选) - 分页偏移量（0-100000，从第几张开始返回），配合 `limit` 翻页，默认 0
- `format_filter` (可选) - 过滤特定图片格式，如`['.jpeg', '.png']`
- `show_details` (可选) - 是否显示详细文件信息，默认`false`

</details>

## 📦 可用资源

除工具外，服务端还暴露以下 MCP 资源供客户端读取运行时信息：

| 资源 URI | 说明 |
| --- | --- |
| `seedream://workspace/roots` | 客户端授权的 MCP 工作区 Roots；未授权时为空，避免暴露服务器本地目录 |
| `seedream://server/info` | 服务器名称、版本与当前生效配置摘要（模型、默认尺寸、自动保存开关等） |
| `seedream://models/info` | 各模型别名与能力声明：支持的尺寸档位、像素范围、像素倍数、参考图上限、输出格式/工具/流式等能力，供客户端按需选择模型 |

## 🎭 风格预设

服务端内置以下 MCP 提示词模板，一键生成指定风格的文生图 prompt，可通过 `subject` 参数指定画面主题：

| Prompt 名称 | 风格 | 默认主题 |
| --- | --- | --- |
| `seedream_style_anime` | 日系动漫风格，赛璐珞上色，鲜艳饱和色彩 | 一个女孩站在樱花树下 |
| `seedream_style_realistic` | 写实摄影风格，高清细节，自然光影 | 城市夜景 |
| `seedream_style_watercolor` | 水彩画风格，柔和晕染，通透色彩 | 山间小屋 |
| `seedream_style_oil_painting` | 油画风格，厚重笔触，丰富层次 | 海边夕阳 |

## ❓ 常见问题

**Q: uvx 命令不存在？**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Q: 如何获取 API 密钥？**
访问 [火山引擎控制台](https://console.volcengine.com/) 创建密钥

**Q: Docker 服务无法启动？**
确保设置了环境变量：

```bash
# Linux/macOS
export ARK_API_KEY=your_key
docker-compose up -d

# Windows
$env:ARK_API_KEY="your_key"
docker-compose up -d
```

## 🧪 本地开发

```bash
# 克隆仓库
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP

# 安装依赖（开发模式）
uv sync

# 创建 .env 文件
cp .env.example .env
# 编辑 .env 文件，添加您的 API 密钥

# 启动服务
uv run python -m seedream_mcp.server

# 或直接使用 API 密钥启动
uv run python -m seedream_mcp.server --api-key your_key
```

## ⚙️ 环境变量配置

主要配置项（详见 `.env.example`）：

配置优先级：MCP 客户端显式配置（命令行参数） > 运行时系统环境变量 > `.env` 文件 > 默认值。

`.env` 加载规则：

- 使用 `--config-file` 时：仅加载指定文件。
- 未指定 `--config-file` 时：按“项目根 `.env` -> 当前工作目录 `.env`”顺序合并，后者覆盖前者。
- `.env` 的值**不会注入**进程环境变量，仅按上述优先级解析后写入配置对象，避免污染全局状态；系统环境变量优先于 `.env` 文件。

```bash
# 必需配置
ARK_API_KEY=your_api_key_here

# 模型配置
SEEDREAM_MODEL_ID=doubao-seedream-5.0

# 默认值
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# 自动保存
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=./seedream_images
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30

# 客户端性能
SEEDREAM_IMAGE_PREPARE_CONCURRENCY=5
SEEDREAM_PREPARE_CACHE_MAX=32
```

## 👥 贡献者

### 项目维护者

- **[@tengmmvp](https://github.com/tengmmvp)** - 项目维护者

### 重要贡献者

- **[@caoergou](https://github.com/caoergou)** - 通过 [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2) 贡献了 uvx 支持、Docker 容器化配置、GitHub Actions 自动化发布流程，极大简化了项目的安装与部署体验

### 参与贡献

欢迎提交 Issue 和 Pull Request！请查看 [GitHub Issues](https://github.com/tengmmvp/Seedream_MCP/issues) 了解当前的讨论和需求。

<div align="center"><b>🌟 如果您希望参与开发，请先在 Issues 中讨论您的想法！</b></div>

## 📄 许可证

这个项目基于 MIT 许可证开源。更多信息请查看 [LICENSE](LICENSE) 文件。
