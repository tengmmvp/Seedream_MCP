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
  <b>An MCP tool based on the Volcengine Seedream APIs for AI image generation.</b>
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

# Optional: create .env (see .env.example) for the read-only compose mount, instead of prefixing env vars below

# Start the service
ARK_API_KEY=your_api_key_here SEEDREAM_HTTP_AUTH_TOKEN=your_token_here docker compose up -d
```

The service listens on container port `8000` via the streamable-http transport; the host port is controlled by `SEEDREAM_HTTP_PORT` (default 8000), and the MCP endpoint path is `/mcp`. Client configuration (Claude Desktop shown; other streamable-http clients are analogous):

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

`<token>` is a placeholder and must match the server-side `SEEDREAM_HTTP_AUTH_TOKEN` environment variable; when the service is exposed through a TLS reverse proxy or in-container TLS, use the `https://` form for `url` (e.g. `https://mcp.example.com/mcp`).

## 🔧 Client Configuration

> It is recommended to inject `ARK_API_KEY` via `env` rather than writing it into `args` (command-line arguments appear in the process list and pose a leakage risk).

### Claude Desktop

Edit `claude_desktop_config.json`:

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
<summary><b>Other client configurations</b> (Claude Code · Cursor · Cline)</summary>

### Claude Code (one-line registration)

```bash
claude mcp add seedream-image-mcp --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
```

### Cursor

Create `.cursor/mcp.json` in the project root:

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

### Cline / Other stdio clients

Generic configuration (the `command` + `args` + `env` fields are the same as above). For Cline, edit `cline_mcp_settings.json`:

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

> To specify a model/size, append it to `args`, e.g. `["seedream-image-mcp", "--model", "doubao-seedream-5.0"]`.

Restart the corresponding client after configuration.

## ⚙️ CLI Options

```bash
# Authentication & configuration source
--api-key TEXT                                     # API key (optional; ARK_API_KEY env var recommended)
--config-file TEXT                                 # Custom .env config file path

# Model & generation
--model [doubao-seedream-5.0-pro|doubao-seedream-5.0|doubao-seedream-5.0-lite|doubao-seedream-4.5|doubao-seedream-4.0]
                                                   # Model selection (default: doubao-seedream-5.0)
--default-size [1K|1.5K|2K|3K|4K|<width>x<height>] # Image size (default: 2K; must be compatible with the model)
--watermark                                        # Enable watermark
--no-watermark                                     # Disable watermark

# Connection & transport
--base-url TEXT                                    # API base URL (default per config or built-in default; must be https, http requires SEEDREAM_ALLOW_HTTP_BASE_URL=true)
--transport [stdio|streamable-http]                # MCP transport (default: stdio)
--host TEXT                                        # streamable-http listen address (default: 127.0.0.1; binding to a non-loopback address requires --auth-token along with TLS (or the --insecure-allow-non-tls exemption), and the service refuses to start without them)
--port INTEGER                                     # streamable-http listen port (default: 8000)
--stateless                                        # streamable-http stateless mode, suited for remote multi-client and load balancing (default off)

# Security
--auth-token TEXT                                  # Bearer auth token (required for non-loopback binding; alternatively use SEEDREAM_HTTP_AUTH_TOKEN)
--ssl-certfile TEXT                                # TLS certificate file (required for non-loopback binding to prevent plaintext token transmission, minimum protocol version TLS 1.2 once enabled; use --insecure-allow-non-tls when a trusted reverse proxy terminates TLS)
--ssl-keyfile TEXT                                 # TLS private key file, used together with --ssl-certfile
--insecure-allow-non-tls                           # Explicitly allow non-loopback plaintext operation (only for trusted reverse proxy TLS-terminating scenarios)

# Logging
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # Log level
```

> **Security note**: `localhost` is not treated as a loopback address (its resolution depends on hosts/DNS and may be poisoned to a non-loopback address). Binding to it likewise requires a Bearer auth token and TLS, and the service refuses to start without them; for loopback semantics without auth, bind to `127.0.0.1` or `::1` instead. Non-loopback bindings must likewise configure a Bearer token and TLS. In production and container deployments, pass secrets via environment variables (`ARK_API_KEY` / `SEEDREAM_HTTP_AUTH_TOKEN`) instead of the CLI flags `--api-key` / `--auth-token`, which are exposed in the process list and shell history; on multi-user hosts, configure an auth token for streamable-http even when it binds to a loopback address.

