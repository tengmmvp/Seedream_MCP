/**
 * @fileoverview 生成台域：配置加载、工具切换的表单形态、请求组装与结果渲染。
 * 表单字段名与后端 schemas.py 的 *Input 模型一一对应，新增参数须两侧同步。
 */

"use strict";

import {
  $,
  apiFetch,
  currentModel,
  fetchBlobUrl,
  fetchExternalBlobUrl,
  normalizePayloadError,
  revokeObjectUrls,
  setActiveTool,
  showInlineError,
  state,
} from "./api.js";
import { renderReferences, toolConfig, withinUploadBudget } from "./refs.js";
import { openLightbox } from "./gallery.js";

/**
 * config-info 是启动面：填充尺寸档位与能力开关；模型跟随服务器配置无下拉。
 *
 * @throws {Error} 请求失败时上抛，由调用方走令牌门或错误文案。
 */
export async function loadConfigInfo() {
  const response = await apiFetch("/web/api/config-info");
  if (!response.ok) throw new Error("config-info 请求失败");
  const info = await response.json();
  state.configInfo = info;

  const current = currentModel();
  $("server-meta").textContent = current
    ? `${current.display_name} · ${info.default_size} 默认`
    : info.model_id;

  const sizeSelect = $("size");
  // 令牌重输等场景二次加载时保留先前选择：自定义保持，档位在新列表中仍存在则恢复。
  const previous = sizeSelect.value;
  sizeSelect.innerHTML = "";
  // 未知模型（Endpoint ID 部署）回退档位取 unknown 家族声明，与后端放行同源。
  const presets = current
    ? current.allowed_presets
    : Array.isArray(info.fallback_presets)
      ? info.fallback_presets
      : [];
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
  if (previous === "custom" || presets.includes(previous)) {
    sizeSelect.value = previous;
  }

  // 格式过滤器选项从 config-info 派生，与后端支持清单单一来源；先前选择在新列表
  // 中仍存在则恢复，恢复失败回「全部」并同步重置图库偏移，页码与过滤语义不错位。
  const formatSelect = $("format-filter");
  if (formatSelect && Array.isArray(info.supported_extensions)) {
    const previousFormat = formatSelect.value;
    formatSelect.innerHTML = "";
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "全部";
    formatSelect.appendChild(all);
    for (const ext of info.supported_extensions) {
      const option = document.createElement("option");
      option.value = ext;
      option.textContent = ext.replace(".", "").toUpperCase();
      formatSelect.appendChild(option);
    }
    if (previousFormat && info.supported_extensions.includes(previousFormat)) {
      formatSelect.value = previousFormat;
    } else if (previousFormat) {
      state.gallery.offset = 0;
    }
  }

  // 数量上限与参考图文件类型从 config-info 派生，与后端约束单一来源。
  if (Number.isFinite(info.max_request_count))
    $("request-count").max = String(info.max_request_count);
  if (Number.isFinite(info.max_images))
    $("max-images").max = String(info.max_images);
  if (Array.isArray(info.supported_extensions))
    $("ref-file").accept = info.supported_extensions.join(",");

  // 输出格式字段随模型能力显隐，未知模型按 unknown 家族放行，二次加载能力翻转
  // 可双向切换。
  $("output-format")
    .closest(".field")
    .classList.toggle("collapsed", current ? !current.supports_output_format : false);

  // 勾选框初始态反映服务器默认，此后保留用户选择：令牌重输会再次加载配置，
  // 重置勾选态会使静默回退变为显式传值的实际生效。
  const watermarkBox = $("watermark");
  if (!watermarkBox.dataset.initialized) {
    watermarkBox.checked = info.default_watermark === true;
    watermarkBox.dataset.initialized = "1";
  }

  // 服务端全局关闭自动保存时禁用复选框并标注，UI 与实际行为一致。
  const autoSave = $("auto-save");
  if (info.auto_save_enabled === false) {
    autoSave.disabled = true;
    autoSave.checked = false;
    $("auto-save-note").classList.remove("hidden");
  } else {
    autoSave.disabled = false;
    $("auto-save-note").classList.add("hidden");
  }

  updateToolAvailability();
}

/**
 * 当前模型能力下各表单区的显隐与提示词必填态；提示词可留空的说明只在对应
 * 能力真实可用时提及，避免误导用户寻找不存在的开关。
 */
