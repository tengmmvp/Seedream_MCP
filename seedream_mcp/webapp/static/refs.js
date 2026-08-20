/* 参考图域：各工具的数量规则、增删与渲染。
   参考图三来源——上传转 data URI、URL、图库回填的本地绝对路径；
   提示词以「图N」指代列表序号。 */

"use strict";

import { $, state } from "./api.js";

const SINGLE_REF_LIMIT = 1;
const FUSION_REF_MIN = 2;
// data URI 有 4/3 膨胀，累计 45MB 字符给服务端 64MB 请求体上限留余量，
// 防多图融合多张上传触发 413。
const UPLOAD_TOTAL_LIMIT_CHARS = 45 * 1024 * 1024;

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

/* 上传累计校验：对现有 data_uri 参考图按字符数求和，新增后总量超限即拒绝。
   handleFiles 与灯箱回填均经 addReference 汇聚，此处是唯一闸口。 */
function dataUriTotalChars() {
  return state.refs.reduce(
    (sum, ref) => (ref.kind === "data_uri" ? sum + ref.value.length : sum),
    0,
  );
}

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

export function handleFiles(files) {
  for (const file of files) {
    const reader = new FileReader();
    reader.onload = () =>
      addReference("data_uri", reader.result, reader.result);
    reader.readAsDataURL(file);
  }
}
