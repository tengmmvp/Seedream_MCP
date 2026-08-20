/* 生成台域：配置加载、工具切换的表单形态、请求组装与结果渲染。
   表单字段名与后端 schemas.py 的 *Input 模型一一对应，新增参数须两侧同步。 */

"use strict";

import {
  $,
  apiFetch,
  fetchBlobUrl,
  fetchExternalBlobUrl,
  revokeObjectUrls,
  state,
} from "./api.js";
import { renderReferences, toolConfig } from "./refs.js";
import { openLightbox } from "./gallery.js";

/* config-info 是启动面：填充尺寸档位与能力开关；模型跟随服务器配置无下拉。 */
export async function loadConfigInfo() {
  const response = await apiFetch("/web/api/config-info");
  if (!response.ok) throw new Error("config-info 请求失败");
  const info = await response.json();
  state.configInfo = info;

  const current = (info.models || []).find((m) => m.model_id === info.model_id);
  $("server-meta").textContent = current
    ? `${current.display_name} · ${info.default_size} 默认`
    : info.model_id;

  const sizeSelect = $("size");
  sizeSelect.innerHTML = "";
  const presets = current ? current.allowed_presets : ["2K", "3K", "4K"];
  for (const preset of presets) {
    const option = document.createElement("option");
    option.value = preset;
    option.textContent =
      preset === info.default_size ? `${preset}（默认）` : preset;
    sizeSelect.appendChild(option);
  }
  const custom = document.createElement("option");
  custom.value = "custom";
  custom.textContent = "自定义";
  sizeSelect.appendChild(custom);

  const outputFormatField = $("output-format").closest(".field");
  if (current && !current.supports_output_format)
    outputFormatField.classList.add("hidden");

  updateToolAvailability();
}

/* 当前模型能力下各表单区的显隐与提示词必填态；提示词可留空的说明只在对应
   能力真实可用时提及，避免误导用户寻找不存在的开关。 */
export function applyToolUI() {
  const config = toolConfig(state.tool);
  const current = state.configInfo
    ? (state.configInfo.models || []).find(
        (m) => m.model_id === state.configInfo.model_id,
      )
    : null;
  const layerAllowed =
    state.tool === "image-to-image" &&
    current &&
    current.supports_layer_decomposition;

  $("reference-section").classList.toggle("hidden", !config.refs);
  $("prompt-label").textContent = "提示词";
  $("prompt-hint").textContent = config.promptOptional
    ? layerAllowed
      ? "启用图层拆分或纯改图时可留空；建议不超过 300 字。"
      : "纯改图时可留空；建议不超过 300 字。"
    : "建议不超过 300 字。";
  $("max-images-field").classList.toggle(
    "hidden",
    state.tool !== "sequential-generation",
  );
  const layerField = $("layer-field");
  layerField.classList.toggle("hidden", !layerAllowed);
  if (!layerAllowed) $("layer-decomposition").checked = false;
  while (state.refs.length > config.max) state.refs.pop();
  renderReferences();
}

/* 按模型能力启停工具 tab：不支持组图的模型禁用组图入口，当前工具被禁时
   回落文生图。 */
export function updateToolAvailability() {
  const current = state.configInfo
    ? (state.configInfo.models || []).find(
        (m) => m.model_id === state.configInfo.model_id,
      )
    : null;
  const sequentialAllowed = current
    ? current.supports_sequential_generation
    : true;
  const sequentialTab = document.querySelector(
    '[data-tool="sequential-generation"]',
  );
  sequentialTab.disabled = !sequentialAllowed;
  sequentialTab.title = sequentialAllowed ? "" : "当前模型不支持组图生成";
  if (state.tool === "sequential-generation" && !sequentialAllowed) {
    state.tool = "text-to-image";
    document.querySelectorAll("#tool-tabs button").forEach((b) => {
      b.classList.toggle("active", b.dataset.tool === "text-to-image");
    });
    applyToolUI();
  }
}

export function buildRequestBody() {
  const config = toolConfig(state.tool);
  const body = { prompt: $("prompt").value.trim() };

  if (config.refs && state.refs.length > 0) {
    const values = state.refs.map((ref) => ref.value);
    body.image = state.tool === "image-to-image" ? values[0] : values;
  }

  const sizeValue = $("size").value;
  if (sizeValue === "custom") {
    const width = Number($("size-width").value);
    const height = Number($("size-height").value);
    if (width && height) body.size = `${width}x${height}`;
  } else if (sizeValue) {
    body.size = sizeValue;
  }

  const requestCount = Number($("request-count").value);
  if (requestCount > 1) body.request_count = requestCount;
  if ($("watermark").checked) body.watermark = true;

  if (state.tool === "sequential-generation") {
    body.max_images = Number($("max-images").value) || undefined;
  }
  if (
    !$("layer-field").classList.contains("hidden") &&
    $("layer-decomposition").checked
  ) {
    body.layer_decomposition = true;
  }

  const outputFormat = $("output-format").value;
  if (outputFormat) body.output_format = outputFormat;
  if (!$("auto-save").checked) body.auto_save = false;
  const savePath = $("save-path").value.trim();
  if (savePath) body.save_path = savePath;
  const customName = $("custom-name").value.trim();
  if (customName) body.custom_name = customName;

  return body;
}

/* 状态行与等待动画的联动：running 时状态行带呼吸点，预览区域铺对角波点阵。 */
export function setStatus(kind, text) {
  const status = $("result-status");
  status.className = `result-status ${kind}`;
  status.textContent = "";
  if (kind === "running") {
    const dots = document.createElement("span");
    dots.className = "loading-dots";
    dots.appendChild(document.createElement("i"));
    dots.appendChild(document.createElement("i"));
    dots.appendChild(document.createElement("i"));
    status.appendChild(dots);
  }
  status.appendChild(document.createTextNode(text));
  const loading = $("result-loading");
  loading.classList.toggle("hidden", kind !== "running");
  if (kind === "running") {
    buildRenderDots();
  } else {
    $("render-dots").innerHTML = "";
  }
}

