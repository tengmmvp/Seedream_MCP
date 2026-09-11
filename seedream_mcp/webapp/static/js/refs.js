/**
 * @fileoverview 参考图域：各工具的数量规则、增删与渲染。参考图三来源——
 * 上传转 data URI、手输 URL、图库灯箱回填转 data URI；提示词以「图N」指代
 * 列表序号。
 */

"use strict";

import { $, clearInlineError, currentModel, showInlineError, state } from "./api.js";

const SINGLE_REF_LIMIT = 1;
const FUSION_REF_MIN = 2;
// config-info 未加载时的回退默认：上限 14 与 unknown 家族一致，预算 45MB 为
// 64MB 请求体上限的推导值；加载后以服务端下发值为单一来源。
const DEFAULT_UPLOAD_BUDGET_CHARS = 45 * 1024 * 1024;
const DEFAULT_UNKNOWN_REF_LIMIT = 14;

// 预算为 0 时部署无法接受任何 data URI 上传。
const ZERO_BUDGET_REASON =
  "当前部署请求体上限过低，无法上传参考图，请改用图片 URL";

// data URI 累计字符预算，来自服务端按请求体上限的推导；0 为有效预算，
// 表示该上限容纳不下任何 data URI。
function uploadBudgetChars() {
  const fromServer = state.configInfo && state.configInfo.upload_budget_chars;
  return Number.isFinite(fromServer) && fromServer >= 0
    ? fromServer
    : DEFAULT_UPLOAD_BUDGET_CHARS;
}

// 未知模型（Endpoint ID 部署）的参考图上限，来自服务端 unknown 家族能力声明。
function unknownRefLimit() {
  const fromServer = state.configInfo && state.configInfo.unknown_max_reference_images;
  return typeof fromServer === "number" && fromServer > 0
    ? fromServer
    : DEFAULT_UNKNOWN_REF_LIMIT;
}

/**
 * 声明各工具的参考图数量区间与提示词必填性；上限随当前模型能力收缩。
 *
 * @param {string} tool - 工具标识。
 * @returns {Object} 形如 {refs, min, max, promptOptional} 的配置。
 */
export function toolConfig(tool) {
  const current = currentModel();
  const refLimit = current
    ? current.max_reference_images
    : unknownRefLimit();
  if (tool === "image-to-image")
    return {
      refs: true,
      min: SINGLE_REF_LIMIT,
      max: SINGLE_REF_LIMIT,
      promptOptional: true,
    };
  if (tool === "multi-image-fusion")
    return {
      refs: true,
      min: FUSION_REF_MIN,
      max: refLimit,
      promptOptional: false,
    };
  if (tool === "sequential-generation")
    return { refs: true, min: 0, max: refLimit, promptOptional: false };
  return { refs: false, min: 0, max: 0, promptOptional: false };
}

/**
 * 按当前工具配置重渲染参考图列表与计数。
 *
 * @param {number} [enterIndex=-1] - 播放入场动画的条目序号，默认不播。
 */
export function renderReferences(enterIndex = -1) {
  const config = toolConfig(state.tool);
  const list = $("reference-list");
  list.innerHTML = "";
  state.refs.forEach((ref, index) => {
    const item = document.createElement("div");
    item.className = "reference-item";
    if (index === enterIndex) item.classList.add("enter");
    const badge = document.createElement("span");
    badge.className = "ref-badge";
    badge.textContent = String(index + 1);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ref-remove";
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      state.refs.splice(index, 1);
      renderReferences();
    });
    item.appendChild(remove);
    item.appendChild(badge);
    if (ref.preview) {
      const img = document.createElement("img");
      img.src = ref.preview;
      img.alt = `参考图 ${index + 1}`;
      item.appendChild(img);
    } else {
      const label = document.createElement("span");
      label.className = "ref-path mono";
      label.textContent =
        ref.value.length > 18 ? ref.value.slice(0, 18) + "…" : ref.value;
      item.appendChild(label);
    }
    list.appendChild(item);
  });
  $("ref-count-note").textContent = `${state.refs.length} / ${config.max}`;
}

