/* 参考图域：各工具的数量规则、增删与渲染。
   参考图三来源——上传转 data URI、URL、图库回填的本地绝对路径；
   提示词以「图N」指代列表序号。 */

"use strict";

import { $, state } from "./api.js";

const SINGLE_REF_LIMIT = 1;
const FUSION_REF_MIN = 2;
// data URI 有 4/3 膨胀，40MB 上传留出服务端 64MB 与单图 30MB 解码上限的余量。
const UPLOAD_SOFT_LIMIT_BYTES = 40 * 1024 * 1024;

/* 声明各工具的参考图数量区间与提示词必填性；上限随当前模型能力收缩。 */
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

export function addReference(kind, value, preview) {
  const config = toolConfig(state.tool);
  if (state.refs.length >= config.max) {
    alert(`该工具最多 ${config.max} 张参考图`);
    return;
  }
  state.refs.push({ kind, value, preview: preview || null });
  renderReferences();
}

export function handleFiles(files) {
  for (const file of files) {
    if (file.size > UPLOAD_SOFT_LIMIT_BYTES) {
      alert(`「${file.name}」超过 40MB，请改用图片 URL`);
      continue;
    }
    const reader = new FileReader();
    reader.onload = () =>
      addReference("data_uri", reader.result, reader.result);
    reader.readAsDataURL(file);
  }
}
