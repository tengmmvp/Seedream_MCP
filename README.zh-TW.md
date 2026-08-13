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
  <b>基於火山引擎 Seedream 4.0、4.5 與 5.0 系列（含 5.0 Pro）API 的 MCP 工具，支援 AI 圖像生成。</b>
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

# 啟動服務
ARK_API_KEY=your_api_key_here docker-compose up -d
```

## 🔧 用戶端設定

> 推薦透過 `env` 注入 `ARK_API_KEY`，避免把金鑰寫進 `args`（命令列參數會出現在行程清單中，存在洩漏風險）。

### Claude Desktop

編輯 `claude_desktop_config.json`：

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
<summary><b>其他用戶端設定</b>（Claude Code · Cursor · Cline）</summary>

### Claude Code（命令列一鍵註冊）

```bash
claude mcp add seedream --env ARK_API_KEY=your_api_key_here -- uvx seedream-image-mcp
```

### Cursor

在專案根目錄建立 `.cursor/mcp.json`：

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

### Cline / 其他 stdio 用戶端

通用設定（`command` + `args` + `env` 欄位同上）。Cline 編輯 `cline_mcp_settings.json`：

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
--default-size [1K|2K|3K|4K|<寬>x<高>]            # 圖像尺寸 (預設: 2K，需與所選模型相容)
--watermark                                        # 啟用浮水印
--no-watermark                                     # 關閉浮水印

# 連線與傳輸
--base-url TEXT                                    # API 基礎 URL（預設按設定或內建預設值）
--transport [stdio|streamable-http]                # MCP 傳輸方式 (預設: stdio)
--host TEXT                                        # streamable-http 監聽位址 (預設: 127.0.0.1；繫結非回環位址將觸發安全告警)
--port INTEGER                                     # streamable-http 監聽連接埠 (預設: 8000)
--stateless                                        # streamable-http 無狀態模式，適合遠端多用戶端與負載平衡 (預設關閉)

# 安全
--auth-token TEXT                                  # Bearer 鑑權權杖（非回環繫結必須設定，也可用 SEEDREAM_HTTP_AUTH_TOKEN）
--ssl-certfile TEXT                                # TLS 憑證檔案（非回環繫結必須設定，防權杖明文傳輸；受信任反向代理終結 TLS 時可用 --insecure-allow-non-tls 豁免）
--ssl-keyfile TEXT                                 # TLS 私鑰檔案，與 --ssl-certfile 搭配
--insecure-allow-non-tls                           # 明確允許非回環明文執行（僅受信任反向代理終結 TLS 場景）

# 日誌
--log-level [DEBUG|INFO|WARNING|ERROR|CRITICAL]    # 日誌層級
```

> **安全提示**：繫結 `localhost` 時服務將其視為回環位址，不強制 Bearer 鑑權與 TLS。部署方應確認 `localhost` 解析到 `127.0.0.1` 或 `::1`，容器與虛擬環境若修改 hosts 需特別注意；非回環繫結必須設定 Bearer 權杖與 TLS。

### 使用範例

```bash
# 基本使用
ARK_API_KEY=your_key uvx seedream-image-mcp

# 使用自訂設定檔
ARK_API_KEY=your_key uvx seedream-image-mcp --config-file ./my-config.env

# 切換其他模型（如 4.0 / 4.5）並指定尺寸與除錯模式
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-4.5 --default-size 4K --log-level DEBUG

# 高精度生圖（5.0 Pro；注意：不支援組圖 / 連網搜尋 / 串流輸出，尺寸僅 1K/2K）
ARK_API_KEY=your_key uvx seedream-image-mcp --model doubao-seedream-5.0-pro
```

## 📐 模型能力差異

各模型支援的能力與參數範圍不同，選擇模型時請留意：

| 能力 / 參數                | 5.0 Pro   | 5.0 / 5.0 Lite | 4.5       | 4.0          |
| -------------------------- | --------- | ------------ | --------- | ------------ |
| 文生圖 / 圖生圖 / 多圖生圖 | ✅        | ✅           | ✅        | ✅           |
| 組圖生成                   | ❌        | ✅           | ✅        | ✅           |
| 連網搜尋                   | ❌        | ✅           | ❌        | ❌           |
| 串流輸出                   | ❌        | ✅           | ✅        | ✅           |
| 輸出格式（png/jpeg）       | ✅        | ✅           | ❌        | ❌           |
| 解析度選項                 | 1K / 2K   | 2K / 3K / 4K | 2K / 4K   | 1K / 2K / 4K |
| 自訂尺寸倍數                 | 16 的倍數    | 不限制          | 不限制       | 不限制          |
| MCP 預設尺寸               | 2048x2048 | 2048x2048    | 2048x2048 | 2048x2048    |
| 參考圖上限                 | 10 張     | 14 張        | 14 張     | 14 張        |

