# Seedream 4.0 MCP 工具

[![uvx](https://img.shields.io/badge/uvx-ready-brightgreen.svg)](https://github.com/astral-sh/uv)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![MCP](https://img.shields.io/badge/MCP-compatible-orange.svg)

基于火山引擎 Seedream 4.0 API 的 MCP 工具，支持 AI 图像生成。

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
--api-key TEXT        # API 密钥（必需）
--default-size [1K|2K|4K]  # 图像尺寸 (默认: 2K)
--watermark                 # 启用水印
--log-level [DEBUG|INFO|WARNING|ERROR]  # 日志级别
```

### 使用示例

```bash
# 基础使用
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --api-key your_key

# 高质量图像 + 调试模式
uvx git+https://github.com/tengmmvp/Seedream_MCP \
  --api-key your_key --default-size 4K --log-level DEBUG
```

## 🎨 功能特性

- **文生图**：文本生成图像
- **图生图**：图像转换风格
- **多图融合**：融合多张图片
- **组图生成**：生成图像序列
- **自动保存**：图片本地存储

## 🛠️ 可用工具

### 1. `seedream_text_to_image` - 文生图

根据文本提示词生成图像

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，建议不超过 600 个字符
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K`，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 2. `seedream_image_to_image` - 图生图

根据输入图像和文本提示生成新图像

**参数：**

- `prompt` (必需) - 图像修改要求或风格转换指令，建议不超过 600 个字符
- `image` (必需) - 输入图像的 URL 或本地文件路径
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K`，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 3. `seedream_multi_image_fusion` - 多图融合

将多张图像融合生成新图像

**参数：**

- `prompt` (必需) - 图像融合要求或风格指令，建议不超过 600 个字符
- `images` (必需) - 输入图像 URL 或本地文件路径列表（2-5 张图像）
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K`，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 4. `seedream_sequential_generation` - 组图生成

连续生成多张图像，支持文生组图、单图生组图、多图生组图

**参数：**

- `prompt` (必需) - 图像生成的文本提示词，应明确指明生成数量和内容，建议不超过 600 个字符
- `max_images` (可选) - 最大生成图像数量，范围 1-15，默认 4
- `image` (可选) - 参考图像，支持单张图片（字符串）或多张图片（数组）
- `size` (可选) - 图像尺寸：`1K`、`2K`、`4K`，默认使用配置文件值
- `watermark` (可选) - 是否添加水印，默认使用配置文件值
- `response_format` (可选) - 响应格式：`url`或`b64_json`，默认`url`
- `auto_save` (可选) - 是否自动保存到本地，默认使用全局配置
- `save_path` (可选) - 自定义保存目录路径
- `custom_name` (可选) - 自定义文件名前缀

### 5. `seedream_browse_images` - 图片浏览

浏览工作区中的图片文件，获取文件路径用于图像生成

**参数：**

- `directory` (可选) - 要浏览的目录路径，默认当前目录
- `recursive` (可选) - 是否递归搜索子目录，默认`true`
- `max_depth` (可选) - 最大搜索深度，范围 1-10，默认 3
- `limit` (可选) - 返回的最大文件数量，范围 1-200，默认 50
- `format_filter` (可选) - 过滤特定图片格式，如`['.jpg', '.png']`
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
export ARK_API_KEY=your_key
docker-compose up -d
```

## 🧪 本地开发

```bash
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP
uv sync --dev
uv run python -m seedream_mcp.server --api-key your_key
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
