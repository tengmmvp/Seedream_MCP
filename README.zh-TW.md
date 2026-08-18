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
  <b>基於火山引擎 Seedream API 的 AI 圖像生成 MCP 工具。</b>
</div>

---

<details>
<summary>本專案由 智譜 GLM Coding Plan 提供支援</summary>

<div align="center">
  <a href="https://www.bigmodel.cn/glm-coding?ic=GDEQEW52AC">
    <img src="https://raw.githubusercontent.com/tengmmvp/img2code/main/img/zhipu-glm-coding-plan.png" alt="Powered by 智譜 GLM Coding Plan · 智譜編碼套餐" />
  </a>
</div>

</details>

---

## ⚡ 快速安裝

### 1. 前置準備

安裝 [uv](https://docs.astral.sh/uv/)（包含 `uvx` 指令）：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

在[火山引擎控制台](https://console.volcengine.com/)取得 API 金鑰，透過環境變數 `ARK_API_KEY` 提供。

### 2. 一鍵啟動

```bash
# 透過環境變數提供金鑰（推薦）
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp

# 也可明確指定模型、尺寸等執行參數
ARK_API_KEY=your_api_key_here uvx seedream-image-mcp --model doubao-seedream-5.0 --default-size 2K
```

`uvx` 會自動從 [PyPI](https://pypi.org/project/seedream-image-mcp/) 拉取最新版本並在隔離環境中執行——無需 clone 儲存庫、無需手動建立虛擬環境、無需安裝相依套件。

### 3. 選用：Docker Compose

```bash
# 下載 docker-compose.yml
curl -O https://raw.githubusercontent.com/tengmmvp/Seedream_MCP/main/docker-compose.yml

# 選用：建立 .env（參考 .env.example）供 compose 唯讀掛載，替代下行環境變數前置
# 未建立 .env 時 Docker 會自動建立同名目錄導致掛載異常，需先 touch .env 或刪除 compose 中的該掛載

# 啟動服務
ARK_API_KEY=your_api_key_here SEEDREAM_HTTP_AUTH_TOKEN=your_token_here docker compose up -d
```

服務以 streamable-http 傳輸監聽容器內 `8000` 連接埠，宿主機連接埠由 `SEEDREAM_HTTP_PORT` 控制（預設 8000），MCP 端點路徑為 `/mcp`。連接埠映射預設僅綁定回環位址 `127.0.0.1`，需從其他裝置直連時把 docker-compose.yml 中的連接埠映射改為 `0.0.0.0:${SEEDREAM_HTTP_PORT:-8000}:8000` 或指定宿主機網卡位址。連接埠映射改為 `0.0.0.0` 即把服務暴露給網路，此時 `SEEDREAM_HTTP_AUTH_TOKEN` 會以明文 HTTP 過網傳輸；必須將服務置於 TLS 反向代理之後，或透過 `SEEDREAM_EXTRA_CLI_ARGS` 向容器提供 TLS 憑證參數，禁止在無 TLS 的狀態下對外暴露。用戶端接入設定（以 Claude Desktop 為例，其他支援 streamable-http 的用戶端同理）：

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

`<token>` 為佔位符，須與伺服器端環境變數 `SEEDREAM_HTTP_AUTH_TOKEN` 一致；若經 TLS 反向代理或容器內 TLS 暴露，`url` 改用 `https://` 形態（如 `https://mcp.example.com/mcp`）。靜態令牌鑑權不提供 OAuth 受保護資源元資料發現，標準 OAuth 客戶端需手動配置認證。

## 🔧 用戶端設定

> 推薦透過 `env` 注入 `ARK_API_KEY`，避免把金鑰寫進 `args`（命令列參數會出現在行程清單中，存在洩漏風險）。

### Claude Desktop

編輯 `claude_desktop_config.json`：

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
<summary><b>其他用戶端設定</b>（Claude Code · Cursor · Cline）</summary>

### Claude Code（命令列一鍵註冊）

```bash
claude mcp add seedream-image-mcp --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
```

### Cursor

在專案根目錄建立 `.cursor/mcp.json`：

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

### Cline / 其他 stdio 用戶端

通用設定（`command` + `args` + `env` 欄位同上）。Cline 編輯 `cline_mcp_settings.json`：

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

> 需要指定模型/尺寸時，附加到 `args`，例如 `["seedream-image-mcp", "--model", "doubao-seedream-5.0"]`。

設定後重啟對應用戶端即可使用。

## ⚙️ 啟動參數

```bash
# 驗證與設定來源
--api-key TEXT                                     # API 金鑰（選用，推薦使用環境變數 ARK_API_KEY）
--config-file TEXT                                 # 自訂 .env 設定檔路徑

# 模型與生成
--model [doubao-seedream-5.0-pro|doubao-seedream-5.0|doubao-seedream-5.0-lite|doubao-seedream-4.5|doubao-seedream-4.0]
                                                 # 模型選擇 (預設: doubao-seedream-5.0)
--default-size [1K|1.5K|2K|3K|4K|<寬>x<高>]        # 圖像尺寸 (預設: 2K，需與所選模型相容)
--watermark                                        # 啟用浮水印
--no-watermark                                     # 關閉浮水印

# 連線與傳輸
--base-url TEXT                                    # API 基礎 URL（預設按設定或內建預設值；須 https，http 需設 SEEDREAM_ALLOW_HTTP_BASE_URL=true 豁免）
--transport [stdio|streamable-http]                # MCP 傳輸方式 (預設: stdio)
--host TEXT                                        # streamable-http 監聽位址 (預設: 127.0.0.1；繫結非回環位址必須設定 --auth-token 與 TLS（或 --insecure-allow-non-tls 豁免），否則拒絕啟動)
--port INTEGER                                     # streamable-http 監聽連接埠 (預設: 8000)
--stateless                                        # streamable-http 無狀態模式，適合遠端多用戶端與負載平衡 (預設關閉)

# 安全
--auth-token TEXT                                  # Bearer 鑑權權杖（非回環繫結必須設定，也可用 SEEDREAM_HTTP_AUTH_TOKEN）
--ssl-certfile TEXT                                # TLS 憑證檔案（非回環繫結必須設定，防權杖明文傳輸，啟用後最低協定版本 TLS 1.2；受信任反向代理終結 TLS 時可用 --insecure-allow-non-tls 豁免）
--ssl-keyfile TEXT                                 # TLS 私鑰檔案，與 --ssl-certfile 搭配
--insecure-allow-non-tls                           # 明確允許非回環明文執行（僅受信任反向代理終結 TLS 場景）

# 日誌
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # 日誌層級
```

> **安全提示**：`localhost` 不被視為回環位址（其解析依賴 hosts/DNS，可能被污染指向非回環），繫結它同樣要求設定 Bearer 鑑權權杖與 TLS，未設定則服務拒絕啟動；如需回環免鑑權語義，請改繫結 `127.0.0.1` 或 `::1`。非回環繫結同樣必須設定 Bearer 權杖與 TLS。生產與容器部署應透過環境變數（`ARK_API_KEY` / `SEEDREAM_HTTP_AUTH_TOKEN`）傳遞密鑰，而非 CLI `--api-key` / `--auth-token`（命令列參數會暴露在行程清單與 shell 歷史記錄中）；多用戶主機上 streamable-http 即使繫結回環位址，也建議設定鑑權權杖。

### 使用範例

```bash
# 基本使用
ARK_API_KEY=your_key uvx seedream-image-mcp

# 使用自訂設定檔
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# 切換其他模型（如 4.0 / 4.5）並指定尺寸與除錯模式
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG

# 高精度生圖（5.0 Pro；注意：不支援組圖 / 連網搜尋 / 串流輸出，尺寸僅 1K/1.5K/2K）
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-5.0-pro
```

## 📐 模型能力差異

各模型支援的能力與參數範圍不同，選擇模型時請留意：

| 能力 / 參數                | 5.0 Pro        | 5.0 / 5.0 Lite | 4.5       | 4.0          |
| -------------------------- | -------------- | -------------- | --------- | ------------ |
| 文生圖 / 圖生圖 / 多圖生圖 | ✅             | ✅             | ✅        | ✅           |
| 組圖生成                   | ❌             | ✅             | ✅        | ✅           |
| 連網搜尋                   | ❌             | ✅             | ❌        | ❌           |
| 串流輸出                   | ❌             | ✅             | ✅        | ✅           |
| 輸出格式（png/jpeg）       | ✅             | ✅             | ❌        | ❌           |
| 圖層拆分                   | ✅             | ❌             | ❌        | ❌           |
| 透明背景                   | ✅             | ❌             | ❌        | ❌           |
| 解析度選項                 | 1K / 1.5K / 2K | 2K / 3K / 4K   | 2K / 4K   | 1K / 2K / 4K |
| 自訂尺寸倍數               | 16 的倍數      | 不限制         | 不限制    | 不限制       |
| MCP 預設尺寸               | 2048x2048      | 2048x2048      | 2048x2048 | 2048x2048    |
| 參考圖上限                 | 10 張          | 14 張          | 14 張     | 14 張        |

> **MCP 預設尺寸**：表中「MCP 預設尺寸」列為 MCP 統一設定 `default_size=2K`（對應 `2048x2048`）的執行階段解析值，與各模型原生預設無關。

> **提示**：預設模型為 **doubao-seedream-5.0**（與 5.0 Lite 等價），開箱即用全部能力。切換到 `doubao-seedream-5.0-pro` 後，組圖、連網搜尋、串流輸出不可用，尺寸僅支援 `1K/1.5K/2K`（預設 `2048x2048`），多圖生圖參考圖上限降為 10 張，另獨享圖層拆分與透明背景能力。

## 🛠️ 可用工具

<details>
<summary><b>1. <code>text_to_image</code></b> — 文生圖</summary>

根據文字提示詞生成圖像。該工具呼叫外部計費 API、在本機產出檔案，非唯讀。

**參數：**

- `prompt` (必要) - 圖像生成的文字提示詞，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 5.0 Pro / 4.0 支援
- `size` (選用) - 圖像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（Pro/標準/Lite）支援 `jpeg` 或 `png`，預設不指定，由 API 按模型預設處理
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 同一提示並行發起的獨立生成次數，每次各產出一張圖，範圍 1-10，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-10，預設 `min(request_count, 10)`，一般無需手動指定
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

**呼叫範例：**

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
<summary><b>2. <code>image_to_image</code></b> — 圖文生圖</summary>

根據輸入圖像和文字提示生成新圖像。該工具呼叫外部計費 API、在本機產出檔案，非唯讀。

**參數：**

- `prompt` (選用) - 圖像修改要求或風格轉換指令，建議不超過 300 個漢字或 600 個英文單字；僅圖層拆分場景可缺省，由模型自動識別拆分意圖
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 5.0 Pro / 4.0 支援
- `image` (必要) - 輸入圖像，支援圖像 URL、本地檔案路徑或 Base64 圖片資料
- `layer_decomposition` (選用) - 是否開啟圖層拆分，僅 5.0 Pro 支援；開啟後將單張輸入圖拆解為 1 張底圖與最多 16 個帶透明通道的 PNG 圖層，圖層條目額外回傳 `z_index`、`name`、`description`、`bounding_box` 欄位；`output_format` 僅控制底圖格式，圖層恆為 PNG
- `background` (選用) - 透明通道，`transparent` 生成透明背景圖（需輸入單張帶透明通道的圖片，與 `output_format=jpeg` 互斥）或 `opaque` 生成常規圖，僅 5.0 Pro 支援
- `size` (選用) - 圖像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容；圖層拆分場景僅支援檔位與 `auto`（按輸入圖自適應，未指定尺寸時的預設值）
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（Pro/標準/Lite）支援 `jpeg` 或 `png`，預設不指定，由 API 按模型預設處理
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 同一提示並行發起的獨立生成次數，每次各產出一張圖，範圍 1-10，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-10，預設 `min(request_count, 10)`，一般無需手動指定
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

**呼叫範例：**

```json
{
  "name": "image_to_image",
  "arguments": {
    "prompt": "把这张人像照片转换为吉卜力动画风格",
    "image": ".seedream/images/2026-08-15/image_to_image/portrait.jpeg"
  }
}
```

</details>

<details>
<summary><b>3. <code>multi_image_fusion</code></b> — 多圖融合</summary>

將多張圖像融合生成新圖像。該工具呼叫外部計費 API、在本機產出檔案，非唯讀。

**參數：**

- `prompt` (必要) - 圖像融合要求或風格指令，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 5.0 Pro / 4.0 支援
- `image` (必要) - 輸入圖像（2-14 張；5.0 Pro 最多 10 張），每張支援圖像 URL、本地檔案路徑或 Base64 圖片資料
- `size` (選用) - 圖像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（Pro/標準/Lite）支援 `jpeg` 或 `png`，預設不指定，由 API 按模型預設處理
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 同一提示並行發起的獨立生成次數，每次各產出一張圖，範圍 1-10，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-10，預設 `min(request_count, 10)`，一般無需手動指定
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

**呼叫範例：**

```json
{
  "name": "multi_image_fusion",
  "arguments": {
    "prompt": "把两张人像融合为一张双人合影，影棚灯光",
    "image": [
      ".seedream/images/2026-08-15/multi_image_fusion/person_a.jpeg",
      ".seedream/images/2026-08-15/multi_image_fusion/person_b.jpeg"
    ]
  }
}
```

</details>

<details>
<summary><b>4. <code>sequential_generation</code></b> — 組圖輸出</summary>

連續生成多張圖像，支援文生組圖、單圖生組圖、多圖生組圖（僅 doubao-seedream-5.0 系列（5.0/5.0-lite）/4.5/4.0 支援；5.0 Pro 不支援組圖）。該工具呼叫外部計費 API、在本機產出檔案，非唯讀。

**參數：**

- `prompt` (必要) - 圖像生成的文字提示詞，應明確指明生成數量與內容，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 5.0 Pro / 4.0 支援
- `image` (選用) - 參考圖像（最多 14 張，且參考圖數量與 max_images 之和不超過 15），每張支援圖像 URL、本地檔案路徑或 Base64 圖片資料
- `size` (選用) - 圖像尺寸：`1K`、`1.5K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `max_images` (選用) - 最大生成圖像數量，範圍 1-15，預設 15；提供參考圖時預設自動扣減為 15 減參考圖數量
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（Pro/標準/Lite）支援 `jpeg` 或 `png`，預設不指定，由 API 按模型預設處理
- `stream` (選用) - 是否啟用串流輸出，預設`false`
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 同一提示並行發起的獨立生成次數，每次各產出一組圖片，組內圖片數量由模型按提示詞決定，最多 `max_images` 張，範圍 1-10，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-10，預設 `min(request_count, 10)`，一般無需手動指定
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

**呼叫範例：**

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
<summary><b>5. <code>browse_images</code></b> — 圖片瀏覽</summary>

瀏覽工作區中的圖片檔案，取得檔案路徑用於圖像生成。該工具唯讀、冪等、不存取網路。

**參數：**

- `directory` (選用) - 要瀏覽的目錄路徑，預設瀏覽工作區根目錄（MCP Roots 授權的首個根；無 Roots 時回退 `SEEDREAM_WORKSPACE_ROOT` 設定的本地工作區根，均未設定時為行程目前工作目錄）
- `recursive` (選用) - 是否遞迴搜尋子目錄，預設`true`
- `max_depth` (選用) - 最大搜尋深度，範圍 1-10，預設 3
- `limit` (選用) - 回傳的最大檔案數量，範圍 1-200，預設 50
- `offset` (選用) - 分頁偏移量（0-100000，從第幾張開始回傳），搭配 `limit` 翻頁，預設 0
- `format_filter` (選用) - 篩選特定圖片格式，如`['.jpeg', '.png']`
- `show_details` (選用) - 是否顯示詳細檔案資訊，預設`false`

**呼叫範例：**

```json
{
  "name": "browse_images",
  "arguments": {}
}
```

</details>

## 📦 可用資源

除工具外，伺服器還公開以下 MCP 資源供用戶端讀取執行時資訊：

| 資源 URI                     | 說明                                                                                                                 |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `seedream://workspace/roots` | 用戶端授權的 MCP 工作區 Roots；未授權時為空，避免暴露伺服器本地目錄                                                  |
| `seedream://server/info`     | 伺服器名稱、版本與目前生效設定摘要（模型、預設尺寸、自動儲存開關，共五項欄位）                                       |
| `seedream://models/info`     | 各模型別名與能力宣告：支援的尺寸檔位、像素範圍、像素倍數、參考圖上限、輸出格式/工具/串流等能力，供用戶端按需選擇模型 |

## 🎭 風格預設

伺服器內建以下 MCP 提示詞範本，一鍵產生指定風格的文生圖 prompt，可透過 `subject` 參數指定畫面主題：

| Prompt 名稱                   | 風格                                   | 預設主題             |
| ----------------------------- | -------------------------------------- | -------------------- |
| `seedream_style_anime`        | 日系動漫風格，賽璐珞上色，鮮豔飽和色彩 | 一個女孩站在櫻花樹下 |
| `seedream_style_realistic`    | 寫實攝影風格，高畫質細節，自然光影     | 城市夜景             |
| `seedream_style_watercolor`   | 水彩畫風格，柔和暈染，通透色彩         | 山間小屋             |
| `seedream_style_oil_painting` | 油畫風格，厚重筆觸，豐富層次           | 海邊夕陽             |

## ❓ 常見問題

**Q: 找不到 uvx 指令？**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Q: 如何取得 API 金鑰？**
前往 [火山引擎控制台](https://console.volcengine.com/) 建立金鑰

**Q: Docker 服務無法啟動？**
請確保已設定環境變數：

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

## 🧪 本地開發

```bash
# 複製儲存庫
git clone https://github.com/tengmmvp/Seedream_MCP
cd Seedream_MCP

# 安裝相依套件（開發模式）
uv sync

# 建立 .env 檔案
cp .env.example .env
# 編輯 .env 檔案，新增您的 API 金鑰

# 啟動服務
uv run python -m seedream_mcp.server

# 或直接使用 API 金鑰啟動
uv run python -m seedream_mcp.server --api-key your_key
```

## ⚙️ 環境變數設定

主要設定項（詳見 `.env.example`）：

設定優先順序：MCP 用戶端明確設定（命令列參數） > 執行階段系統環境變數 > `.env` 檔案 > 預設值。

`.env` 載入規則：

- 使用 `--config-file` 時：僅載入指定檔案。
- 未指定 `--config-file` 時：按「專案根 `.env` -> 目前工作目錄 `.env`」順序合併，後者覆寫前者。
- `.env` 的值**不會注入**行程環境變數，僅按上述優先順序解析後寫入設定物件，避免污染全域狀態；系統環境變數優先於 `.env` 檔案。

```bash
# 必要設定
ARK_API_KEY=your_api_key_here

# API 端點安全
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # API 基礎 URL，預設火山引擎北京端點；須 https，http 會使 API Key 明文傳輸而被預設拒絕，僅自建可信內網端點可經 SEEDREAM_ALLOW_HTTP_BASE_URL 豁免
SEEDREAM_ALLOW_HTTP_BASE_URL=false                      # 豁免 http:// 的 ARK_BASE_URL（預設拒絕明文傳輸；僅自建可信內網端點設 true）

# 模型設定
SEEDREAM_MODEL_ID=doubao-seedream-5.0

# 預設值
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# 逾時
SEEDREAM_TIMEOUT=60                         # 連線建立/寫入/連線池取得逾時（秒）
SEEDREAM_API_TIMEOUT=600                    # API 呼叫讀取與總逾時（秒）
SEEDREAM_MAX_RETRIES=3                      # API 呼叫最大重試次數（429/5xx、逾時與網路錯誤重試，4xx 不重試）

# 日誌
LOG_LEVEL=INFO                              # 日誌級別（DEBUG / INFO / WARNING / ERROR / CRITICAL）
LOG_FILE=                                   # 日誌檔案路徑（預設 .seedream/logs/seedream_mcp.log，相對行程工作目錄解析）

# 自動儲存
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=                # 圖片儲存根目錄（預設 <工作區根>/.seedream/images，工作區根取 MCP Roots 首項或 SEEDREAM_WORKSPACE_ROOT）
SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT=30      # 單張圖片下載逾時（秒）
SEEDREAM_AUTO_SAVE_MAX_RETRIES=3            # 下載失敗最大重試次數（0 表示不重試）
SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE=52428800   # 單張圖片大小上限（位元組，預設 50MB）；另兼作流式單事件截斷閾值與回應體讀取上限的推導基準
SEEDREAM_RESPONSE_BODY_LIMIT=               # 上游回應體讀取總量上限（位元組；不設則按 SEEDREAM_AUTO_SAVE_MAX_FILE_SIZE×20 推導，非流式/流式 JSON 與 SSE 共用）
SEEDREAM_AUTO_SAVE_MAX_CONCURRENT=5         # 最大並行下載數
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30
SEEDREAM_AUTO_SAVE_FSYNC=false               # 落盤前 fsync：開啟提升崩潰一致性、略降寫入吞吐，預設關閉
SEEDREAM_AUTO_SAVE_MAX_TOTAL_BYTES=10737418240 # 儲存目錄總位元組上限（預設 10GB；超限按最舊檔案優先逐出）
SEEDREAM_PREVIEW_ENABLED=true                 # 生成結果附帶已儲存圖片的縮圖（對話內直接預覽，依賴自動儲存；預設開啟）

# 工作區與傳輸
SEEDREAM_WORKSPACE_ROOT=                    # 本地開發時檔案讀寫邊界回退目錄（MCP Roots 優先）
SEEDREAM_HTTP_AUTH_TOKEN=                   # streamable-http Bearer 鑑權權杖（非回環繫結必須設定，否則拒絕啟動；另需 TLS 或 --insecure-allow-non-tls 豁免）
SEEDREAM_HTTP_MAX_BODY_SIZE=67108864        # streamable-http 請求內文上限（位元組，≥1MB，預設 64MB；單圖 data URI 約 40MB，兼顧多圖融合）
SEEDREAM_HTTP_ALLOWED_HOSTS=                # 非回環直連部署的 Host 頭允許清單，逗號分隔，支援 host:port 與尾部 :* 萬用（如 mcp.example.com,mcp.example.com:*）；留空則整體關閉 SDK 內層 Host 校驗，適用反向代理場景

# 用戶端效能
SEEDREAM_IMAGE_PREPARE_CONCURRENCY=5
SEEDREAM_PREPARE_CACHE_MAX=32
SEEDREAM_PREPARE_CACHE_MAX_BYTES=268435456    # 參考圖前置處理快取累計位元組上限（預設 256MB）

# 串流處理
SEEDREAM_STREAM_BUFFER_MAX_SIZE=10485760      # SSE 串流回應緩衝區前綴回收閾值（預設 10MB）
SEEDREAM_STREAM_CHUNK_SIZE=1048576            # SSE 串流回應每次讀取區塊大小（預設 1MB）
```

### 部署注意事項

- **儲存目錄由服務管理**：自動儲存的按天清理與總量配額會刪除儲存目錄內**所有**符合圖片副檔名的過期檔案與空目錄，不區分是否由本服務生成。請勿將 `SEEDREAM_AUTO_SAVE_BASE_DIR` 指向個人相簿等含重要圖片的目錄。
- **多租戶 streamable-http 部署建議顯式設定 `SEEDREAM_WORKSPACE_ROOT`**：MCP Roots 讀取失敗時檔案存取邊界會回退到該環境變數（未設定時為行程工作目錄）。
- **未認證請求的體積限制**：未攜帶有效權杖的 chunked 請求不讀 body 即回傳 401，其體積限制依賴 uvicorn 層或前置反向代理；公網暴露部署請在代理層設定請求體上限。
- **Linux 宿主掛載目錄屬主**：容器以 uid 1000 的非 root 使用者執行，Linux 宿主上 compose 掛載的 `./.seedream` 目錄需對該使用者可寫（`mkdir -p .seedream && chown 1000:1000 .seedream`）；Docker Desktop 不受影響。

## 👥 貢獻者

### 專案維護者

- **[@tengmmvp](https://github.com/tengmmvp)** - 專案維護者

### 重要貢獻者

- **[@caoergou](https://github.com/caoergou)** - 透過 [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2) 貢獻了 uvx 支援、Docker 容器化設定、GitHub Actions 自動化發布流程，大幅簡化了專案的安裝與部署體驗

## 📄 授權條款

本專案基於 MIT 授權條款開源。更多資訊請查看 [LICENSE](LICENSE) 檔案。