// 现有 data_uri 参考图的累计字符数。
function dataUriTotalChars() {
  return state.refs.reduce(
    (sum, ref) => (ref.kind === "data_uri" ? sum + ref.value.length : sum),
    0,
  );
}

/** 追加 nextChars 字符后是否仍在 data URI 累计上限内。 */
export function withinUploadBudget(nextChars) {
  return dataUriTotalChars() + nextChars <= uploadBudgetChars();
}

// 参考图区行内错误提示：拒绝原因落在表单内，替代阻塞式弹窗。
function showRefError(message) {
  showInlineError($("ref-error"), message);
}

function clearRefError() {
  clearInlineError($("ref-error"));
}

/**
 * 添加一张参考图并重渲染；超数量上限或 data URI 累计超限时行内提示拒绝。
 * 上传、灯箱回填与 URL 手输均经此汇聚受校验。
 *
 * @param {string} kind - 来源类型，取 data_uri 或 url。
 * @param {string} value - 参考图值，data URI 或图片 URL。
 * @param {string|null} [preview] - 预览地址。
 * @returns {string|null} 成功入列返回 null，被拒绝返回拒绝原因。
 */
export function addReference(kind, value, preview) {
  const config = toolConfig(state.tool);
  if (state.refs.length >= config.max) {
    const reason = `该工具最多 ${config.max} 张参考图`;
    showRefError(reason);
    return reason;
  }
  // URL 来源前置校验 scheme，非 http(s) 开头的输入在入列前拦下。
  if (kind === "url" && !/^https?:\/\//i.test(value)) {
    const reason = "图片 URL 须以 http:// 或 https:// 开头";
    showRefError(reason);
    return reason;
  }
  if (kind === "data_uri" && !withinUploadBudget(value.length)) {
    const budget = uploadBudgetChars();
    if (budget <= 0) {
      showRefError(ZERO_BUDGET_REASON);
      return ZERO_BUDGET_REASON;
    }
    const budgetText =
      budget >= 1024 * 1024
        ? `${Math.floor(budget / (1024 * 1024))}MB`
        : `${Math.floor(budget / 1024)}KB`;
    const reason = `参考图总量超过 ${budgetText} 上限，请改用图片 URL`;
    showRefError(reason);
    return reason;
  }
  clearRefError();
  state.refs.push({ kind, value, preview: preview || null });
  renderReferences(state.refs.length - 1);
  return null;
}

/**
 * 并行读取文件为 data URI，按选择顺序加入参考图以稳定「图N」编号。
 *
 * 读取前按类型与体积前置拦截，避免大文件白付全量读取；精确判定由
 * addReference 兜底，拒绝原因聚合为一条提示。
 *
 * @param {FileList} files - 待读取的文件列表。
 */
export async function handleFiles(files) {
  if (uploadBudgetChars() <= 0) {
    showRefError(ZERO_BUDGET_REASON);
    return;
  }
  const rejected = [];
  let batchChars = dataUriTotalChars();
  const reads = [];
  for (const file of files) {
    if (file.type && !file.type.startsWith("image/")) {
      rejected.push(`${file.name}（非图片）`);
      continue;
    }
    // data URI 有 4/3 膨胀，加 64 字节头与填充余量。
    const projected = Math.ceil((file.size * 4) / 3) + 64;
    if (batchChars + projected > uploadBudgetChars()) {
      rejected.push(`${file.name}（超出参考图总量上限）`);
      continue;
    }
    batchChars += projected;
    reads.push(
      new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () =>
          resolve({ name: file.name, value: reader.result });
        reader.onerror = () => {
          rejected.push(`文件读取失败: ${file.name}`);
          resolve(null);
        };
        reader.readAsDataURL(file);
      }),
    );
  }
  for (const entry of await Promise.all(reads)) {
    if (entry === null) continue;
    const reason = addReference("data_uri", entry.value, entry.value);
    if (reason) rejected.push(`${entry.name}（${reason}）`);
  }
  if (rejected.length) showRefError(`未添加：${rejected.join("、")}`);
}