export function applyToolUI() {
  const config = toolConfig(state.tool);
  const current = currentModel();
  // 未知模型（Endpoint ID 部署）无能力条目时回退为允许，与 unknown 家族放行一致。
  const layerAllowed =
    state.tool === "image-to-image" &&
    (current ? current.supports_layer_decomposition : true);

  $("reference-section").classList.toggle("collapsed", !config.refs);
  // 提示词留空仅图层拆分场景被后端接受，说明只在开关真实可用时提及。
  $("prompt-hint").textContent =
    config.promptOptional && layerAllowed
      ? "启用图层拆分时可留空；建议不超过 300 字。"
      : "建议不超过 300 字。";
  $("max-images-field").classList.toggle(
    "collapsed",
    state.tool !== "sequential-generation",
  );
  const layerField = $("layer-field");
  layerField.classList.toggle("collapsed", !layerAllowed);
  if (!layerAllowed) $("layer-decomposition").checked = false;
  // 上限收缩时参考图暂存而非丢弃，切回支持的工具按上限自动恢复原顺序；
  // 恢复与入列同受 data URI 累计上限约束，超限项留在暂存不丢。
  while (state.refs.length > config.max) {
    state.parkedRefs.push(state.refs.pop());
  }
  while (state.parkedRefs.length > 0 && state.refs.length < config.max) {
    const next = state.parkedRefs[state.parkedRefs.length - 1];
    if (next.kind === "data_uri" && !withinUploadBudget(next.value.length)) {
      break;
    }
    state.refs.push(state.parkedRefs.pop());
  }
  renderReferences();
}

/**
 * 按模型能力启停工具 tab：不支持组图的模型禁用组图入口，当前工具被禁时
 * 回落文生图。
 */
export function updateToolAvailability() {
  const current = currentModel();
  const sequentialAllowed = current
    ? current.supports_sequential_generation
    : true;
  const sequentialTab = document.querySelector(
    '[data-tool="sequential-generation"]',
  );
  sequentialTab.disabled = !sequentialAllowed;
  sequentialTab.title = sequentialAllowed ? "" : "当前模型不支持组图生成";
  if (state.tool === "sequential-generation" && !sequentialAllowed) {
    setActiveTool("text-to-image");
    applyToolUI();
  }
}

/**
 * 从表单状态组装生成请求体。
 *
 * @returns {Object} 请求体对象。
 */
export function buildRequestBody() {
  const config = toolConfig(state.tool);
  // 空提示词整体省略键：后端仅接受键缺省形态，空串会被 min_length 拒绝。
  const promptText = $("prompt").value.trim();
  const body = promptText ? { prompt: promptText } : {};

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
  body.watermark = $("watermark").checked;

  if (state.tool === "sequential-generation") {
    // 留空省略键，由后端按参考图数量推导。
    body.max_images = Number($("max-images").value) || undefined;
  }
  if (
    !$("layer-field").classList.contains("collapsed") &&
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

// 点阵退场计时器：fade-out 过渡结束后收起容器并清空点阵。
let loadingHideTimer = 0;

/**
 * 状态行与等待动画的联动：running 时文案后带呼吸点，等待区铺对角波点阵；
 * 结束态点阵淡出且高度同步收缩，结果区连续上移不跳位。
 *
 * @param {string} kind - 状态类别，取 running / done / failed。
 * @param {string} text - 状态文案。
 */
export function setStatus(kind, text) {
  const status = $("result-status");
  status.className = `result-status ${kind}`;
  status.textContent = "";
  status.appendChild(document.createTextNode(text));
  if (kind === "running") {
    const dots = document.createElement("span");
    dots.className = "loading-dots";
    dots.appendChild(document.createElement("i"));
    dots.appendChild(document.createElement("i"));
    dots.appendChild(document.createElement("i"));
    status.appendChild(dots);
  }
  const loading = $("result-loading");
  if (kind === "running") {
    clearTimeout(loadingHideTimer);
    loading.classList.remove("hidden", "fade-out");
    buildRenderDots();
    return;
  }
  // reduced-motion 下无过渡可等，直接收起；否则给淡出 360ms 走完再清理。
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    loading.classList.add("hidden");
    $("render-dots").innerHTML = "";
    return;
  }
  loading.classList.add("fade-out");
  loadingHideTimer = setTimeout(() => {
    loading.classList.add("hidden");
    loading.classList.remove("fade-out");
    $("render-dots").innerHTML = "";
  }, 400);
}

// 渲染点阵：按容器尺寸铺点，点亮延迟随行列递增，形成左上到右下扫过的
// 对角波。行列均封顶防宽高视口铺出上千个并发动画节点。须在容器可见后调用，
// 隐藏态取不到宽度。
const RENDER_DOTS_MAX_COLS = 48;
const RENDER_DOTS_MAX_ROWS = 24;

function buildRenderDots() {
  const container = $("render-dots");
  container.innerHTML = "";
  const gap = 22;
  const cols = Math.min(
    RENDER_DOTS_MAX_COLS,
    Math.max(8, Math.floor((container.clientWidth || 600) / gap)),
  );
  const rows = Math.min(
    RENDER_DOTS_MAX_ROWS,
    Math.max(12, Math.floor((container.clientHeight || 320) / gap)),
  );
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const dot = document.createElement("i");
      dot.style.animationDelay = `${(r + c) * 55}ms`;
      container.appendChild(dot);
    }
  }
}

