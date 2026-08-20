/* 图库域：浏览分页、缩略图网格与灯箱。
   灯箱支持把历史图片回填为参考图。 */

"use strict";

import { $, apiFetch, fetchBlobUrl, revokeObjectUrls, state } from "./api.js";
import { addReference } from "./refs.js";

const GALLERY_PAGE_SIZE = 60;

export async function refreshGallery() {
  if (!state.configInfo || !state.configInfo.save_root) {
    $("gallery-empty").classList.remove("hidden");
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
  state.gallery.hasMore = Boolean(payload.has_more);
  state.gallery.items = payload.images || [];

  revokeObjectUrls();
  const grid = $("gallery-grid");
  grid.innerHTML = "";
  $("gallery-empty").classList.toggle("hidden", state.gallery.items.length > 0);
  for (const item of state.gallery.items) {
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
    const blobUrl = await fetchBlobUrl(
      `/web/api/thumbnail?path=${encodeURIComponent(item.path)}`,
    );
    if (blobUrl) img.src = blobUrl;
  }

  const start = state.gallery.offset + 1;
  $("gallery-page").textContent =
    `${start} – ${start + state.gallery.items.length - 1}`;
  $("gallery-count").textContent = state.gallery.hasMore ? "（还有更多）" : "";
  $("gallery-prev").disabled = state.gallery.offset === 0;
  $("gallery-next").disabled = !state.gallery.hasMore;
}

export async function openLightbox(item) {
  if (!item || !item.web_path) return;
  state.lightboxPath = item.web_path;
  $("lightbox-caption").textContent = item.web_path;
  $("lightbox").classList.remove("hidden");
  const url = await fetchBlobUrl(
    `/web/api/image?path=${encodeURIComponent(item.web_path)}`,
  );
  if (url) $("lightbox-img").src = url;
}

export function closeLightbox() {
  $("lightbox").classList.add("hidden");
  $("lightbox-img").removeAttribute("src");
}

/* 回填参考图：save_root 与图库相对路径拼绝对路径；若越出工作区边界由服务端
   拒绝并提示，用户可改走上传。文生图工具自动切到图生图，hash 变化由浏览器
   原生 hashchange 事件驱动视图切换。 */
export function useLightboxAsReference() {
  if (!state.lightboxPath || !state.configInfo || !state.configInfo.save_root)
    return;
  const separator = state.configInfo.save_root.includes("\\") ? "\\" : "/";
  const absolute = `${state.configInfo.save_root}${separator}${state.lightboxPath}`;
  if (state.tool === "text-to-image") {
    document.querySelector('[data-tool="image-to-image"]').click();
  }
  addReference("local", absolute, null);
  closeLightbox();
  location.hash = "#/generate";
}
