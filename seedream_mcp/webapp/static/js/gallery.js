/**
 * @fileoverview 图库域：浏览分页、缩略图网格与灯箱。灯箱支持把历史图片回填
 * 为参考图。
 */

"use strict";

import {
  $,
  apiFetch,
  clearInlineError,
  fetchBlobUrl,
  normalizePayloadError,
  parseJsonLoose,
  revokeObjectUrls,
  showInlineError,
  state,
} from "./api.js";
import { addReference, toolConfig } from "./refs.js";

/** 图库单页条数。 */
export const GALLERY_PAGE_SIZE = 60;

// 缩略图分批并发：整页 60 张串行加载过慢，批大小 6 做受限并发。
const THUMBNAIL_BATCH_SIZE = 6;

// 请求序号守卫：翻页或刷新连点时，仅最后一次请求的响应可落地。
let requestSeq = 0;

// 灯箱当前对象 URL 与原始 blob：独立于全局 objectUrls 生命周期，随开关与
// 换图精确回收；blob 保留供「用作参考图」转 data URI；序号守卫丢弃换图或
// 关闭后到达的过期响应。
let currentLightboxUrl = null;
let currentLightboxBlob = null;
let lightboxSeq = 0;

function resetGalleryPager() {
  $("gallery-page").textContent = "";
  $("gallery-count").textContent = "";
  $("gallery-prev").disabled = true;
  $("gallery-next").disabled = true;
}

// 图库区错误提示：落视图顶部的专用错误节点（空态文案位在满页时位于首屏外）；
// 失败同时复位翻页器，避免旧可用态把用户引向空页。
function showGalleryError(message) {
  showInlineError($("gallery-error"), message);
  resetGalleryPager();
}

/**
 * 按当前偏移与格式过滤请求图库并渲染缩略图网格与翻页器；连点由请求序号
 * 守卫收敛到最后一次，刷新按钮在请求期间保持 busy 旋转。
 */
export async function refreshGallery() {
  const seq = ++requestSeq;
  $("gallery-refresh").classList.add("busy");
  // 请求期间禁用翻页按钮，防止响应返回前连点叠加 offset 跳页；成功路径按
  // 新状态恢复，失败路径由 resetGalleryPager 维持禁用。
  $("gallery-prev").disabled = true;
  $("gallery-next").disabled = true;
  try {
    await refreshGalleryForSeq(seq);
  } finally {
    // 仅最新请求有权摘除 busy，旧请求的收尾不得打断新请求的旋转。
    if (seq === requestSeq) $("gallery-refresh").classList.remove("busy");
  }
}