/**
 * 生成表单提交处理器：前端校验后请求生成端点并渲染结果或错误。
 *
 * @param {Event} event - submit 事件。
 */
export async function submitGenerate(event) {
  event.preventDefault();
  const config = toolConfig(state.tool);
  const prompt = $("prompt").value.trim();
  // 空提示词仅图生图勾选图层拆分时被后端接受，判定与提示文案口径一致。
  const layerChecked =
    !$("layer-field").classList.contains("collapsed") &&
    $("layer-decomposition").checked;
  if (!prompt && !(config.promptOptional && layerChecked)) {
    setStatus("failed", "请填写提示词。");
    return;
  }
  if (config.refs && state.refs.length < config.min) {
    setStatus("failed", `该工具至少需要 ${config.min} 张参考图。`);
    return;
  }
  if ($("size").value === "custom") {
    const width = Number($("size-width").value);
    const height = Number($("size-height").value);
    if (!width || !height) {
      setStatus("failed", "自定义尺寸需同时填写宽与高。");
      return;
    }
  }

  const button = $("generate-btn");
  button.disabled = true;
  button.textContent = "生成中…";
  setStatus("running", "生成中，请稍候");
  $("result-error").classList.add("hidden");
  $("result-meta").classList.add("hidden");
  revokeObjectUrls("generate");
  $("result-grid").innerHTML = "";

  try {
    // 上限 20 分钟覆盖真实生成的最坏量级；服务端总预算含重试与排队，到点中止时
    // 服务端可能仍在执行，超时分支如实提示去图库核对而非直接重试。旧浏览器无
    // AbortSignal.timeout 时不设超时，退化为无上限等待。
    const timeoutSignal =
      typeof AbortSignal.timeout === "function"
        ? AbortSignal.timeout(1200000)
        : undefined;
    const response = await apiFetch(`/web/api/generate/${state.tool}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequestBody()),
      signal: timeoutSignal,
    });
    const payload = await response.json();
    if (response.ok) {
      const { failedCount, total } = await renderResults(payload);
      if (failedCount === total && total > 0) {
        setStatus("failed", "生成失败，全部请求未成功，原因见卡片。");
      } else if (failedCount) {
        setStatus("failed", `部分完成：${total - failedCount}/${total} 成功，失败原因见卡片。`);
      } else {
        setStatus("done", "完成。");
      }
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
    } else if (error.name === "TimeoutError") {
      setStatus("failed", "生成耗时异常。");
      showResultError({
        type: "timeout",
        message:
          "本次生成已超过 20 分钟，请求已中止。服务端可能仍在后台执行，请稍后到图库查看结果，勿立即重试以免重复生成。",
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
  showInlineError($("result-error"), `[${error.type || "error"}] ${error.message || ""}`);
}

// 结果图装载并发上限：固定 4 个取图任务持续消费队列，无批次屏障。
const RESULT_IMAGE_CONCURRENCY = 4;

// 结果渲染：web_path 优先走本服务鉴权图片端点，blob 装载使令牌只进请求头
// 不进 URL；外链 url 走裸 fetch 防令牌外送，失败回退 img.src 直连，跨域
// img 标签不受 CORS 限制。卡片先全部落地再分批装载，单张失败只降级该卡片
// 显示占位错误，不中断整批渲染。
async function renderResults(payload) {
  const grid = $("result-grid");
  const items = Array.isArray(payload.data) ? payload.data : [];
  const pending = [];
  let failedCount = 0;
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "result-card";
    // 批次内失败占位项展示原因。
    if (item.type === "image_generation.request_failed") {
      failedCount += 1;
      const reason = item.error && item.error.message ? item.error.message : "未知错误";
      appendCardError(card, `该请求失败：${reason}`);
      grid.appendChild(card);
      continue;
    }
    appendCardInfo(card, item);
    if (item.web_path || item.url) {
      const img = document.createElement("img");
      img.alt = item.web_path || item.url || "生成结果";
      img.classList.add("developing");
      img.addEventListener("load", () => img.classList.add("loaded"));
      // 外链直连失败时退出显影态，显示裂图而非透明空块。
      img.addEventListener("error", () => img.classList.remove("developing"));
      card.insertBefore(img, card.firstChild);
      grid.appendChild(card);
      pending.push({ card, img, item });
    } else {
      grid.appendChild(card);
    }
  }
  if (!items.length)
    grid.appendChild(document.createTextNode("本次没有返回图片。"));

  let cursor = 0;
  const workers = Array.from({ length: Math.min(RESULT_IMAGE_CONCURRENCY, pending.length) }, async () => {
    while (cursor < pending.length) {
      const entry = pending[cursor++];
      try {
        await loadResultImage(entry.img, entry.item);
      } catch (error) {
        entry.img.classList.remove("developing");
        if (error.message === "unauthorized") {
          // 401 已弹令牌门；卡片标注重试方式，不误报为加载失败。
          appendCardError(entry.card, "令牌已过期，重新输入后请再次生成");
        } else {
          console.error("结果图片加载失败:", error);
          appendCardError(entry.card, "图片加载失败");
        }
      }
    }
  });
  await Promise.all(workers);

  const meta = $("result-meta");
  const metaLines = [];
  const usage = payload.usage;
  // usage 键名对齐官方 API 的 output_tokens/total_tokens。
  if (usage && Number.isFinite(usage.output_tokens)) {
    metaLines.push(`用量：输出 ${usage.output_tokens} tokens`);
  }
  if (usage && Number.isFinite(usage.total_tokens)) {
    metaLines.push(`总计 ${usage.total_tokens} tokens`);
  }
  // 保存阶段整体降级（磁盘满、目录不可写）时 results 为空，仅展示原因，不误报
  // 「已保存 0 张」；图片 URL 过期后不可再取回，用户需据此手动补救。
  if (payload.auto_save && payload.auto_save.error) {
    metaLines.push(`自动保存失败：${payload.auto_save.error}`);
  } else if (payload.auto_save && Array.isArray(payload.auto_save.results)) {
    const results = payload.auto_save.results;
    const savedCount = results.filter((r) => r && r.success !== false).length;
    // 部分失败时展示 N/总数，用户可察觉有保存失败的条目。
    metaLines.push(
      savedCount === results.length
        ? `已保存 ${savedCount} 张`
        : `已保存 ${savedCount}/${results.length} 张`,
    );
  }
  if (metaLines.length) {
    meta.textContent = metaLines.join("\n");
    meta.classList.remove("hidden");
  }
  return { failedCount, total: items.length };
}

// 单张结果图装载：卡片走缩略图端点与图库网格同口径，原图由灯箱按需加载；
// 仅 web_path 条目可进灯箱并带 zoomable 光标，url-only 条目不可回填也不响应点击。
async function loadResultImage(img, item) {
  if (item.web_path) {
    const blobUrl = await fetchBlobUrl(
      `/web/api/thumbnail?path=${encodeURIComponent(item.web_path)}`,
      "generate",
    );
    if (!blobUrl) throw new Error("thumbnail endpoint 请求失败");
    img.src = blobUrl;
    img.classList.add("zoomable");
    // 失败提示落生成视图的错误节点，图库侧节点在此视图不可见。
    img.addEventListener("click", () => openLightbox(item, $("result-error")));
    return;
  }
  const objectUrl = await fetchExternalBlobUrl(item.url, "generate");
  img.src = objectUrl || item.url;
}

function appendCardInfo(card, item) {
  const info = document.createElement("div");
  info.className = "result-card-info";
  const path = document.createElement("span");
  path.className = "result-path";
  // 结果路径展示取 web_path 相对形态，服务器绝对路径不出现在前端。
  path.textContent = item.web_path || item.url || "";
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