> **MCP 預設尺寸**：表中「MCP 預設尺寸」列為 MCP 統一設定 `default_size=2K`（對應 `2048x2048`）的執行階段解析值，與各模型原生預設無關（例如 5.0 Pro 原生預設為 `1024x1024`）。

> **提示**：預設模型為 **doubao-seedream-5.0**（與 5.0 Lite 等價），開箱即用全部能力。切換到 `doubao-seedream-5.0-pro` 後，組圖、連網搜尋、串流輸出不可用，尺寸僅支援 `1K/2K`（預設 `2048x2048`），多圖生圖參考圖上限降為 10 張。

## 🎨 功能特性

- **文生圖**：文字生成圖像
- **圖文生圖**：圖像轉換風格
- **多圖融合**：融合多張圖片
- **組圖輸出**：生成圖像序列
- **圖片瀏覽**：本地圖片檔案瀏覽

## 🛠️ 可用工具

<details>
<summary><b>1. <code>seedream_text_to_image</code></b> — 文生圖</summary>

根據文字提示詞生成圖像

**參數：**

- `prompt` (必要) - 圖像生成的文字提示詞，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 4.0 支援
- `size` (選用) - 圖像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（5.0 Pro/5.0 Lite）支援 `jpeg` 或 `png`，預設 `jpeg`
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 並行請求次數，範圍 1-4，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-4，預設 `min(request_count, 4)`
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

</details>

<details>
<summary><b>2. <code>seedream_image_to_image</code></b> — 圖文生圖</summary>

根據輸入圖像和文字提示生成新圖像

**參數：**

- `prompt` (必要) - 圖像修改要求或風格轉換指令，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 4.0 支援
- `image` (必要) - 輸入圖像的 URL 或本地檔案路徑
- `size` (選用) - 圖像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（5.0 Pro/5.0 Lite）支援 `jpeg` 或 `png`，預設 `jpeg`
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 並行請求次數，範圍 1-4，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-4，預設 `min(request_count, 4)`
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

</details>

<details>
<summary><b>3. <code>seedream_multi_image_fusion</code></b> — 多圖融合</summary>

將多張圖像融合生成新圖像

**參數：**

- `prompt` (必要) - 圖像融合要求或風格指令，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 4.0 支援
- `image` (必要) - 輸入圖像 URL 或本地檔案路徑清單（2-14 張；5.0 Pro 最多 10 張）
- `size` (選用) - 圖像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（5.0 Pro/5.0 Lite）支援 `jpeg` 或 `png`，預設 `jpeg`
- `stream` (選用) - 是否啟用串流輸出，預設`false`（5.0 Pro 不支援）
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 並行請求次數，範圍 1-4，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-4，預設 `min(request_count, 4)`
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

</details>

<details>
<summary><b>4. <code>seedream_sequential_generation</code></b> — 組圖輸出</summary>

連續生成多張圖像，支援文生組圖、單圖生組圖、多圖生組圖（僅 5.0 Lite/4.5/4.0 支援；5.0 Pro 不支援組圖）

**參數：**

- `prompt` (必要) - 圖像生成的文字提示詞，應明確指明生成數量與內容，建議不超過 300 個漢字或 600 個英文單字
- `optimize_prompt_options` (選用) - 提示詞最佳化選項，支援 mode: "standard" 或 "fast"，fast 僅 4.0 支援
- `image` (選用) - 參考圖像，支援單張圖片（字串）或多張圖片（陣列）；參考圖最多 14 張，且參考圖數量與 max_images 之和不超過 15
- `size` (選用) - 圖像尺寸：`1K`、`2K`、`3K`、`4K` 或 `<寬>x<高>` 像素值，預設使用設定檔值，需與所選模型相容
- `watermark` (選用) - 是否新增浮水印，預設使用設定檔值（預設 false）
- `max_images` (選用) - 最大生成圖像數量，範圍 1-15，預設 15
- `response_format` (選用) - 回應格式：`url`或`b64_json`，預設`url`
- `output_format` (選用) - 輸出檔案格式，僅 5.0 系列（5.0 Pro/5.0 Lite）支援 `jpeg` 或 `png`，預設 `jpeg`
- `stream` (選用) - 是否啟用串流輸出，預設`false`
- `tools` (選用) - 模型工具設定，僅 `doubao-seedream-5.0` / `5.0-lite` 系列支援連網搜尋，例如 `[{"type":"web_search"}]`
- `request_count` (選用) - 並行請求次數，範圍 1-4，預設 1
- `parallelism` (選用) - 並行度上限，範圍 1-4，預設 `min(request_count, 4)`
- `auto_save` (選用) - 是否自動儲存到本地，預設使用全域設定（預設 true）
- `save_path` (選用) - 自訂儲存目錄路徑
- `custom_name` (選用) - 自訂檔名前置詞