/* 渲染点阵：按容器尺寸铺点，点亮延迟随行列递增，形成左上到右下扫过的
   对角波。须在容器可见后调用，隐藏态取不到宽度。 */
function buildRenderDots() {
  const container = $("render-dots");
  container.innerHTML = "";
  const gap = 22;
  const cols = Math.max(8, Math.floor((container.clientWidth || 600) / gap));
  const rows = Math.max(12, Math.floor((container.clientHeight || 320) / gap));
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const dot = document.createElement("i");
      dot.style.animationDelay = `${(r + c) * 55}ms`;
      container.appendChild(dot);
    }
  }
}

export async function submitGenerate(event) {
  event.preventDefault();
  const config = toolConfig(state.tool);
  const prompt = $("prompt").value.trim();
  if (!prompt && !config.promptOptional) {
    setStatus("failed", "请填写提示词。");
    return;
  }
  if (config.refs && state.refs.length < config.min) {
    setStatus("failed", `该工具至少需要 ${config.min} 张参考图。`);
    return;
  }

  const button = $("generate-btn");
  button.disabled = true;
  button.textContent = "生成中…";
  setStatus("running", "生成中，请稍候…");
  $("result-error").classList.add("hidden");
  $("result-meta").classList.add("hidden");
  revokeObjectUrls();
  $("result-grid").innerHTML = "";

  try {
    const response = await apiFetch(`/web/api/generate/${state.tool}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequestBody()),
    });
    const payload = await response.json();
    if (response.ok) {
      await renderResults(payload);
      setStatus("done", "完成。");
    } else {
      setStatus("failed", "生成失败。");
      showResultError(normalizePayloadError(payload, response));
    }
  } catch (error) {
    if (error.message === "unauthorized") {
      setStatus("failed", "需要重新输入令牌。");
    } else if (error instanceof SyntaxError) {
      setStatus("failed", "生成失败。");
      showResultError({
        type: "bad_response",
        message: "响应不是有效的 JSON，服务可能异常，请稍后重试。",
      });
    } else {
      setStatus("failed", "请求失败。");
      showResultError({
        type: "network",
        message: String(error.message || error),
      });
    }
  } finally {
    button.disabled = false;
    button.textContent = "生成图片";
  }
}

function showResultError(error) {
  const box = $("result-error");
  box.textContent = `[${error.type || "error"}] ${error.message || ""}`;
  box.classList.remove("hidden");
}

/* 失败载荷归一：error.message、error_description、状态码三级回退，未知响应
   形态也给出可读信息。 */
function normalizePayloadError(payload, response) {
  const error = payload && payload.error;
  if (error && typeof error === "object" && error.message) return error;
  if (payload && typeof payload.error_description === "string") {
    return { type: error || "error", message: payload.error_description };
  }
  return { type: error || "error", message: `HTTP ${response.status}` };
}

/* 结果渲染：web_path 优先走本服务鉴权图片端点（blob 免令牌入 URL），外链 url
   走裸 fetch 防令牌外送、失败回退 img.src 直连（跨域 img 标签不受 CORS 限制）。
   单张失败只降级该卡片显示占位错误，不中断整批渲染。 */
async function renderResults(payload) {
  const grid = $("result-grid");
  const items = Array.isArray(payload.data) ? payload.data : [];
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "result-card";
    appendCardInfo(card, item);
    if (item.web_path || item.url) {
      const img = document.createElement("img");
      img.alt = item.local_path || item.url || "生成结果";
      card.insertBefore(img, card.firstChild);
      grid.appendChild(card);
      try {
        await loadResultImage(img, item);
      } catch (error) {
        console.error("结果图片加载失败:", error);
        appendCardError(card, "图片加载失败");
      }
    } else {
      grid.appendChild(card);
    }
  }
  if (!items.length)
    grid.appendChild(document.createTextNode("本次没有返回图片。"));

  const meta = $("result-meta");
  const metaLines = [];
  const usage = payload.usage;
  if (usage && usage.completion_tokens != null) {
    metaLines.push(`用量：completion ${usage.completion_tokens}`);
  }
  if (payload.auto_save && Array.isArray(payload.auto_save.results)) {
    metaLines.push(`已保存 ${payload.auto_save.results.length} 张`);
  }
  if (metaLines.length) {
    meta.textContent = metaLines.join("\n");
    meta.classList.remove("hidden");
  }
}

/* 单张结果图装载：仅 web_path 条目可进灯箱并带 zoomable 光标；url-only 条目
   无本地路径，不可回填也不响应点击。 */
async function loadResultImage(img, item) {
  if (item.web_path) {
    const blobUrl = await fetchBlobUrl(
      `/web/api/image?path=${encodeURIComponent(item.web_path)}`,
    );
    if (!blobUrl) throw new Error("image endpoint 请求失败");
    img.src = blobUrl;
    img.classList.add("zoomable");
    img.addEventListener("click", () => openLightbox(item));
    return;
  }
  const objectUrl = await fetchExternalBlobUrl(item.url);
  img.src = objectUrl || item.url;
}

function appendCardInfo(card, item) {
  const info = document.createElement("div");
  info.className = "result-card-info";
  const path = document.createElement("span");
  path.className = "result-path";
  path.textContent = item.local_path || item.url || "";
  path.title = path.textContent;
  info.appendChild(path);
  card.appendChild(info);
}

function appendCardError(card, message) {
  const error = document.createElement("div");
  error.className = "result-card-error";
  error.textContent = message;
  card.appendChild(error);
}
