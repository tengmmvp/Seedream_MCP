<h1 align="center">Seedream 4.0 和 Seedream 4.5 MCP 生图工具</h1>

<div align="center">
  <a href="https://zread.ai/tengmmvp/Seedream_MCP">
    <img src="https://img.shields.io/badge/Ask_Zread-_.svg?style=for-the-badge&color=00b0aa&labelColor=000000&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTQuOTYxNTYgMS42MDAxSDIuMjQxNTZDMS44ODgxIDEuNjAwMSAxLjYwMTU2IDEuODg2NjQgMS42MDE1NiAyLjI0MDFWNC45NjAxQzEuNjAxNTYgNS4zMTM1NiAxLjg4ODEgNS42MDAxIDIuMjQxNTYgNS42MDAxSDQuOTYxNTZDNS4zMTUwMiA1LjYwMDEgNS42MDE1NiA1LjMxMzU2IDUuNjAxNTYgNC45NjAxVjIuMjQwMUM1LjYwMTU2IDEuODg2NjQgNS4zMTUwMiAxLjYwMDEgNC45NjE1NiAxLjYwMDFaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00Ljk2MTU2IDEwLjM5OTlIMi4yNDE1NkMxLjg4ODEgMTAuMzk5OSAxLjYwMTU2IDEwLjY4NjQgMS42MDE1NiAxMS4wMzk5VjEzLjc1OTlDMS42MDE1NiAxNC4xMTM0IDEuODg4MSAxNC4zOTk5IDIuMjQxNTYgMTQuMzk5OUg0Ljk2MTU2QzUuMzE1MDIgMTQuMzk5OSA1LjYwMTU2IDE0LjExMzQgNS42MDE1NiAxMy43NTk5VjExLjAzOTlDNS42MDE1NiAxMC42ODY0IDUuMzE1MDIgMTAuMzk5OSA0Ljk2MTU2IDEwLjM5OTlaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik0xMy43NTg0IDEuNjAwMUgxMS4wMzg0QzEwLjY4NSAxLjYwMDEgMTAuMzk4NCAxLjg4NjY0IDEwLjM5ODQgMi4yNDAxVjQuOTYwMUMxMC4zOTg0IDUuMzEzNTYgMTAuNjg1IDUuNjAwMSAxMS4wMzg0IDUuNjAwMUgxMy43NTg0QzE0LjExMTkgNS42MDAxIDE0LjM5ODQgNS4zMTM1NiAxNC4zOTg0IDQuOTYwMVYyLjI0MDFDMTQuMzk4NCAxLjg4NjY0IDE0LjExMTkgMS42MDAxIDEzLjc1ODQgMS42MDAxWiIgZmlsbD0iI2ZmZiIvPgo8cGF0aCBkPSJNNCAxMkwxMiA0TDQgMTJaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00IDEyTDEyIDQiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8L3N2Zz4K&logoColor=ffffff" alt="Ask Zread"/>
  </a>
  <br>
  <img src="https://img.shields.io/github/v/release/tengmmvp/Seedream_MCP?display_name=tag&sort=semver&label=Release" alt="Version"/>
  <img src="https://img.shields.io/badge/Python-3.10+-cyan.svg" alt="Python"/>
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"/>
  <img src="https://img.shields.io/badge/Powered_By-Codex%26GLM-violet.svg" alt="Powered by Codex&GLM"/>
  <br><br>
  <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/doubao-seedream-4-5.jpeg" alt="Seedream MCP" width="440"/>
  <br><br>
  <b>基于火山引擎 Seedream 4.0 和 Seedream 4.5 API 的 MCP 工具，支持 AI 图像生成。</b>
</div>

## ⚡ 快速安装

### 方法 1：uvx 一键启动（推荐）

```bash
# 直接从 GitHub 仓库启动
uvx git+https://github.com/tengmmvp/Seedream_MCP --api-key your_api_key_here

# 或者先克隆再启动
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP
uvx . --api-key your_api_key_here
```

### 方法 2：Docker Compose

```bash
# 下载 docker-compose.yml
curl -O https://raw.githubusercontent.com/tengmmvp/Seedream_MCP/main/docker-compose.yml

# 启动服务
ARK_API_KEY=your_api_key_here docker-compose up -d
```

## 🔧 Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "seedream": {
      "command": "uvx",
      "args": [
        "git+https://github.com/tengmmvp/Seedream_MCP",
        "--api-key",
        "your_api_key_here"
      ]
    }
  }
}
```

重启 Claude Desktop 即可使用。

## ⚙️ 启动参数

```bash
--api-key TEXT                                     # API 密钥（必需）
--model [doubao-seedream-4.5|doubao-seedream-4.0]  # 模型选择 (默认: doubao-seedream-4.5)
--default-size [1K|2K|4K|<宽>x<高>]                # 图像尺寸 (默认: 2K)
--watermark                                        # 启用水印
--log-level [DEBUG|INFO|WARNING|ERROR]             # 日志级别
--transport [stdio|sse|streamable-http]            # MCP 传输方式 (默认: stdio)
--mount-path TEXT                                  # SSE 挂载路径（仅 transport=sse 生效）
--config-file TEXT                                 # 自定义 .env 配置文件路径
```

### 使用示例

```bash
# 基础使用
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --api-key your_key

