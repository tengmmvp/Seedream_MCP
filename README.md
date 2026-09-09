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
  <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/doubao-seedream-5-0-pro.jpeg" alt="Seedream MCP" width="670"/>
  <br><br>
  <b>基于火山引擎 Seedream API 的 AI 图像生成 MCP 工具。</b>
</div>

---

<details>
<summary>本项目由 智谱 GLM Coding Plan 提供支持</summary>

<div align="center">
  <a href="https://www.bigmodel.cn/invite?icode=DGfqlMKwV%2BAThYQ7VC85PnHEaazDlIZGj9HxftzTbt4%3D">
    <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/zhipu-glm-coding-plan-26-9-9.png" alt="Powered by 智谱 GLM Coding Plan · 智谱编码套餐" />
  </a>
</div>

</details>

---

## 📑 目录

<table align="center">
  <tr>
    <td><a href="#-快速安装">⚡ 快速安装</a></td>
    <td><a href="#-客户端配置">🔧 客户端配置</a></td>
    <td><a href="#️-web-操作台">🖥️ Web 操作台</a></td>
    <td><a href="#️-启动参数">⚙️ 启动参数</a></td>
  </tr>
  <tr>
    <td><a href="#-模型能力差异">📐 模型能力差异</a></td>
    <td><a href="#️-可用工具">🛠️ 可用工具</a></td>
    <td><a href="#-可用资源">📦 可用资源</a></td>
    <td><a href="#-agent-skills">🧠 Agent Skills</a></td>
  </tr>
  <tr>
    <td><a href="#-风格预设">🎭 风格预设</a></td>
    <td><a href="#-常见问题">❓ 常见问题</a></td>
    <td><a href="#-本地开发">🧪 本地开发</a></td>
    <td><a href="#️-环境变量配置">⚙️ 环境变量配置</a></td>
  </tr>
  <tr>
    <td><a href="#-贡献者">👥 贡献者</a></td>
    <td><a href="#-许可证">📄 许可证</a></td>
  </tr>
</table>

## ⚡ 快速安装

### 1. 前置准备