### Usage Examples

```bash
# Basic usage
ARK_API_KEY=your_key uvx seedream-image-mcp

# Use a custom config file
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# Switch to other models (e.g. 4.0 / 4.5) with a custom size and debug mode
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG

# High-precision image generation (5.0 Pro; note: sequential generation / web search / streaming output not supported; sizes 1K/1.5K/2K only)
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-5.0-pro
```

## 📐 Model Capability Differences

Different models support different capabilities and parameter ranges. Please note this when selecting a model:

| Capability / Parameter                       | 5.0 Pro        | 5.0 / 5.0 Lite | 4.5       | 4.0          |
| -------------------------------------------- | -------------- | -------------- | --------- | ------------ |
| Text-to-Image / Image-to-Image / Multi-Image | ✅             | ✅             | ✅        | ✅           |
| Sequential Generation                        | ❌             | ✅             | ✅        | ✅           |
| Web Search                                   | ❌             | ✅             | ❌        | ❌           |
| Streaming Output                             | ❌             | ✅             | ✅        | ✅           |
| Output Format (png/jpeg)                     | ✅             | ✅             | ❌        | ❌           |
| Layer Decomposition                          | ✅             | ❌             | ❌        | ❌           |
| Transparent Background                       | ✅             | ❌             | ❌        | ❌           |
| Resolution Presets                           | 1K / 1.5K / 2K | 2K / 3K / 4K   | 2K / 4K   | 1K / 2K / 4K |
| Custom Size Multiple                         | Multiple of 16 | No limit       | No limit  | No limit     |
| Default Size (MCP)                           | 2048x2048      | 2048x2048      | 2048x2048 | 2048x2048    |
| Max Reference Images                         | 10             | 14             | 14        | 14           |

> **Default Size (MCP)**: The "Default Size (MCP)" row reflects the runtime resolved value of MCP's unified `default_size=2K` setting (corresponding to `2048x2048`), independent of each model's native default.

> **Tip**: The default model is **doubao-seedream-5.0** (equivalent to 5.0 Lite), with all capabilities available out of the box. After switching to `doubao-seedream-5.0-pro`, sequential generation, web search, and streaming output are unavailable; only `1K/1.5K/2K` sizes are supported (default `2048x2048`), the multi-image reference cap drops to 10, plus exclusive layer decomposition and transparent background support.

## 🛠️ Available Tools

<details>
<summary><b>1. <code>seedream_text_to_image</code></b> — Text-to-Image</summary>

Generate an image from a text prompt. This tool calls an external billed API and produces files locally; it is not read-only.

**Parameters:**

- `prompt` (required) - Text prompt for image generation; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"; `fast` is only supported by 5.0 Pro / 4.0
- `size` (optional) - Image size: `1K`, `1.5K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only the 5.0 series (Pro/Standard/Lite) supports `jpeg` or `png`; by default not specified and handled by the API per the model default
- `stream` (optional) - Whether to enable streaming output; default `false` (5.0 Pro not supported)
- `tools` (optional) - Model tool config; only the `doubao-seedream-5.0` / `5.0-lite` series supports web search, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of independent generations launched in parallel for the same prompt, one image each; range 1-10; default 1
- `parallelism` (optional) - Parallelism cap; range 1-10; default `min(request_count, 10)`; usually no need to set manually
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

**Call examples:**

```json
{
  "name": "seedream_text_to_image",
  "arguments": {
    "prompt": "水彩风格的江南水乡，清晨薄雾"
  }
}
```

</details>

<details>
<summary><b>2. <code>seedream_image_to_image</code></b> — Image-to-Image</summary>

Generate a new image from an input image and a text prompt. This tool calls an external billed API and produces files locally; it is not read-only.

**Parameters:**

- `prompt` (optional) - Image editing request or style transfer instruction; recommended no more than 300 Chinese characters or 600 English words; may be omitted only in the layer decomposition scenario, where the model automatically identifies elements to split
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"; `fast` is only supported by 5.0 Pro / 4.0
- `image` (required) - URL or local file path of the input image
- `layer_decomposition` (optional) - Enable layer decomposition, only supported by 5.0 Pro; splits the single input image into 1 base image and up to 16 PNG layers with alpha channels; layer entries additionally return `z_index`, `name`, `description`, and `bounding_box` fields; `output_format` only controls the base image format — layers are always PNG
- `background` (optional) - Transparency mode: `transparent` produces a transparent-background image (requires a single input image with an alpha channel; mutually exclusive with `output_format=jpeg`) or `opaque` produces a regular image; only supported by 5.0 Pro
- `size` (optional) - Image size: `1K`, `1.5K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model; the layer decomposition scenario only supports presets and `auto` (adapts to the input image, and is the default when no size is specified)
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only the 5.0 series (Pro/Standard/Lite) supports `jpeg` or `png`; by default not specified and handled by the API per the model default
- `stream` (optional) - Whether to enable streaming output; default `false` (5.0 Pro not supported)
- `tools` (optional) - Model tool config; only the `doubao-seedream-5.0` / `5.0-lite` series supports web search, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of independent generations launched in parallel for the same prompt, one image each; range 1-10; default 1
- `parallelism` (optional) - Parallelism cap; range 1-10; default `min(request_count, 10)`; usually no need to set manually
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

