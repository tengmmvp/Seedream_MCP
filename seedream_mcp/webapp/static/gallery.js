/* 图库域：浏览分页、缩略图网格与灯箱。
   灯箱支持把历史图片回填为参考图。 */

"use strict";

import { $, apiFetch, fetchBlobUrl, revokeObjectUrls, state } from "./api.js";
import { addReference } from "./refs.js";

export const GALLERY_PAGE_SIZE = 60;

/* 缩略图分批并发：整页 60 张串行加载过慢，批大小 6 做受限并发。 */
const THUMBNAIL_BATCH_SIZE = 6;

/* 请求序号守卫：翻页或刷新连点时，仅最后一次请求的响应可落地。 */
let requestSeq = 0;

/* 灯箱当前对象 URL 与原始 blob：独立于全局 objectUrls 生命周期，随开关与
   换图精确回收；blob 保留供「用作参考图」转 data URI。 */
let currentLightboxUrl = null;
let currentLightboxBlob = null;
let lightboxSeq = 0;

function resetGalleryPager() {
  $("gallery-page").textContent = "";
  $("gallery-count").textContent = "";
  $("gallery-prev").disabled = true;
  $("gallery-next").disabled = true;
}

export async function refreshGallery() {
  const seq = ++requestSeq;
  /* 配置未就绪时不渲染任何空态结论，由 main 的补刷在 config-info 落地后重进。 */
  if (!state.configInfo) return;
  if (!state.configInfo.save_root_available) {
    state.gallery.offset = 0;
    state.gallery.hasMore = false;
    state.gallery.items = [];
    $("gallery-grid").innerHTML = "";
    $("gallery-empty").textContent =
      "未配置保存根目录（SEEDREAM_WORKSPACE_ROOT 或 SEEDREAM_AUTO_SAVE_BASE_DIR）";
    $("gallery-empty").classList.remove("hidden");
    resetGalleryPager();
    return;
  }
  const format = $("format-filter").value;
  const body = {
    limit: GALLERY_PAGE_SIZE,
    offset: state.gallery.offset,
    show_details: true,
  };
  if (format) body.format_filter = [format];
  const response = await apiFetch("/web/api/browse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) return;
  const payload = await response.json();
  if (seq !== requestSeq) return;
  /* 页码一律以响应回显的 offset 为准，避免竞态期间本地偏移已被改写。 */
  if (typeof payload.offset === "number") {
    state.gallery.offset = payload.offset;
  }
  state.gallery.hasMore = Boolean(payload.has_more);
  state.gallery.items = payload.images || [];

  revokeObjectUrls();
  const grid = $("gallery-grid");
  grid.innerHTML = "";
  $("gallery-empty").textContent = "保存目录还没有图片。";
  $("gallery-empty").classList.toggle("hidden", state.gallery.items.length > 0);
  const pending = state.gallery.items.map((item) => {
    const figure = document.createElement("figure");
    figure.className = "gallery-item";
    const img = document.createElement("img");
    img.alt = item.path;
    figure.appendChild(img);
    const caption = document.createElement("figcaption");
    const detail = [
      item.size_mb ? `${item.size_mb}MB` : "",
      item.modified || "",
    ]
      .filter(Boolean)
      .join(" · ");
    caption.textContent = `${item.path}${detail ? " — " + detail : ""}`;
    caption.title = item.path;
    figure.appendChild(caption);
    figure.addEventListener("click", () =>
      openLightbox({ web_path: item.path }),
    );
    grid.appendChild(figure);
    return { img, path: item.path };
  });

  for (let i = 0; i < pending.length; i += THUMBNAIL_BATCH_SIZE) {
    if (seq !== requestSeq) return;
    await Promise.all(
      pending.slice(i, i + THUMBNAIL_BATCH_SIZE).map(async (entry) => {
        try {
          const blobUrl = await fetchBlobUrl(
            `/web/api/thumbnail?path=${encodeURIComponent(entry.path)}`,
          );
          if (blobUrl) entry.img.src = blobUrl;
        } catch {
          /* 单张缩略图失败不阻断同批与后续批次 */
        }
      }),
    );
  }

  const count = state.gallery.items.length;
  $("gallery-page").textContent = count
    ? `${state.gallery.offset + 1} – ${state.gallery.offset + count}`
    : "0";
  $("gallery-count").textContent = state.gallery.hasMore ? "（还有更多）" : "";
  $("gallery-prev").disabled = state.gallery.offset === 0;
  $("gallery-next").disabled = !state.gallery.hasMore;
}

export async function openLightbox(item) {
  if (!item || !item.web_path) return;
  const seq = ++lightboxSeq;
  $("lightbox-caption").textContent = item.web_path;
  $("lightbox").classList.remove("hidden");
  releaseLightboxUrl();
  const response = await apiFetch(
    `/web/api/image?path=${encodeURIComponent(item.web_path)}`,
  );
  if (!response.ok) return;
  const blob = await response.blob();
  /* 序号已变：新图已打开或灯箱已关闭，本次结果整包丢弃。 */
  if (seq !== lightboxSeq) return;
  currentLightboxBlob = blob;
  currentLightboxUrl = URL.createObjectURL(blob);
  $("lightbox-img").src = currentLightboxUrl;
}

export function closeLightbox() {
  lightboxSeq++;
  releaseLightboxUrl();
  currentLightboxBlob = null;
  $("lightbox").classList.add("hidden");
  $("lightbox-img").removeAttribute("src");
}

function releaseLightboxUrl() {
  if (currentLightboxUrl) {
    URL.revokeObjectURL(currentLightboxUrl);
    currentLightboxUrl = null;
  }
}

/* 回填参考图：config-info 不再下发保存根绝对路径，前端无从拼本地路径，改用
   灯箱已持有的 blob 转 data URI 作为参考图值。文生图工具自动切到图生图，
   hash 变化由浏览器原生 hashchange 事件驱动视图切换。 */
export async function useLightboxAsReference() {
  const blob = currentLightboxBlob;
  if (!blob || !state.configInfo || !state.configInfo.save_root_available)
    return;
  if (state.tool === "text-to-image") {
    document.querySelector('[data-tool="image-to-image"]').click();
  }
  try {
    const dataUri = await blobToDataUri(blob);
    addReference("data_uri", dataUri, dataUri);
  } catch {
    alert("回填参考图失败，请改用上传或图片 URL。");
    return;
  }
  closeLightbox();
  location.hash = "#/generate";
}

function blobToDataUri(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("FileReader 失败"));
    reader.readAsDataURL(blob);
  });
}
