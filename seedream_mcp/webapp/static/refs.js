/**
 * @fileoverview 参考图域：各工具的数量规则、增删与渲染。参考图三来源——
 * 上传转 data URI、手输 URL、图库灯箱回填转 data URI；提示词以「图N」指代
 * 列表序号。
 */

"use strict";

import { $, state } from "./api.js";

const SINGLE_REF_LIMIT = 1;
const FUSION_REF_MIN = 2;
// data URI 有 4/3 膨胀，累计 45MB 字符给服务端 64MB 请求体上限留余量，
// 防多图融合多张上传触发 413。
const UPLOAD_TOTAL_LIMIT_CHARS = 45 * 1024 * 1024;

/**
 * 声明各工具的参考图数量区间与提示词必填性；上限随当前模型能力收缩。
 *
 * @param {string} tool - 工具标识。
 * @returns {Object} 形如 {refs, min, max, promptOptional} 的配置。
 */
export function toolConfig(tool) {
  const current = state.configInfo
    ? (state.configInfo.models || []).find(
        (m) => m.model_id === state.configInfo.model_id,
      )
    : null;
  const refLimit = current ? current.max_reference_images : 10;
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

/** 按当前工具配置重渲染参考图列表与计数。 */
export function renderReferences() {
  const config = toolConfig(state.tool);
  const list = $("reference-list");
  list.innerHTML = "";
  state.refs.forEach((ref, index) => {
    const item = document.createElement("div");
    item.className = "reference-item";
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
      label.textContent = ref.value.slice(0, 18) + "…";
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

/**
 * 添加一张参考图并重渲染；超数量上限或 data URI 累计超限时弹窗拒绝。
 * handleFiles 与灯箱回填均经此汇聚，是上传累计校验的唯一闸口。
 *
 * @param {string} kind - 来源类型，取 data_uri 或 url。
 * @param {string} value - 参考图值，data URI 或图片 URL。
 * @param {string|null} [preview] - 预览地址。
 */
export function addReference(kind, value, preview) {
  const config = toolConfig(state.tool);
  if (state.refs.length >= config.max) {
    alert(`该工具最多 ${config.max} 张参考图`);
    return;
  }
  if (
    kind === "data_uri" &&
    dataUriTotalChars() + value.length > UPLOAD_TOTAL_LIMIT_CHARS
  ) {
    alert("参考图总量超过 45MB 上限，请改用图片 URL");
    return;
  }
  state.refs.push({ kind, value, preview: preview || null });
  renderReferences();
}

/**
 * 逐个读取文件为 data URI 并加入参考图。
 *
 * @param {FileList} files - 待读取的文件列表。
 */
export function handleFiles(files) {
  for (const file of files) {
    const reader = new FileReader();
    reader.onload = () =>
      addReference("data_uri", reader.result, reader.result);
    reader.readAsDataURL(file);
  }
}