**Call examples:**

```json
{
  "name": "seedream_image_to_image",
  "arguments": {
    "prompt": "把这张人像照片转换为吉卜力动画风格",
    "image": ".seedream/images/2026/08/15/portrait.jpeg"
  }
}
```

</details>

<details>
<summary><b>3. <code>seedream_multi_image_fusion</code></b> — Multi-Image Fusion</summary>

Fuse multiple images into a new image. This tool calls an external billed API and produces files locally; it is not read-only.

**Parameters:**

- `prompt` (required) - Image fusion request or style instruction; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"; `fast` is only supported by 5.0 Pro / 4.0
- `image` (required) - List of input image URLs or local file paths (2-14 images; 5.0 Pro max 10)
- `size` (optional) - Image size: `1K`, `1.5K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only the 5.0 series (Pro/Standard/Lite) supports `jpeg` or `png`; by default not specified and handled by the API per the model default
- `stream` (optional) - Whether to enable streaming output; default `false` (5.0 Pro not supported)
- `tools` (optional) - Model tool config; only the `doubao-seedream-5.0` / `5.0-lite` series supports web search, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of independent generations launched in parallel for the same prompt, one image each; range 1-10; default 1
- `parallelism` (optional) - Parallelism cap; range 1-10; default `min(request_count, 10)`; usually no need to set manually
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

**Call examples:**

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

</details>

<details>
<summary><b>4. <code>seedream_sequential_generation</code></b> — Sequential Generation</summary>

Generate multiple images in sequence; supports text-to-sequence, single-image-to-sequence, and multi-image-to-sequence (only the doubao-seedream-5.0 series (5.0/5.0-lite)/4.5/4.0 supported; 5.0 Pro does not support sequential generation). This tool calls an external billed API and produces files locally; it is not read-only.

**Parameters:**

- `prompt` (required) - Text prompt for image generation; should clearly specify the quantity and content; recommended no more than 300 Chinese characters or 600 English words
- `optimize_prompt_options` (optional) - Prompt optimization options; supports mode: "standard" or "fast"; `fast` is only supported by 5.0 Pro / 4.0
- `image` (optional) - Reference image(s); supports a single image (string) or multiple images (array); up to 14 reference images, and the sum of reference images and max_images must not exceed 15
- `size` (optional) - Image size: `1K`, `1.5K`, `2K`, `3K`, `4K` or `<width>x<height>` pixels; defaults to the config value; must be compatible with the selected model
- `watermark` (optional) - Whether to add a watermark; defaults to the config value (default false)
- `max_images` (optional) - Maximum number of images to generate; range 1-15; default 15, automatically reduced by the number of reference images when provided
- `response_format` (optional) - Response format: `url` or `b64_json`; default `url`
- `output_format` (optional) - Output file format; only the 5.0 series (Pro/Standard/Lite) supports `jpeg` or `png`; by default not specified and handled by the API per the model default
- `stream` (optional) - Whether to enable streaming output; default `false`
- `tools` (optional) - Model tool config; only the `doubao-seedream-5.0` / `5.0-lite` series supports web search, e.g. `[{"type":"web_search"}]`
- `request_count` (optional) - Number of independent generations launched in parallel for the same prompt, one image each; range 1-10; default 1
- `parallelism` (optional) - Parallelism cap; range 1-10; default `min(request_count, 10)`; usually no need to set manually
- `auto_save` (optional) - Whether to auto-save locally; defaults to the global config (default true)
- `save_path` (optional) - Custom save directory path
- `custom_name` (optional) - Custom filename prefix

**Call examples:**

```json
{
  "name": "seedream_sequential_generation",
  "arguments": {
    "prompt": "四格漫画：一只柴犬的一天，起床、吃饭、散步、睡觉"
  }
}
```

</details>

<details>
<summary><b>5. <code>seedream_browse_images</code></b> — Browse Images</summary>

Browse image files in the workspace and get file paths for image generation. This tool is read-only, idempotent, and does not access the network.

**Parameters:**

- `directory` (optional) - Directory path to browse; defaults to the workspace root (the first root authorized by MCP Roots; falls back to the local workspace root configured via `SEEDREAM_WORKSPACE_ROOT` when no Roots are set, and to the process current working directory when neither is set)
- `recursive` (optional) - Whether to search subdirectories recursively; default `true`
- `max_depth` (optional) - Maximum search depth; range 1-10; default 3
- `limit` (optional) - Maximum number of files to return; range 1-200; default 50
- `offset` (optional) - Pagination offset (0-100000; index of the first item to return); used with `limit` for pagination; default 0
- `format_filter` (optional) - Filter by specific image formats, e.g. `['.jpeg', '.png']`
- `show_details` (optional) - Whether to show detailed file info; default `false`

**Call examples:**

```json
{
  "name": "seedream_browse_images",
  "arguments": {}
}
```

</details>

## 📦 Available Resources

Beyond tools, the server exposes the following MCP resources for clients to read runtime information:

| Resource URI                 | Description                                                                                                                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `seedream://workspace/roots` | MCP workspace Roots authorized by the client; empty when none authorized, avoiding exposure of server-local directories                                                                           |
| `seedream://server/info`     | Server name, version, and a summary of the active configuration (model, default size, auto-save toggle; five fields in total)                                                                     |
| `seedream://models/info`     | Per-model aliases and capability declarations: supported size presets, pixel ranges, pixel multiples, reference image limits, output format/tools/streaming, etc., to help clients choose a model |