</details>

<details>
<summary><b>5. <code>seedream_browse_images</code></b> — 圖片瀏覽</summary>

瀏覽工作區中的圖片檔案，取得檔案路徑用於圖像生成

**參數：**

- `directory` (選用) - 要瀏覽的目錄路徑，預設目前目錄
- `recursive` (選用) - 是否遞迴搜尋子目錄，預設`true`
- `max_depth` (選用) - 最大搜尋深度，範圍 1-10，預設 3
- `limit` (選用) - 回傳的最大檔案數量，範圍 1-200，預設 50
- `offset` (選用) - 分頁偏移量（0-100000，從第幾張開始回傳），搭配 `limit` 翻頁，預設 0
- `format_filter` (選用) - 篩選特定圖片格式，如`['.jpeg', '.png']`
- `show_details` (選用) - 是否顯示詳細檔案資訊，預設`false`

</details>

## 📦 可用資源

除工具外，伺服器還公開以下 MCP 資源供用戶端讀取執行時資訊：

| 資源 URI | 說明 |
| --- | --- |
| `seedream://workspace/roots` | 用戶端授權的 MCP 工作區 Roots；未授權時為空，避免暴露伺服器本地目錄 |
| `seedream://server/info` | 伺服器名稱、版本與目前生效設定摘要（模型、預設尺寸、自動儲存開關等） |
| `seedream://models/info` | 各模型別名與能力宣告：支援的尺寸檔位、像素範圍、像素倍數、參考圖上限、輸出格式/工具/串流等能力，供用戶端按需選擇模型 |

## 🎭 風格預設

伺服器內建以下 MCP 提示詞範本，一鍵產生指定風格的文生圖 prompt，可透過 `subject` 參數指定畫面主題：

| Prompt 名稱 | 風格 | 預設主題 |
| --- | --- | --- |
| `seedream_style_anime` | 日系動漫風格，賽璐珞上色，鮮豔飽和色彩 | 一個女孩站在櫻花樹下 |
| `seedream_style_realistic` | 寫實攝影風格，高畫質細節，自然光影 | 城市夜景 |
| `seedream_style_watercolor` | 水彩畫風格，柔和暈染，通透色彩 | 山間小屋 |
| `seedream_style_oil_painting` | 油畫風格，厚重筆觸，豐富層次 | 海邊夕陽 |

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
docker-compose up -d

# Windows
$env:ARK_API_KEY="your_key"
docker-compose up -d
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

# 模型設定
SEEDREAM_MODEL_ID=doubao-seedream-5.0

# 預設值
SEEDREAM_DEFAULT_SIZE=2K
SEEDREAM_DEFAULT_WATERMARK=false

# 逾時
SEEDREAM_TIMEOUT=60                         # 連線建立/寫入/連線池取得逾時（秒）
SEEDREAM_API_TIMEOUT=600                    # API 呼叫讀取與總逾時（秒）

# 自動儲存
SEEDREAM_AUTO_SAVE_ENABLED=true
SEEDREAM_AUTO_SAVE_BASE_DIR=./seedream_images
SEEDREAM_AUTO_SAVE_DOWNLOAD_TIMEOUT=30      # 單張圖片下載逾時（秒）
SEEDREAM_AUTO_SAVE_DATE_FOLDER=true
SEEDREAM_AUTO_SAVE_CLEANUP_DAYS=30

# 工作區與傳輸
SEEDREAM_WORKSPACE_ROOT=                    # 本地開發時檔案讀寫邊界回退目錄（MCP Roots 優先）
SEEDREAM_HTTP_AUTH_TOKEN=                   # streamable-http Bearer 鑑權權杖（非回環繫結建議設定）

# 用戶端效能
SEEDREAM_IMAGE_PREPARE_CONCURRENCY=5
SEEDREAM_PREPARE_CACHE_MAX=32
```

## 👥 貢獻者

### 專案維護者

- **[@tengmmvp](https://github.com/tengmmvp)** - 專案維護者

### 重要貢獻者

- **[@caoergou](https://github.com/caoergou)** - 透過 [PR #2](https://github.com/tengmmvp/Seedream_MCP/pull/2) 貢獻了 uvx 支援、Docker 容器化設定、GitHub Actions 自動化發布流程，大幅簡化了專案的安裝與部署體驗

### 參與貢獻

歡迎提交 Issue 與 Pull Request！請查看 [GitHub Issues](https://github.com/tengmmvp/Seedream_MCP/issues) 了解目前的討論與需求。

<div align="center"><b>🌟 如果您希望參與開發，請先在 Issues 中討論您的想法！</b></div>

## 📄 授權條款

本專案基於 MIT 授權條款開源。更多資訊請查看 [LICENSE](LICENSE) 檔案。