// 请求主体：seq 为本次请求序号，过期响应在内部各检查点丢弃。
async function refreshGalleryForSeq(seq) {
  // 配置未就绪时提示刷新并复位翻页器，main 的补刷在 config-info 落地后重进。
  if (!state.configInfo) {
    $("gallery-empty").textContent = "配置未加载，请刷新页面重试。";
    $("gallery-empty").classList.remove("hidden");
    resetGalleryPager();
    return;
  }
  if (!state.configInfo.images_root_available) {
    state.gallery.offset = 0;
    state.gallery.hasMore = false;
    state.gallery.items = [];
    $("gallery-grid").innerHTML = "";
    $("gallery-empty").textContent = state.configInfo.images_root_hint || "";
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
  let response = null;
  try {
    response = await apiFetch("/web/api/browse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    // 401 已弹令牌门，令牌补齐后由提交回调补刷；其余网络异常落图库错误提示。
    if (error.message === "unauthorized") return;
    if (seq === requestSeq) showGalleryError("图库加载失败，请稍后重试。");
    return;
  }
  if (!response.ok) {
    if (seq !== requestSeq) return;
    // 结构化错误优先展示后端消息：offset 越界等可指导用户刷新，未知形态回退
    // HTTP 状态码文案。
    const payload = await parseJsonLoose(response);
    if (seq !== requestSeq) return;
    showGalleryError(`图库加载失败：${normalizePayloadError(payload, response).message}`);
    return;
  }
  const payload = await parseJsonLoose(response);
  if (payload === null) {
    // 响应体非 JSON 或中途截断，按网络失败同口径提示。
    if (seq === requestSeq) showGalleryError("图库响应异常，请稍后重试。");
    return;
  }
  if (seq !== requestSeq) return;
  // 新一页装载成功，清除上一轮遗留的加载失败提示。
  clearInlineError($("gallery-error"));
  // 页码一律以响应回显的 offset 为准，避免竞态期间本地偏移已被改写。
  if (typeof payload.offset === "number") {
    state.gallery.offset = payload.offset;
  }
  state.gallery.hasMore = Boolean(payload.has_more);
  // 越界条目已在服务端剔除，前端条目均带 web_path。
  state.gallery.items = payload.images || [];

  // 只回收图库缩略图池，生成台结果图的 blob URL 不受翻页与刷新波及。
  revokeObjectUrls("gallery");
  const grid = $("gallery-grid");
  grid.innerHTML = "";
  $("gallery-empty").textContent = "保存目录还没有图片。";
  $("gallery-empty").classList.toggle("hidden", state.gallery.items.length > 0);
  const pending = state.gallery.items.map((item) => {
    const figure = document.createElement("figure");
    figure.className = "gallery-item";
    const img = document.createElement("img");
    img.alt = item.path;
    img.classList.add("developing");
    img.addEventListener("load", () => img.classList.add("loaded"));
    figure.appendChild(img);
    const caption = document.createElement("figcaption");
    const detail = [
      item.size_mb ? `${item.size_mb.toFixed(2)}MB` : "",
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

  const markUnavailable = (entry) => {
    entry.img.classList.add("thumb-error");
    entry.img.alt = "缩略图不可用";
  };

  // 固定 worker 消费队列，序号逐项守卫，过期请求的 blob URL 就地释放。
  let cursor = 0;
  const workers = Array.from(
    { length: Math.min(THUMBNAIL_BATCH_SIZE, pending.length) },
    async () => {
      while (cursor < pending.length) {
        if (seq !== requestSeq) return;
        const entry = pending[cursor++];
        try {
          const blobUrl = await fetchBlobUrl(
            `/web/api/thumbnail?path=${encodeURIComponent(entry.path)}`,
            "gallery",
          );
          if (seq !== requestSeq) {
            if (blobUrl) URL.revokeObjectURL(blobUrl);
            return;
          }
          if (blobUrl) entry.img.src = blobUrl;
          else markUnavailable(entry);
        } catch {
          markUnavailable(entry);
        }
      }
    },
  );
  await Promise.all(workers);
  // 过期请求不执行收尾：翻页器写回按新请求的状态由其自身完成。
  if (seq !== requestSeq) return;

  const count = state.gallery.items.length;
  $("gallery-page").textContent = count
    ? `${state.gallery.offset + 1} – ${state.gallery.offset + count}`
    : "0";
  $("gallery-count").textContent = state.gallery.hasMore
    ? "（还有更多）"
    : typeof payload.total_count === "number"
      ? `（共 ${payload.total_count} 张）`
      : "";
  $("gallery-prev").disabled = state.gallery.offset === 0;
  $("gallery-next").disabled = !state.gallery.hasMore;
}

/**
 * 打开灯箱并装载原图。原图 blob 就绪后才显示灯箱，入场一次到位，避免
 * 先开空框再被图片撑开造成的尺寸突变；序号守卫丢弃过期响应。
 *
 * @param {Object} item - 目标条目，仅使用 web_path 字段。
 * @param {HTMLElement} [errorEl=$("gallery-error")] - 失败提示落点，须在调用方
 *   当前可见的视图内；生成台复用本函数时传生成视图的错误节点。
 */
export async function openLightbox(item, errorEl = $("gallery-error")) {
  if (!item || !item.web_path) return;
  const seq = ++lightboxSeq;
  releaseLightboxUrl();
  let response = null;
  try {
    response = await apiFetch(
      `/web/api/image?path=${encodeURIComponent(item.web_path)}`,
    );
  } catch (error) {
    // 401 已弹令牌门；其余网络异常不开灯箱，缩略图仍在，可再次点击重试。
    if (error.message === "unauthorized") return;
    if (seq !== lightboxSeq) return;
    showInlineError(errorEl, "原图加载失败，请重试。");
    return;
  }
  if (!response.ok) {
    console.error("原图加载失败:", item.web_path);
    if (seq !== lightboxSeq) return;
    showInlineError(errorEl, "原图加载失败，图片可能已被清理。");
    return;
  }
  let blob = null;
  try {
    blob = await response.blob();
  } catch {
    if (seq !== lightboxSeq) return;
    showInlineError(errorEl, "原图加载失败，请重试。");
    return;
  }
  // 序号已变：新图已打开或灯箱已关闭，本次结果整包丢弃。
  if (seq !== lightboxSeq) return;
  currentLightboxBlob = blob;
  currentLightboxUrl = URL.createObjectURL(blob);
  $("lightbox-caption").textContent = item.web_path;
  clearInlineError(errorEl);
  clearInlineError($("lightbox-error"));
  const lightbox = $("lightbox");
  lightbox.classList.remove("hidden");
  void lightbox.offsetWidth;
  lightbox.classList.add("open");
  const img = $("lightbox-img");
  img.classList.remove("loaded");
  // load 与 error 均用属性赋值覆盖上一张的残留监听，连续打开不累积。
  img.onload = () => img.classList.add("loaded");
  img.onerror = () => {
    // 损坏图片无 load 事件，须显式收场否则灯箱永久空白。
    img.classList.remove("loaded");
    showInlineError($("lightbox-error"), "图片无法解码，文件可能已损坏。");
  };
  img.src = currentLightboxUrl;
}

// 灯箱退场收尾计时器：过渡走完后再挂 hidden 并释放资源。
let lightboxCloseTimer = 0;

/** 关闭灯箱：退场过渡结束后释放当前对象 URL 与 blob。 */
export function closeLightbox() {
  lightboxSeq++;
  $("lightbox").classList.remove("open");
  clearTimeout(lightboxCloseTimer);
  // reduced-motion 下无过渡可等，立即收尾。
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    finishLightboxClose();
    return;
  }
  lightboxCloseTimer = setTimeout(finishLightboxClose, 260);
}

// 灯箱退场收尾：挂 hidden 并释放对象 URL 与 blob。
function finishLightboxClose() {
  const lightbox = $("lightbox");
  // 计时窗口内被重新打开时不收尾，交由新一轮关闭处理。
  if (lightbox.classList.contains("open")) return;
  lightbox.classList.add("hidden");
  releaseLightboxUrl();
  currentLightboxBlob = null;
  const img = $("lightbox-img");
  img.removeAttribute("src");
  img.classList.remove("loaded");
}

function releaseLightboxUrl() {
  if (currentLightboxUrl) {
    URL.revokeObjectURL(currentLightboxUrl);
    currentLightboxUrl = null;
  }
}

/**
 * 回填参考图：config-info 不再下发图片目录绝对路径，前端无从拼本地路径，改用
 * 灯箱已持有的 blob 转 data URI 作为参考图值。文生图工具自动切到图生图，
 * hash 变化由浏览器原生 hashchange 事件驱动视图切换。
 */
export async function useLightboxAsReference() {
  const blob = currentLightboxBlob;
  if (!blob || !state.configInfo || !state.configInfo.images_root_available)
    return;
  if (state.tool === "text-to-image") {
    document.querySelector('[data-tool="image-to-image"]').click();
    // 切工具时恢复的旧参考图可能占满唯一槽位，让位给用户显式选择的灯箱图，
    // 被顶出的仍回暂存不丢。
    while (
      state.refs.length > 0 &&
      state.refs.length >= toolConfig("image-to-image").max
    ) {
      state.parkedRefs.push(state.refs.pop());
    }
  }
  try {
    const dataUri = await blobToDataUri(blob);
    // 拒绝（数量或体积超限）时停留灯箱不跳转，具体原因已落生成台提示位。
    if (addReference("data_uri", dataUri, dataUri) !== null) {
      showInlineError($("lightbox-error"), "参考图未添加，详情见生成台提示。");
      return;
    }
  } catch {
    // 回填失败提示落在灯箱栏内，替代阻塞式弹窗。
    showInlineError($("lightbox-error"), "回填参考图失败，请改用上传方式。");
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