## 🎭 Style Presets

The server provides the following MCP prompt templates to generate text-to-image prompts for a given style in one click; use the `subject` parameter to set the scene subject:

| Prompt name                   | Style                                                     | Default subject                       |
| ----------------------------- | --------------------------------------------------------- | ------------------------------------- |
| `seedream_style_anime`        | Japanese anime style, cel shading, vivid saturated colors | A girl standing under cherry blossoms |
| `seedream_style_realistic`    | Realistic photography, high-detail, natural lighting      | City night view                       |
| `seedream_style_watercolor`   | Watercolor style, soft blending, translucent colors       | Mountain cabin                        |
| `seedream_style_oil_painting` | Oil painting style, thick brushstrokes, rich layers       | Seaside sunset                        |

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
export SEEDREAM_HTTP_AUTH_TOKEN=your_token
docker compose up -d

# Windows
$env:ARK_API_KEY="your_key"
$env:SEEDREAM_HTTP_AUTH_TOKEN="your_token"
docker compose up -d
```

## 🧪 Local Development

```bash
# Clone the repo
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP

# Install dependencies (dev mode)
uv sync

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
- `.env` values are **not injected** into the process environment; they are only resolved by the above priority and written to the config object, avoiding global state pollution. System environment variables take precedence over the `.env` file.