安装 [uv](https://docs.astral.sh/uv/)，安装后即可直接使用 `uvx` 命令：

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

# 可选：参照 .env.example 创建 .env 供 compose 只读挂载，免去下行命令的环境变量前置
# 未创建 .env 时 Docker 会自动建出同名目录充当挂载源导致挂载异常，请先 touch .env 或移除 compose 中的挂载行

# 启动服务
ARK_API_KEY=your_api_key_here SEEDREAM_HTTP_AUTH_TOKEN=your_token_here docker compose up -d
```

服务以 streamable-http 传输监听容器内 `8000` 端口，MCP 端点路径为 `/mcp`；宿主机映射端口由 `SEEDREAM_HTTP_PORT` 控制，默认 8000。端口映射默认仅绑定回环地址 `127.0.0.1`，需从其他设备直连时，把 docker-compose.yml 改为 `0.0.0.0:${SEEDREAM_HTTP_PORT:-8000}:8000` 或指定宿主机网卡地址。映射一旦改为 `0.0.0.0`，服务即暴露给网络，`SEEDREAM_HTTP_AUTH_TOKEN` 会以明文 HTTP 在网络上传输；此时必须将服务置于 TLS 反向代理之后，或经 `SEEDREAM_EXTRA_CLI_ARGS` 向容器提供 TLS 证书参数，无 TLS 禁止对外暴露。

客户端接入配置以 Claude Desktop 为例，其他支持 streamable-http 的客户端同理：

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

`<token>` 为占位符，须与服务端环境变量 `SEEDREAM_HTTP_AUTH_TOKEN` 一致；若经 TLS 反向代理或容器内 TLS 暴露，`url` 改用 `https://` 形态（如 `https://mcp.example.com/mcp`）。静态令牌鉴权不提供 OAuth 受保护资源元数据发现，标准 OAuth 客户端需手动配置凭据。

## 🔧 客户端配置

> 推荐通过 `env` 注入 `ARK_API_KEY`，避免把密钥写进 `args`：命令行参数会出现在进程列表中，存在泄露风险。

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

### Claude Code

一条命令完成注册：

```bash
claude mcp add seedream-image-mcp --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
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

## 🖥️ Web 操作台

不使用 MCP 客户端的用户也可以直接通过网页使用：以 `--web` 旗标（或环境变量 `SEEDREAM_WEB_ENABLED=true`）启动 streamable-http 传输后，浏览器访问 `http://127.0.0.1:8000/web` 即可打开操作台，覆盖文生图、图生图、多图融合、组图生成与历史图库。默认关闭，stdio 传输与未开启时不暴露任何 Web 端点。

```bash
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp --transport streamable-http --web --auth-token your_token_here
```

鉴权方式：网页本身无需令牌即可打开；网页的功能接口在部署配置了令牌时需要验证——在页面中输入后令牌仅存于当前标签页会话，关闭标签页即失效，下次使用需重新输入，不会出现在网址中。仅本机使用且未配置令牌时，全程无需输入任何东西，接口也只接受来自本页面与本机程序的请求。

## ⚙️ 启动参数

```bash
# 配置来源
--config-file TEXT                                 # .env 配置文件路径；指定后不再读取项目根与当前目录的 .env

# 必需配置
--api-key TEXT                                     # API 密钥（推荐用环境变量 ARK_API_KEY；命令行传入会留在进程列表与 shell 历史中）

# 模型与端点
--model [doubao-seedream-5.0-pro|doubao-seedream-5.0|doubao-seedream-5.0-lite|doubao-seedream-4.5|doubao-seedream-4.0]
                                                 # 模型选择；完整 Model ID 或 Endpoint ID 经 SEEDREAM_MODEL_ID 传入 (默认: doubao-seedream-5.0)
--default-size [1K|1.5K|2K|3K|4K|<宽>x<高>]        # 默认生成尺寸，需与所选模型兼容 (默认: 2K)
--watermark                                        # 启用水印
--no-watermark                                     # 关闭水印
--base-url TEXT                                    # 模型 API 端点 URL（须 https，http 需设 SEEDREAM_ALLOW_HTTP_BASE_URL=true 豁免）

# 日志
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # 日志级别 (默认: INFO)

# 传输与 Web
--transport [stdio|streamable-http]                # MCP 传输方式 (默认: stdio)
--host TEXT                                        # streamable-http 监听地址 (默认: 127.0.0.1；绑定非回环地址必须配置鉴权令牌与 TLS，否则拒绝启动)
--port INTEGER                                     # streamable-http 监听端口 (默认: 8000，范围 1-65535)
--auth-token TEXT                                  # Bearer 鉴权令牌 (非回环绑定必须配置；推荐用 SEEDREAM_HTTP_AUTH_TOKEN)
--ssl-certfile TEXT                                # TLS 证书文件 (非回环绑定必须配置，与 --ssl-keyfile 成对)
--ssl-keyfile TEXT                                 # TLS 私钥文件 (与 --ssl-certfile 成对)
--insecure-allow-non-tls                           # 允许非回环明文运行 (仅受信反向代理终结 TLS 场景)
--stateless                                        # 无状态模式，仅影响带握手会话的旧规范修订客户端，代价是失去反向通道 (默认关闭)
--web                                              # 开启 Web 操作台，浏览器访问 /web 直接使用 (默认关闭；未传入时按 SEEDREAM_WEB_ENABLED 解析)
--no-web                                           # 关闭 Web 操作台，覆盖 SEEDREAM_WEB_ENABLED 的开启设置
--version                                          # 打印版本号并退出
```

> **安全提示**：`localhost` 不被视为回环地址（其解析依赖 hosts/DNS，可能被污染指向非回环地址），须按非回环地址要求配置 Bearer 鉴权令牌与 TLS，未配置则拒绝启动；如需免鉴权使用回环地址，请改绑 `127.0.0.1` 或 `::1`。非回环绑定默认按该地址校验 Host 与 Origin 头以防 DNS rebinding；通配绑定（`0.0.0.0`/`::`）无法预知访问地址，校验默认关闭，需配置 `SEEDREAM_HTTP_ALLOWED_HOSTS` 启用。生产与容器部署的密钥应经环境变量（`ARK_API_KEY` / `SEEDREAM_HTTP_AUTH_TOKEN`）传递，而非 CLI `--api-key` / `--auth-token`——命令行参数会留在进程列表与 shell 历史记录中；多用户主机上 streamable-http 即使绑定回环地址，也建议配置鉴权令牌。Web 操作台不改变上述传输层安全要求：开启后新增的 API 面全部强制令牌，免鉴权的仅限无数据的静态页面骨架。

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

<table align="center">
  <tr>
    <th style="text-align: center">能力 / 参数</th>
    <th style="text-align: center">5.0 Pro</th>
    <th style="text-align: center">5.0 / 5.0 Lite</th>
    <th style="text-align: center">4.5</th>
    <th style="text-align: center">4.0</th>
  </tr>
  <tr>
    <td>文生图 / 图生图 / 多图生图</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
  </tr>
  <tr>
    <td>组图生成</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
  </tr>
  <tr>
    <td>联网搜索</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
  </tr>
  <tr>
    <td>流式输出</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
  </tr>
  <tr>
    <td>输出格式（png/jpeg）</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
  </tr>
  <tr>
    <td>图层拆分</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
  </tr>
  <tr>
    <td>透明背景</td>
    <td style="text-align: center">✅</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
    <td style="text-align: center">❌</td>
  </tr>
  <tr>
    <td>分辨率档位</td>
    <td style="text-align: center">1K / 1.5K / 2K</td>
    <td style="text-align: center">2K / 3K / 4K</td>
    <td style="text-align: center">2K / 4K</td>
    <td style="text-align: center">1K / 2K / 4K</td>
  </tr>
  <tr>
    <td>参考图上限</td>
    <td style="text-align: center">10 张</td>
    <td style="text-align: center">14 张</td>
    <td style="text-align: center">14 张</td>
    <td style="text-align: center">14 张</td>
  </tr>
</table>

> **提示**：默认模型为 **doubao-seedream-5.0**（与 5.0 Lite 等价），开箱即用全部能力。切换到 `doubao-seedream-5.0-pro` 后，组图、联网搜索、流式输出不可用，尺寸仅支持 `1K/1.5K/2K`，默认档位 `2K`，多图生图参考图上限降为 10 张，另独享图层拆分与透明背景能力。

## 🛠️ 可用工具

<details>
<summary><b>1. <code>text_to_image</code></b> — 文生图</summary>

根据文本提示词生成图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（Pro/标准/Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 同一提示并行发起的独立生成次数，每次各产出一张图，范围 1-10，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-10，默认 `min(request_count, 10)`，一般无需手动指定
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

```json
{
  "name": "text_to_image",
  "arguments": {
    "prompt": "水彩风格的江南水乡，清晨薄雾"
  }
}
```

</details>

<details>
<summary><b>2. <code>image_to_image</code></b> — 图文生图</summary>

根据输入图像和文本提示生成新图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (可选) - 图像修改要求或风格转换指令，建议不超过 300 个汉字或 600 个英文单词；仅图层拆分场景可缺省，由模型自动识别拆分意图
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (必需) - 输入图像，支持图像 URL、本地文件路径或 Base64 图片数据；本地文件路径须在读取范围内，其中相对路径仅限图片保存目录内
- `layer_decomposition` (可选) - 是否开启图层拆分，仅 5.0 Pro 支持；开启后将单张输入图拆解为 1 张底图与最多 16 个带透明通道的 PNG 图层，图层条目额外返回 `z_index`、`name`、`description`、`bounding_box` 字段；`output_format` 仅控制底图格式，图层始终为 PNG
- `background` (可选) - 透明通道，`transparent` 生成透明背景图（需输入单张带透明通道的图片，与 `output_format=jpeg` 互斥）或 `opaque` 生成常规图，仅 5.0 Pro 支持
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容；图层拆分场景仅支持档位与 `auto`（按输入图自适应，未指定尺寸时的默认值）
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（Pro/标准/Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 同一提示并行发起的独立生成次数，每次各产出一张图，范围 1-10，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-10，默认 `min(request_count, 10)`，一般无需手动指定
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

```json
{
  "name": "image_to_image",
  "arguments": {
    "prompt": "把这张人像照片转换为吉卜力动画风格",
    "image": "2026-08-15/image_to_image/portrait.jpeg"
  }
}
```

</details>

<details>
<summary><b>3. <code>multi_image_fusion</code></b> — 多图融合</summary>

将多张图像融合生成新图像。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像融合要求或风格指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (必需) - 输入图像（2-14 张；5.0 Pro 最多 10 张），每张支持图像 URL、本地文件路径或 Base64 图片数据；本地文件路径须在读取范围内，其中相对路径仅限图片保存目录内
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（Pro/标准/Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`（5.0 Pro 不支持）
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 同一提示并行发起的独立生成次数，每次各产出一张图，范围 1-10，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-10，默认 `min(request_count, 10)`，一般无需手动指定
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

```json
{
  "name": "multi_image_fusion",
  "arguments": {
    "prompt": "把两张人像融合为一张双人合影，影棚灯光",
    "image": [
      "2026-08-15/multi_image_fusion/person_a.jpeg",
      "2026-08-15/multi_image_fusion/person_b.jpeg"
    ]
  }
}
```

</details>

<details>
<summary><b>4. <code>sequential_generation</code></b> — 组图输出</summary>

连续生成多张图像，支持文生组图、单图生组图、多图生组图（仅 doubao-seedream-5.0 系列（5.0/5.0-lite）/4.5/4.0 支持；5.0 Pro 不支持组图）。该工具调用外部计费 API、在本地产出文件，非只读。

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，应明确指明生成数量和内容，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"，fast 仅 5.0 Pro / 4.0 支持
- `image` (可选) - 参考图像（最多 14 张，且参考图数量与 max_images 之和不超过 15），每张支持图像 URL、本地文件路径或 Base64 图片数据；本地文件路径须在读取范围内，其中相对路径仅限图片保存目录内
- `size` (可选) - 图像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值，需与所选模型兼容
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `max_images` (可选) - 最大生成图像数量，范围 1-15，默认 15；提供参考图时默认自动扣减为 15 减参考图数量
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `output_format` (可选) - 输出文件格式，仅 5.0 系列（Pro/标准/Lite）支持 `jpeg` 或 `png`，默认不指定，由 API 按模型默认处理
- `stream` (可选) - 是否启用流式输出，默认`false`
- `tools` (可选) - 模型工具配置，仅 `doubao-seedream-5.0` / `5.0-lite` 系列支持联网搜索，例如 `[{"type":"web_search"}]`
- `request_count` (可选) - 同一提示并行发起的独立生成次数，每次各产出一组图片，组内图片数量由模型按提示词决定，最多 `max_images` 张，范围 1-10，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-10，默认 `min(request_count, 10)`，一般无需手动指定
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

**调用示例：**

```json
{
  "name": "sequential_generation",
  "arguments": {
    "prompt": "四格漫画：一只柴犬的一天，起床、吃饭、散步、睡觉"
  }
}
```

</details>

<details>
<summary><b>5. <code>browse_images</code></b> — 图片浏览</summary>

浏览工作区中的图片文件，获取文件路径用于图像生成。该工具只读、幂等、不访问网络。

**参数：**

- `directory` (可选) - 要浏览的目录路径，默认浏览图片保存目录；相对路径仅限图片保存目录内，绝对路径须在读取范围内。返回的条目为绝对路径，可直接作为参考图路径
- `recursive` (可选) - 是否递归搜索子目录，默认`true`
- `max_depth` (可选) - 最大搜索深度，范围 1-10，默认 3
- `limit` (可选) - 返回的最大文件数量，范围 1-200，默认 50
- `offset` (可选) - 分页偏移量（0-100000，从第几张开始返回），配合 `limit` 翻页，默认 0
- `format_filter` (可选) - 过滤特定图片格式，如`['.jpeg', '.png']`
- `show_details` (可选) - 是否显示详细文件信息，默认`false`

**调用示例：**

```json
{
  "name": "browse_images",
  "arguments": {}
}
```

</details>

## 📦 可用资源

除工具外，服务端还暴露以下 MCP 资源供客户端读取运行时信息：

<table align="center">
  <tr>
    <th style="text-align: center">资源 URI</th>
    <th style="text-align: center">说明</th>
  </tr>
  <tr>
    <td><code>seedream://workspace/roots</code></td>
    <td>当前生效的工作区根：客户端授权的 MCP Roots，未声明时回退环境配置的工作目录</td>
  </tr>
  <tr>
    <td><code>seedream://server/info</code></td>
    <td>服务器名称、版本与当前生效配置摘要（模型、默认尺寸、自动保存开关，共五项字段）</td>
  </tr>
  <tr>
    <td><code>seedream://models/info</code></td>
    <td>各模型别名与能力声明：支持的尺寸档位、像素范围、参考图上限、输出格式/工具/流式等能力，供客户端按需选择模型</td>
  </tr>
  <tr>
    <td><code>skill://seedream-image-generation/SKILL.md</code></td>
    <td>Agent Skill 主文件：图像生成指南入口，正文含工具速查、模型差异与参数规则</td>
  </tr>
  <tr>
    <td><code>skill://seedream-image-generation/references/{+path}</code></td>
    <td>Agent Skill 参考文件模板：多步工作流与故障排查，按需读取</td>
  </tr>
</table>

## 🧠 Agent Skills

服务器随包分发 [Agent Skills](https://agentskills.io) 开放标准技能目录，为 AI 客户端提供图像生成的完整方法论，两种方式可用：

- **资源自动发现**：客户端直接读取上表 `skill://` 资源，主文件常驻资源列表，参考文件按需读取
- **手动安装**：将包内 `seedream_mcp/skills/seedream-image-generation/` 整目录拷贝到客户端技能目录，例如 Claude Code 的 `~/.claude/skills/`

```bash
python -c "import pathlib, shutil, seedream_mcp; src = pathlib.Path(seedream_mcp.__file__).parent / 'skills' / 'seedream-image-generation'; shutil.copytree(src, pathlib.Path.home() / '.claude' / 'skills' / 'seedream-image-generation', dirs_exist_ok=True)"
```

技能目录包含以下文件：

<table align="center">
  <tr>
    <th style="text-align: center">文件</th>
    <th style="text-align: center">内容</th>
  </tr>
  <tr>
    <td><code>SKILL.md</code></td>
    <td>生成指南主文件：工具速查、模型差异、提示词写法、参数规则</td>
  </tr>
  <tr>
    <td><code>references/workflows.md</code></td>
    <td>多步工作流：连环画端到端、图层拆分与再合成、风格一致性迭代</td>
  </tr>
  <tr>
    <td><code>references/troubleshooting.md</code></td>
    <td>故障排查：错误码对策、常见失败模式、输入与配额约束</td>
  </tr>
</table>

## 🎭 风格预设

服务端内置以下 MCP 提示词模板，一键生成指定风格的文生图 prompt，可通过 `subject` 参数指定画面主题：

<table align="center">
  <tr>
    <th style="text-align: center">Prompt 名称</th>
    <th style="text-align: center">风格</th>
    <th style="text-align: center">默认主题</th>
  </tr>
  <tr>
    <td><code>seedream_style_anime</code></td>
    <td>日系动漫风格，赛璐珞上色，鲜艳饱和色彩</td>
    <td>一个女孩站在樱花树下</td>
  </tr>
  <tr>
    <td><code>seedream_style_realistic</code></td>
    <td>写实摄影风格，高清细节，自然光影</td>
    <td>城市夜景</td>
  </tr>
  <tr>
    <td><code>seedream_style_watercolor</code></td>
    <td>水彩画风格，柔和晕染，通透色彩</td>
    <td>山间小屋</td>
  </tr>
  <tr>
    <td><code>seedream_style_oil_painting</code></td>
    <td>油画风格，厚重笔触，丰富层次</td>
    <td>海边夕阳</td>
  </tr>
</table>

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

全部配置项、默认值与说明见 **[.env.example](.env.example)**，复制为 `.env` 后按需修改。

配置优先级：MCP 客户端显式配置（命令行参数） > 运行时系统环境变量 > `.env` 文件 > 默认值。

`.env` 加载规则：

- 使用 `--config-file` 时：仅加载指定文件。
- 未指定 `--config-file` 时：按“项目根 `.env` -> 当前工作目录 `.env`”顺序合并，后者覆盖前者。
- `.env` 的值**不会注入**进程环境变量，仅按上述优先级解析后写入配置对象，避免污染全局状态；系统环境变量优先于 `.env` 文件。

### 部署注意事项

- **保存目录由服务管理**：按天清理与总量配额只作用于图片目录 `<数据根目录>/.seedream/images`，目录内**所有**过期的图片文件与空目录都会被删除，不看文件来源；经 `save_path` 保存到其他目录的文件不受管理。
- **多客户端部署建议显式设置 `SEEDREAM_DATA_ROOT`**：数据根目录默认跟随客户端声明的 MCP Roots 变化，不同客户端的图片会散落在各自目录；显式声明后所有会话共用同一落点，读取范围与数据位置随之确定。
- **有状态会话依赖客户端正确断开**：streamable-http 会话在客户端发送 DELETE 或进程退出时回收，客户端异常退出时会话驻留；大量短连客户端的部署建议改用 `--stateless`。
- **Linux 宿主挂载目录属主**：容器以 uid 1000 运行，compose 挂载的 `./.seedream` 目录需对该用户可写：`mkdir -p .seedream && chown 1000:1000 .seedream`；Docker Desktop 不受影响。
- **出站连接不走系统代理**：API 调用与图片下载固定忽略 `HTTP_PROXY` 等系统代理环境变量；企业代理环境需保证主机直连公网，或经网络层透明代理转发。

## 👥 贡献者

### 项目维护者

- **[@tengmmvp](https://github.com/tengmmvp)** - 项目维护者

### 重要贡献者

- **[@caoergou](https://github.com/caoergou)** - 通过 [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2) 贡献了 uvx 支持、Docker 容器化配置、GitHub Actions 自动化发布流程，极大简化了项目的安装与部署体验

## 📄 许可证

这个项目基于 MIT 许可证开源。更多信息请查看 [LICENSE](LICENSE) 文件。
