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
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB.svg?style=for-the-badge" alt="Python"/>
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

# 可选：创建 .env（参考 .env.example）供 compose 只读挂载，替代下行环境变量前置

# 启动服务
ARK_API_KEY=your_api_key_here SEEDREAM_HTTP_AUTH_TOKEN=your_token_here docker compose up -d
```

服务以 streamable-http 传输监听容器内 `8000` 端口，宿主机端口由 `SEEDREAM_HTTP_PORT` 控制（默认 8000），MCP 端点路径为 `/mcp`。客户端接入配置（以 Claude Desktop 为例，其他支持 streamable-http 的客户端同理）：

```json
{
  "mcpServers": {
    "seedream-image-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

`<token>` 为占位符，须与服务端环境变量 `SEEDREAM_HTTP_AUTH_TOKEN` 一致；若经 TLS 反向代理或容器内 TLS 暴露，`url` 改用 `https://` 形态（如 `https://mcp.example.com/mcp`）。

## 🔧 客户端配置

> 推荐通过 `env` 注入 `ARK_API_KEY`，避免把密钥写进 `args`（命令行参数会出现在进程列表中，存在泄露风险）。

### Claude Desktop

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "seedream-image-mcp": {
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
    "seedream-image-mcp": {
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
    "seedream-image-mcp": {
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
--default-size [1K|1.5K|2K|3K|4K|<宽>x<高>]        # 图像尺寸 (默认: 2K，需与所选模型兼容)
--watermark                                        # 启用水印
--no-watermark                                     # 关闭水印

# 连接与传输
--base-url TEXT                                    # API 基础 URL（默认按配置或内置默认值；须 https，http 需设 SEEDREAM_ALLOW_HTTP_BASE_URL=true 豁免）
--transport [stdio|streamable-http]                # MCP 传输方式 (默认: stdio)
--host TEXT                                        # streamable-http 监听地址 (默认: 127.0.0.1；绑定非回环地址必须配置 --auth-token 与 TLS（或 --insecure-allow-non-tls 豁免），否则拒绝启动)
--port INTEGER                                     # streamable-http 监听端口 (默认: 8000)
--stateless                                        # streamable-http 无状态模式，适合远程多客户端与负载均衡 (默认关闭)

# 安全
--auth-token TEXT                                  # Bearer 鉴权令牌（非回环绑定必须配置，也可用 SEEDREAM_HTTP_AUTH_TOKEN）
--ssl-certfile TEXT                                # TLS 证书文件（非回环绑定必须配置，防令牌明文传输，启用后最低协议版本 TLS 1.2；受信反向代理终结 TLS 时可用 --insecure-allow-non-tls 豁免）
--ssl-keyfile TEXT                                 # TLS 私钥文件，与 --ssl-certfile 配合
--insecure-allow-non-tls                           # 显式允许非回环明文运行（仅受信反向代理终结 TLS 场景）

# 日志
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # 日志级别
```

> **安全提示**：`localhost` 不被视为回环地址（其解析依赖 hosts/DNS，可能被污染指向非回环），绑定它同样要求配置 Bearer 鉴权令牌与 TLS，未配置则服务拒绝启动；如需回环免鉴权语义，请改绑 `127.0.0.1` 或 `::1`。非回环绑定同样必须配置 Bearer 令牌与 TLS。生产与容器部署应通过环境变量（`ARK_API_KEY` / `SEEDREAM_HTTP_AUTH_TOKEN`）传递密钥，而非 CLI `--api-key` / `--auth-token`（命令行参数会暴露在进程列表与 shell 历史记录中）；多用户主机上 streamable-http 即使绑定回环地址，也建议配置鉴权令牌。

### 使用示例

```bash
# 基础使用
ARK_API_KEY=your_key uvx seedream-image-mcp

# 使用自定义配置文件
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# 切换其他模型（如 4.0 / 4.5）并指定尺寸与调试模式
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG

# 高精度生图（5.0 Pro；注意：不支持组图 / 联网搜索 / 流式输出，尺寸仅 1K/1.5K/2K）
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
| 图层拆分                   | ✅        | ❌           | ❌        | ❌           |
| 透明背景                   | ✅        | ❌           | ❌        | ❌           |
| 分辨率档位                 | 1K / 1.5K / 2K | 2K / 3K / 4K | 2K / 4K   | 1K / 2K / 4K |
| 自定义尺寸倍数             | 16 的倍数 | 不限制       | 不限制    | 不限制       |
| MCP 默认尺寸               | 2048x2048 | 2048x2048    | 2048x2048 | 2048x2048    |
| 参考图上限                 | 10 张     | 14 张        | 14 张     | 14 张        |

> **MCP 默认尺寸**：表中“MCP 默认尺寸”行为 MCP 统一配置 `default_size=2K`（对应 `2048x2048`）的运行时解析值，与各模型原生默认无关。

> **提示**：默认模型为 **doubao-seedream-5.0**（与 5.0 Lite 等价），开箱即用全部能力。切换到 `doubao-seedream-5.0-pro` 后，组图、联网搜索、流式输出不可用，尺寸仅支持 `1K/1.5K/2K`（默认 `2048x2048`），多图生图参考图上限降为 10 张，另独享图层拆分与透明背景能力。

## 🎨 功能特性

- **文生图**：文本生成图像
- **图文生图**：图像转换风格
- **多图融合**：融合多张图片
- **组图输出**：生成图像序列
- **图片浏览**：本地图片文件浏览

## 🛠️ 可用工具

### 响应契约：文本与 structuredContent 双通道

所有工具的 `tools/call` 结果同时携带两条通道：

- **`content`**：`TextContent` 文本摘要，面向模型可读，包含图片 URL/本地路径与自动保存结果等信息，模型可直接转述给用户。
- **`structuredContent`**：结构化数据，面向程序处理；字段集以各工具声明的 `outputSchema` 为准（客户端经 `tools/list` 自省，字段随版本演进以声明为准）。
- **`isError` 语义**：运行时失败时 `isError` 为 `true`，`content` 为面向用户的错误文案；成功时为 `false`。参数 schema 校验失败（类型错误、超长等）在协议层即被拒绝，仅返回 `isError=true` 与校验错误文本，不含 `structuredContent`。

> URL 形式的图片地址约 24 小时后过期；开启自动保存后，结果中的 `local_path` 字段提供本地持久化路径。

<details>
<summary><b>1. <code>seedream_text_to_image</code></b> — 文生图</summary>

根据文本提示词生成图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

基础调用：

```json
{
  "name": "seedream_text_to_image",
  "arguments": {
    "prompt": "水彩风格的江南水乡，清晨薄雾"
  }
}
```

可选参数组合（尺寸 + 水印 + 响应与输出格式 + 自动保存）：

```json
{
  "name": "seedream_text_to_image",
  "arguments": {
    "prompt": "水彩风格的江南水乡，清晨薄雾",
    "size": "2K",
    "watermark": false,
    "response_format": "url",
    "output_format": "jpeg",
    "auto_save": true,
    "custom_name": "jiangnan"
  }
}
```

</details>

<details>
<summary><b>2. <code>seedream_image_to_image</code></b> — 图文生图</summary>

根据输入图像和文本提示生成新图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (可选) - 图像修改要求或风格转换指令，建议不超过 300 个汉字或 600 个英文单词；仅图层拆分场景可缺省，由模型自动识别拆分意图
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (必需) - 输入图像的 URL 或本地文件路径
- `layer_decomposition` (可选) - 是否开启图层拆分，仅 5.0 Pro 支持；开启后将单张输入图拆解为 1 张底图与最多 16 个带透明通道的 PNG 图层，图层条目额外返回 `z_index`、`name`、`description`、`bounding_box` 字段；`output_format` 仅控制底图格式，图层始终为 PNG
- `background` (可选) - 透明通道，`transparent` 生成透明背景图（需输入单张带透明通道的图片，与 `output_format=jpeg` 互斥）或 `opaque` 生成常规图，仅 5.0 Pro 支持
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容；图层拆分场景仅支持档位与 `auto`（按输入图自适应，未指定尺寸时的默认值）
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

基础调用：

```json
{
  "name": "seedream_image_to_image",
  "arguments": {
    "prompt": "把这张人像照片转换为吉卜力动画风格",
    "image": ".seedream/images/2026/08/15/portrait.jpeg"
  }
}
```

可选参数组合（URL 参考图 + 尺寸 + 水印 + 响应与输出格式）：

```json
{
  "name": "seedream_image_to_image",
  "arguments": {
    "prompt": "把这张人像照片转换为吉卜力动画风格",
    "image": "https://example.com/portrait.jpeg",
    "size": "2048x2048",
    "watermark": false,
    "response_format": "url",
    "output_format": "png"
  }
}
```

</details>

<details>
<summary><b>3. <code>seedream_multi_image_fusion</code></b> — 多图融合</summary>

将多张图像融合生成新图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像融合要求或风格指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (必需) - 输入图像 URL 或本地文件路径列表（2-14 张；5.0 Pro 最多 10 张）
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

基础调用：

```json
{
  "name": "seedream_multi_image_fusion",
  "arguments": {
    "prompt": "把两张人像融合为一张双人合影，影棚灯光",
    "image": [
      ".seedream/images/2026/08/15/person_a.jpeg",
      ".seedream/images/2026/08/15/person_b.jpeg"
    ]
  }
}
```

可选参数组合（`image` 列表混用本地路径与 URL + 尺寸 + 水印 + 响应格式）：

```json
{
  "name": "seedream_multi_image_fusion",
  "arguments": {
    "prompt": "把产品图与品牌 Logo 融合为一张海报主视觉",
    "image": [
      ".seedream/images/product_front.png",
      ".seedream/images/product_side.png",
      "https://example.com/logo.png"
    ],
    "size": "2K",
    "watermark": true,
    "response_format": "url"
  }
}
```

</details>

<details>
<summary><b>4. <code>seedream_sequential_generation</code></b> — 组图输出</summary>

连续生成多张图像，支持文生组图、单图生组图、多图生组图（仅 doubao-seedream-5.0 系列（5.0/5.0-lite）/4.5/4.0 支持；5.0 Pro 不支持组图）。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，应明确指明生成数量和内容，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (可选) - 参考图像，支持单张图片（字符串）或多张图片（数组）；参考图最多 14 张，且参考图数量与 max_images 之和不超过 15
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `max_images` (可选) - 最大生成图像数量，范围 1-15，默认 15；提供参考图时默认自动扣减为 15 减参考图数量
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（5.0 Pro/5.0 Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

基础调用（文生组图，`max_images` 缺省为 15）：

```json
{
  "name": "seedream_sequential_generation",
  "arguments": {
    "prompt": "四格漫画：一只柴犬的一天，起床、吃饭、散步、睡觉"
  }
}
```

可选参数组合（参考图列表 + `max_images` + 尺寸 + 水印）：

```json
{
  "name": "seedream_sequential_generation",
  "arguments": {
    "prompt": "以参考图中的角色为主角，绘制三格探险漫画",
    "image": [
      ".seedream/images/2026/08/15/hero_front.jpeg",
      ".seedream/images/2026/08/15/hero_side.jpeg"
    ],
    "max_images": 3,
    "size": "2K",
    "watermark": false
  }
}
```

</details>

<details>
<summary><b>5. <code>seedream_browse_images</code></b> — 图片浏览</summary>

浏览工作区中的图片文件，获取文件路径用于图像生成。该工具只读、幂等、不访问网络。

**参数：**

- `directory` (可选) - 要浏览的目录路径，默认浏览工作区根目录（MCP Roots 授权的首个根；无 Roots 时回退 `SEEDREAM_WORKSPACE_ROOT` 配置的本地工作区根，均未设置时为进程当前工作目录）
- `recursive` (可选) - 是否递归搜索子目录，默认`true`
- `max_depth` (可选) - 最大搜索深度，范围 1-10，默认 3
- `limit` (可选) - 返回的最大文件数量，范围 1-200，默认 50
- `offset` (可选) - 分页偏移量（0-100000，从第几张开始返回），配合 `limit` 翻页，默认 0
- `format_filter` (可选) - 过滤特定图片格式，如`['.jpeg', '.png']`
- `show_details` (可选) - 是否显示详细文件信息，默认`false`

**调用示例：**

基础调用（无参数浏览工作区根目录）：

```json
{
  "name": "seedream_browse_images",
  "arguments": {}
}
```

可选参数组合（目录 + 递归 + 数量上限 + 格式过滤）：

```json
{
  "name": "seedream_browse_images",
  "arguments": {
    "directory": ".seedream/images",
    "recursive": true,
    "limit": 20,
    "format_filter": [".jpeg", ".png"]
  }
}
```

</details>

## 📦 可用资源

除工具外，服务端还暴露以下 MCP 资源供客户端读取运行时信息：

| 资源 URI | 说明 |
| --- | --- |
| `seedream://workspace/roots` | 客户端授权的 MCP 工作区 Roots；未授权时为空，避免暴露服务器本地目录 |
| `seedream://server/info` | 服务器名称、版本与当前生效配置摘要（模型、默认尺寸、自动保存开关，共五项字段） |
| `seedream://models/info` | 各模型别名与能力声明：支持的尺寸档位、像素范围、像素倍数、参考图上限、输出格式/工具/流式等能力，供客户端按需选择模型 |

## 🎭 风格预设

服务端内置以下 MCP 提示词模板，一键生成指定风格的文生图 prompt，可通过 `subject` 参数指定画面主题：

| Prompt 名称 | 风格 | 默认主题 |
| --- | --- | --- |
| `seedream_style_anime` | 日系动漫风格，赛璐珞上色，鲜艳饱和色彩 | 一个女孩站在樱花树下 |
| `seedream_style_realistic` | 写实摄影风格，高清细节，自然光影 | 城市夜景 |
| `seedream_style_watercolor` | 水彩画风格，柔和晕染，通透色彩 | 山间小屋 |
| `seedream_style_oil_painting` | 油画风格，厚重笔触，丰富层次 | 海边夕阳 |

## 📚 参考文档

仓库 `docs/` 目录收录以下参考资料：

| 文档 | 说明 |
| --- | --- |
| [Seedream-API-Reference.md](docs/volcengine/Seedream-API-Reference.md) | 火山引擎官方文稿：图像生成 API 参考 |
| [Seedream-Official-Tutorial.md](docs/volcengine/Seedream-Official-Tutorial.md) | 火山引擎官方文稿：官方教程 |
| [Seedream-Streaming-Response.md](docs/volcengine/Seedream-Streaming-Response.md) | 火山引擎官方文稿：流式响应（SSE 事件）说明 |
| [claude_desktop_config.json](docs/samples/claude_desktop_config.json) | 本项目示例：Claude Desktop 常用环境变量配置样本 |
| [pyguide.md](docs/development/pyguide.md) | 开发规范收录：Google Python Style Guide |

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
export SEEDREAM_HTTP_AUTH_TOKEN=your_token
docker compose up -d

# Windows
$env:ARK_API_KEY="your_key"
$env:SEEDREAM_HTTP_AUTH_TOKEN="your_token"
docker compose up -d
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

# API 端点安全
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # API 基础 URL，默认火山引擎北京端点；须 https，http 会使 API Key 明文传输而被默认拒绝，仅自建可信内网端点可经 SEEDREAM_ALLOW_HTTP_BASE_URL 豁免
SEEDREAM_ALLOW_HTTP_BASE_URL=false                      # 豁免 http:// 的 ARK_BASE_URL（默认拒绝明文传输；仅自建可信内网端点设 true）

# 模型配置
SEEDREAM_MODEL_ID=doubao-seedream-5.0

# 默认值
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# 超时
SEEDREAM_TIMEOUT=60                         # 连接建立/写入/连接池获取超时（秒）
SEEDREAM_API_TIMEOUT=600                    # API 调用读取与总超时（秒）
SEEDREAM_MAX_RETRIES=3                      # API 调用最大重试次数（429/5xx、超时与网络错误重试，4xx 不重试）

# 日志
LOG_LEVEL=INFO                              # 日志级别（DEBUG / INFO / WARNING / ERROR / CRITICAL）
LOG_FILE=                                   # 日志文件路径（默认 .seedream/logs/seedream_mcp.log，相对进程工作目录解析）

# 自动保存
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=                # 图片保存根目录（默认 <工作区根>/.seedream/images，工作区根取 MCP Roots 首项或 SEEDREAM_WORKSPACE_ROOT）
SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT=30      # 单张图片下载超时（秒）
SEEDREAM_AUTO_SAVE_MAX_RETRIES=3            # 下载失败最大重试次数（0 表示不重试）
SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE=52428800   # 单张图片大小上限（字节，默认 50MB）；另兼作流式单事件截断阈值与响应体读取上限的推导基准
SEEDREAM_RESPONSE_BODY_LIMIT=               # 上游响应体读取总量上限（字节；不设则按 SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE×20 推导，非流式/流式 JSON 与 SSE 共用）
SEEDREAM_AUTO_SAVE_MAX_CONCURRENT=5         # 最大并发下载数
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30
SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES=10737418240 # 保存目录总字节上限（默认 10GB；超限按最旧文件驱逐）

# 工作区与传输
SEEDREAM_WORKSPACE_ROOT=                    # 本地开发时文件读写边界回退目录（MCP Roots 优先）
SEEDREAM_HTTP_AUTH_TOKEN=                   # streamable-http Bearer 鉴权令牌（非回环绑定必须配置，否则拒绝启动；另需 TLS 或 --insecure-allow-non-tls 豁免）
SEEDREAM_HTTP_MAX_BODY_SIZE=67108864        # streamable-http 请求体上限（字节，≥1MB，默认 64MB；单图 data URI 约 40MB，兼顾多图融合）

# 客户端性能
SEEDREAM_IMAGE_PREPARE_CONCURRENCY=5
SEEDREAM_PREPARE_CACHE_MAX=32
SEEDREAM_PREPARE_CACHE_MAX_BYTES=268435456    # 参考图预处理缓存累计字节上限（默认 256MB）

# 流式处理
SEEDREAM_STREAM_BUFFER_MAX_SIZE=10485760      # SSE 流式响应缓冲区前缀回收阈值（默认 10MB）
SEEDREAM_STREAM_CHUNK_SIZE=1048576            # SSE 流式响应每次读取块大小（默认 1MB）
```

### 部署注意事项

- **保存目录归服务管理**：自动保存的按天清理与总量配额会删除保存目录内**所有**符合图片扩展名的过期文件与空目录，不区分是否由本服务生成。请勿将 `SEEDREAM_AUTO_SAVE_BASE_DIR` 指向个人相册等含重要图片的目录。
- **多租户 streamable-http 部署建议显式设置 `SEEDREAM_WORKSPACE_ROOT`**：MCP Roots 读取失败时文件访问边界会回退到该环境变量（未设置时为进程工作目录）。
- **未认证请求的体积限制**：未携带有效令牌的 chunked 请求不读 body 即返回 401，其体积限制依赖 uvicorn 层或前置反向代理；公网暴露部署请在代理层配置请求体上限。


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