```bash
# Required
ARK_API_KEY=your_api_key_here

# API endpoint security
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # API base URL, defaults to the Volcengine Beijing endpoint; must be https, http sends the API key in plaintext and is rejected by default, only trusted self-hosted intranet endpoints can be exempted via SEEDREAM_ALLOW_HTTP_BASE_URL
SEEDREAM_ALLOW_HTTP_BASE_URL=false                      # Exempt an http:// ARK_BASE_URL (plaintext rejected by default; set true only for trusted self-hosted intranet endpoints)

# Model config
SEEDREAM_MODEL_ID=doubao-seedream-5.0

# Defaults
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# Timeouts
SEEDREAM_TIMEOUT=60                         # Connection/write/pool-acquire timeout (seconds)
SEEDREAM_API_TIMEOUT=600                    # API call read & total timeout (seconds)
SEEDREAM_MAX_RETRIES=3                      # Max retries for API calls (retries 429/5xx, timeouts, network errors; no retry on 4xx)

# Logging
LOG_LEVEL=INFO                              # Log level (DEBUG / INFO / WARNING / ERROR / CRITICAL)
LOG_FILE=                                   # Log file path (default .seedream/logs/seedream_mcp.log, resolved relative to the process working directory)

# Auto-save
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=                # Image save root directory (default <workspace root>/.seedream/images; workspace root is the first MCP Root or SEEDREAM_WORKSPACE_ROOT)
SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT=30      # Per-image download timeout (seconds)
SEEDREAM_AUTO_SAVE_MAX_RETRIES=3            # Max retries for failed downloads (0 disables retry)
SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE=52428800   # Max file size per image (bytes, default 50MB); also the derivation base for the stream single-event truncate threshold and the response-body read limit
SEEDREAM_RESPONSE_BODY_LIMIT=               # Total upstream response-body read limit (bytes; derived as SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE×20 when unset, shared by non-stream/stream JSON and SSE)
SEEDREAM_AUTO_SAVE_MAX_CONCURRENT=5         # Max concurrent downloads
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30
SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES=10737418240 # Total byte cap for the save directory (default 10GB; oldest evicted first when exceeded)

# Workspace & transport
SEEDREAM_WORKSPACE_ROOT=                    # Local-dev file I/O boundary fallback (MCP Roots take precedence)
SEEDREAM_HTTP_AUTH_TOKEN=                   # streamable-http Bearer auth token (required for non-loopback binding, or the service refuses to start; TLS or the --insecure-allow-non-tls exemption is also required)
SEEDREAM_HTTP_MAX_BODY_SIZE=67108864        # streamable-http request body size limit (bytes, ≥1MB, default 64MB; a single data-URI image is ~40MB, 64MB covers multi-image fusion)

# Client performance
SEEDREAM_IMAGE_PREPARE_CONCURRENCY=5
SEEDREAM_PREPARE_CACHE_MAX=32
SEEDREAM_PREPARE_CACHE_MAX_BYTES=268435456    # Reference image prepare cache total byte cap (default 256MB)

# Streaming
SEEDREAM_STREAM_BUFFER_MAX_SIZE=10485760      # SSE stream buffer prefix reclaim threshold (default 10MB)
SEEDREAM_STREAM_CHUNK_SIZE=1048576            # SSE stream per-read chunk size (default 1MB)
```

### Deployment Notes

- **The save directory is managed by the server**: age-based cleanup and total-size quota eviction delete **all** expired files with supported image extensions (and empty directories) inside the save directory, regardless of whether they were created by this server. Do not point `SEEDREAM_AUTO_SAVE_BASE_DIR` at directories holding important personal images.
- **Set `SEEDREAM_WORKSPACE_ROOT` explicitly for multi-tenant streamable-http deployments**: if reading MCP Roots fails, the file access boundary falls back to this variable (or the process working directory when unset).
- **Body size of unauthenticated requests**: unauthenticated chunked requests are rejected with 401 before their body is read; their size limiting relies on uvicorn or a fronting reverse proxy. Configure a request body limit at the proxy layer for public deployments.

## 👥 Contributors

### Maintainers

- **[@tengmmvp](https://github.com/tengmmvp)** - Project maintainer

### Key Contributors

- **[@caoergou](https://github.com/caoergou)** - Contributed uvx support, Docker containerization, and the GitHub Actions automated release workflow via [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2), greatly simplifying installation and deployment.

## 📄 License

This project is open-sourced under the MIT License. See the [LICENSE](LICENSE) file for more information.