# 使用自定义配置文件
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --config-file ./my-config.env --api-key your_key

# 使用 Seedream 4.0 模型
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --api-key your_key --model doubao-seedream-4.0

# 高质量图像 + 调试模式
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --api-key your_key --default-size 4K --log-level DEBUG

# 以 SSE 模式运行并指定挂载路径
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --transport sse --mount-path /mcp --api-key your_key

# 以 Streamable HTTP 模式运行
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --transport streamable-http --api-key your_key
```

## 🎨 功能特性

- **文生图**：文本生成图像
- **图文生图**：图像转换风格
- **多图融合**：融合多张图片
- **组图输出**：生成图像序列
- **图片浏览**：本地图片文件浏览

## 🛠️ 可用工具

### 1. `seedream_text_to_image` - 文生图

根据文本提示词生成图像

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `stream` (可选) - 是否启用流式输出，默认`false`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 2. `seedream_image_to_image` - 图文生图

根据输入图像和文本提示生成新图像

**参数：**

- `prompt` (必需) - 图像修改要求或风格转换指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"
- `image` (必需) - 输入图像的 URL 或本地文件路径
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `stream` (可选) - 是否启用流式输出，默认`false`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 3. `seedream_multi_image_fusion` - 多图融合

将多张图像融合生成新图像

**参数：**

- `prompt` (必需) - 图像融合要求或风格指令，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"
- `image` (必需) - 输入图像 URL 或本地文件路径列表（2-14 张图像）
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `stream` (可选) - 是否启用流式输出，默认`false`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 4. `seedream_sequential_generation` - 组图输出

连续生成多张图像，支持文生组图、单图生组图、多图生组图

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，应明确指明生成数量和内容，建议不超过 300 个汉字或 600 个英文单词
- `optimize_prompt_options` (可选) - 提示词优化选项，支持 mode: "standard" 或 "fast"
- `image` (可选) - 参考图像，支持单张图片（字符串）或多张图片（数组）；参考图最多 14 张，且参考图数量与 max_images 之和不超过 15
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K` 或 `<宽>x<高>` 像素值，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值（默认 false）
- `max_images` (可选) - 最大生成图像数量，范围 1-15，默认 15
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `stream` (可选) - 是否启用流式输出，默认`false`
- `request_count` (可选) - 并行请求次数，范围 1-4，默认 1
- `parallelism` (可选) - 并行度上限，范围 1-4，默认 `min(request_count, 4)`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置（默认 true）
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 5. `seedream_browse_images` - 图片浏览

浏览工作区中的图片文件，获取文件路径用于图像生成

**参数：**

- `directory` (可选) - 要浏览的目录路径，默认当前目录
- `recursive` (可选) - 是否递归搜索子目录，默认`true`
- `max_depth` (可选) - 最大搜索深度，范围 1-10，默认 3
- `limit` (可选) - 返回的最大文件数量，范围 1-200，默认 50
- `format_filter` (可选) - 过滤特定图片格式，如`['.jpeg', '.png']`
- `show_details` (可选) - 是否显示详细文件信息，默认`false`

## 🆘 常见问题

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
uv sync --dev

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
- `.env` 会注入进程环境变量供运行时读取，但不会覆盖已存在的系统环境变量。

```bash
# 必需配置
ARK_API_KEY=your_api_key_here

# 模型配置
SEEDREAM_MODEL_ID=doubao-seedream-4-5-251128

# 默认值
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# 自动保存
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=./seedream_images
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30
```

## 👥 贡献者

### 项目创建者

- **[@tengmmvp](https://github.com/tengmmvp)** - 项目创建者

### 重要贡献者

- **[@caoergou](https://github.com/caoergou)** - 通过 [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2) 贡献了 uvx 支持、Docker 容器化配置、GitHub Actions 自动化发布流程，极大简化了项目的安装与部署体验

### 参与贡献

欢迎提交 Issue 和 Pull Request！请查看 [GitHub Issues](https://github.com/tengmmvp/Seedream_MCP/issues) 了解当前的讨论和需求。

**🌟 如果您希望参与开发，请先在 Issues 中讨论您的想法！**

## 📄 许可证

这个项目基于 MIT 许可证开源。更多信息请查看 [LICENSE](LICENSE) 文件。
