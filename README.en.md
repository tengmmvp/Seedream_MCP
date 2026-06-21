<h1 align="center">Seedream 4.0, 4.5 & 5.0 MCP Image Generation Tool</h1>

<p align="center">
  <a href="./README.md">简体中文</a>
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
  <b>An MCP tool based on the Volcengine Seedream 4.0, 4.5 and 5.0 APIs, supporting AI image generation.</b>
</div>

---

<details>
<summary>This project is powered by Zhipu GLM Coding Plan</summary>

<div align="center">
  <a href="https://www.bigmodel.cn/glm-coding?ic=GDEQEW52AC">
    <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/zhipu-glm-coding-plan.png" alt="Powered by Zhipu GLM Coding Plan" />
  </a>
</div>

</details>

---

## ⚡ Quick Start

### 1. Prerequisites

Install [uv](https://docs.astral.sh/uv/) (includes the `uvx` command):

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Get your API key from the [Volcengine Console](https://console.volcengine.com/) and provide it via the `ARK_API_KEY` environment variable.

### 2. One-Command Launch

```bash
# Provide the key via an environment variable (recommended)
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp

# Or explicitly specify model, size and other options
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp --model doubao-seedream-5.0 --default-size 2K
```

`uvx` automatically pulls the latest version from [PyPI](https://pypi.org/project/seedream-image-mcp/) and runs it in an isolated environment — no need to clone the repo, create a virtual environment, or install dependencies.

### 3. Optional: Docker Compose

```bash
# Download docker-compose.yml
curl -O https://raw.githubusercontent.com/tengmmvp/Seedream_MCP/main/docker-compose.yml

# Start the service
ARK_API_KEY=your_api_key_here docker-compose up -d
```

## 🔧 Client Configuration

> It is recommended to inject `ARK_API_KEY` via `env` rather than writing it into `args` (command-line arguments appear in the process list and pose a leakage risk).

### Claude Desktop

Edit `claude_desktop_config.json`:

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
<summary><b>Other client configurations</b> (Claude Code · Cursor · Cline)</summary>

### Claude Code (one-line registration)

```bash
claude mcp add seedream --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
```

### Cursor

Create `.cursor/mcp.json` in the project root:

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

### Cline / Other stdio clients

Generic configuration (the `command` + `args` + `env` fields are the same as above). For Cline, edit `cline_mcp_settings.json`:

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

> To specify a model/size, append it to `args`, e.g. `["seedream-image-mcp", "--model", "doubao-seedream-5.0"]`.

Restart the corresponding client after configuration.

## ⚙️ CLI Options

```bash
# Authentication & configuration source
--api-key TEXT                                     # API key (optional; ARK_API_KEY env var recommended)
--config-file TEXT                                 # Custom .env config file path

# Model & generation
--model [doubao-seedream-5.0|doubao-seedream-5.0-lite|doubao-seedream-4.5|doubao-seedream-4.0]
                                                   # Model selection (default: doubao-seedream-5.0)
--default-size [1K|2K|3K|4K|<width>x<height>]      # Image size (default: 2K; must be compatible with the model)
--watermark                                        # Enable watermark
--no-watermark                                     # Disable watermark

# Connection & transport
--base-url TEXT                                    # API base URL (default per config or built-in default)
--transport [stdio|streamable-http]                # MCP transport (default: stdio)

# Logging
--log-level [DEBUG|INFO|WARNING|ERROR]             # Log level
```

### Usage Examples

```bash
# Basic usage
ARK_API_KEY=your_key uvx seedream-image-mcp

# Use a custom config file
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# Use the Seedream 4.0 model
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.0

# High-quality image + debug mode
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG
```

## 🎨 Features

- **Text-to-Image**: generate images from text
- **Image-to-Image**: transform image styles
- **Multi-Image Fusion**: blend multiple images
- **Sequential Generation**: generate image sequences
- **Browse Images**: browse local image files

## 🛠️ Available Tools

<details>
<summary><b>1. <code>seedream_text_to_image</code></b> — Text-to-Image</summary>

Generate an image from a text prompt

**Parameters:**

- `prompt` (required) - Text prompt for image generation; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"
- `size` (optional) - Image size: `1K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only `doubao-seedream-5.0` supports `jpeg` or `png`
- `stream` (optional) - Whether to enable streaming output; default `false`
- `tools` (optional) - Model tool config; only `doubao-seedream-5.0` supports this, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of parallel requests; range 1-4; default 1
- `parallelism` (optional) - Parallelism cap; range 1-4; default `min(request_count, 4)`
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

</details>

<details>
<summary><b>2. <code>seedream_image_to_image</code></b> — Image-to-Image</summary>

Generate a new image from an input image and a text prompt

**Parameters:**

- `prompt` (required) - Image editing request or style transfer instruction; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"
- `image` (required) - URL or local file path of the input image
- `size` (optional) - Image size: `1K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only `doubao-seedream-5.0` supports `jpeg` or `png`
- `stream` (optional) - Whether to enable streaming output; default `false`
- `tools` (optional) - Model tool config; only `doubao-seedream-5.0` supports this, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of parallel requests; range 1-4; default 1
- `parallelism` (optional) - Parallelism cap; range 1-4; default `min(request_count, 4)`
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

</details>

<details>
<summary><b>3. <code>seedream_multi_image_fusion</code></b> — Multi-Image Fusion</summary>

Fuse multiple images into a new image

**Parameters:**

- `prompt` (required) - Image fusion request or style instruction; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"
- `image` (required) - List of input image URLs or local file paths (2-14 images)
- `size` (optional) - Image size: `1K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only `doubao-seedream-5.0` supports `jpeg` or `png`
- `stream` (optional) - Whether to enable streaming output; default `false`
- `tools` (optional) - Model tool config; only `doubao-seedream-5.0` supports this, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of parallel requests; range 1-4; default 1
- `parallelism` (optional) - Parallelism cap; range 1-4; default `min(request_count, 4)`
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

</details>

<details>
<summary><b>4. <code>seedream_sequential_generation</code></b> — Sequential Generation</summary>

Generate multiple images in sequence; supports text-to-sequence, single-image-to-sequence, and multi-image-to-sequence

**Parameters:**

- `prompt` (required) - Text prompt for image generation; should clearly specify the quantity and content; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"
- `image` (optional) - Reference image(s); supports a single image (string) or multiple images (array); up to 14 reference images, and the sum of reference images and max_images must not exceed 15
- `size` (optional) - Image size: `1K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `max_images` (optional) - Maximum number of images to generate; range 1-15; default 15
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only `doubao-seedream-5.0` supports `jpeg` or `png`
- `stream` (optional) - Whether to enable streaming output; default `false`
- `tools` (optional) - Model tool config; only `doubao-seedream-5.0` supports this, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of parallel requests; range 1-4; default 1
- `parallelism` (optional) - Parallelism cap; range 1-4; default `min(request_count, 4)`
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

</details>

<details>
<summary><b>5. <code>seedream_browse_images</code></b> — Browse Images</summary>

Browse image files in the workspace and get file paths for image generation

**Parameters:**

- `directory` (optional) - Directory to browse; defaults to the current directory
- `recursive` (optional) - Whether to search subdirectories recursively; default `true`
- `max_depth` (optional) - Maximum search depth; range 1-10; default 3
- `limit` (optional) - Maximum number of files to return; range 1-200; default 50
- `offset` (optional) - Pagination offset (0-100000; index of the first item to return); used with `limit` for pagination; default 0
- `format_filter` (optional) - Filter by specific image formats, e.g. `['.jpeg', '.png']`
- `show_details` (optional) - Whether to show detailed file info; default `false`

</details>

## ❓ FAQ

**Q: `uvx` command not found?**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Q: How do I get an API key?**
Visit the [Volcengine Console](https://console.volcengine.com/) to create a key.

**Q: Docker service won't start?**
Make sure the environment variable is set:

```bash
# Linux/macOS
export ARK_API_KEY=your_key
docker-compose up -d

# Windows
$env:ARK_API_KEY="your_key"
docker-compose up -d
```

## 🧪 Local Development

```bash
# Clone the repo
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP

# Install dependencies (dev mode)
uv sync --dev

# Create the .env file
cp .env.example .env
# Edit .env and add your API key

# Start the service
uv run python -m seedream_mcp.server

# Or start directly with an API key
uv run python -m seedream_mcp.server --api-key your_key
```

## ⚙️ Environment Variables

Main configuration options (see `.env.example` for details):

Configuration priority: MCP client explicit config (CLI args) > runtime system environment variables > `.env` file > defaults.

`.env` loading rules:

- When `--config-file` is used: only the specified file is loaded.
- When `--config-file` is not specified: files are merged in the order "project-root `.env` -> current-working-directory `.env`", with the latter overriding the former.
- `.env` injects variables into the process environment for runtime use, but does not override existing system environment variables.

```bash
# Required
ARK_API_KEY=your_api_key_here

# Model config
SEEDREAM_MODEL_ID=doubao-seedream-5-0-260128

# Defaults
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# Auto-save
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=./seedream_images
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30
```

## 👥 Contributors

### Maintainers

- **[@tengmmvp](https://github.com/tengmmvp)** - Project maintainer

### Key Contributors

- **[@caoergou](https://github.com/caoergou)** - Contributed uvx support, Docker containerization, and the GitHub Actions automated release workflow via [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2), greatly simplifying installation and deployment.

### Contributing

Issues and Pull Requests are welcome! Check [GitHub Issues](https://github.com/tengmmvp/Seedream_MCP/issues) to see current discussions and needs.

<div align="center"><b>🌟 If you'd like to contribute, please discuss your ideas in Issues first!</b></div>

## 📄 License

This project is open-sourced under the MIT License. See the [LICENSE](LICENSE) file for more information.
