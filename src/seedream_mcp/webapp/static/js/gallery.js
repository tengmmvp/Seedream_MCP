/**
 * @fileoverview 图库域：浏览分页、缩略图网格与灯箱。灯箱支持把历史图片回填
 * 为参考图。
 */

"use strict";

import {
  $,
  apiFetch,
  armTimeout,
  clearInlineError,
  discardBodyQuietly,
  fetchBlobUrl,
  IMAGE_FETCH_TIMEOUT_MS,
  initialGalleryState,
  isAbortError,
  isUnauthorizedError,
  postJson,
  rethrowIfAbort,
  revokeObjectUrls,
  runBoundedPool,
  showInlineError,
  state,
  TOOL_IMAGE_TO_IMAGE,
  TOOL_TEXT_TO_IMAGE,
} from "./api.js";
import { addReference, ensureRefCapacity } from "./refs.js";

/** 图库单页条数。 */
const GALLERY_PAGE_SIZE = 60;

// 缩略图分批并发：整页 60 张串行加载过慢，批大小 6 做受限并发。
const THUMBNAIL_BATCH_SIZE = 6;

// 401 停止消费时命中与未消费缩略图的统一过期文案。
const TOKEN_EXPIRED_THUMB_MESSAGE = "令牌已过期，重新输入后请刷新图库";

// 网络异常与体读取失败共用的加载失败文案。
const GALLERY_LOAD_FAILED_MESSAGE = "图库加载失败，请稍后重试。";

// 请求序号守卫：翻页或刷新连点时，仅最后一次请求的响应可落地。
let requestSeq = 0;

// 灯箱当前对象 URL 与原始 blob：独立于全局 objectUrls 生命周期，随开关与
// 换图精确回收；blob 保留供「用作参考图」转 data URI；序号守卫丢弃换图或
// 关闭后到达的过期响应。
let currentLightboxUrl = null;
let currentLightboxBlob = null;
let lightboxSeq = 0;
// 灯箱打开前的焦点来源，关闭后归还。
let lightboxReturnFocus = null;

function clearPagerDom() {
  $("gallery-page").textContent = "";
  $("gallery-count").textContent = "";
  $("gallery-prev").disabled = true;
  $("gallery-next").disabled = true;
}

// 图库区错误提示：落视图顶部的专用错误节点（空态文案位在满页时位于首屏外）；
// 失败同时复位翻页器，避免旧可用态把用户引向空页。
function showGalleryError(message) {
  showInlineError($("gallery-error"), message);
  clearPagerDom();
}

// null 态更多未知，不宣称没有更多也不误报总数；永久停滞与截断徒劳均为
// 终态（下一页已禁用），不追加「可能还有更多」，截断徒劳改述服务端截断
// 口径；空页的 null 态由空页文案细化，计数行统一称扫描未完成。
function galleryCountText(hasMore, totalCount, shownCount, stallPermanent, rescanFutile) {
  if (hasMore === null) {
    if (!shownCount || stallPermanent) return "（扫描未完成）";
    if (rescanFutile) return "（目录条目过多，结果可能不完整）";
    return "（可能还有更多）";
  }
  if (hasMore) return "（还有更多）";
  return typeof totalCount === "number" ? `（共 ${totalCount} 张）` : "";
}

// 空页文案三轴分流：更多未知的空页按终态改述（永久停滞、截断徒劳、余者
// 导引续扫）；SKIPPED 收尾的 has_more 已按事实置 false，有跳过条目改述部分
// 图片无法访问并随永久停滞取持续口径；末轴按格式过滤回退。
function galleryEmptyText(gallery, skippedCount, format) {
  if (gallery.hasMore === null && gallery.items.length === 0) {
    if (gallery.stallPermanent) return "目录持续无法访问，请稍后重试。";
    if (gallery.rescanFutile) return "扫描未完成，本页暂无图片；目录条目过多已截断。";
    return "扫描未完成，本页暂无图片；点击「下一页」重试扫描。";
  }
  if (skippedCount)
    return `部分图片${gallery.stallPermanent ? "持续" : "暂时"}无法访问（跳过 ${skippedCount} 张）。`;
  return format ? "当前过滤条件下没有图片。" : "保存目录还没有图片。";
}

/**
 * 按当前偏移与格式过滤请求图库并渲染缩略图网格与翻页器；连点由请求序号
 * 守卫收敛到最后一次，刷新按钮在请求期间保持 busy 旋转；异常兜底为图库
 * 错误提示，不向调用方抛出。
 */
export async function refreshGallery() {
  const seq = ++requestSeq;
  $("gallery-refresh").classList.add("busy");
  // 请求期间禁用翻页按钮，防止响应返回前连点叠加 offset 跳页；成功路径按
  // 新状态恢复，失败路径由 clearPagerDom 维持禁用。
  $("gallery-prev").disabled = true;
  $("gallery-next").disabled = true;
  try {
    await refreshGalleryForSeq(seq);
  } catch (error) {
    // 渲染段兜底：请求与解析各有 try/catch，渲染期异常外溢会成为未处理
    // rejection。
    if (seq === requestSeq) {
      // 中止类异常属请求期超时或中止，分流提示以免误标渲染失败。
      if (isAbortError(error)) {
        showGalleryError("图库加载超时，请稍后重试。");
      } else {
        console.error("图库渲染失败:", error);
        showGalleryError("图库渲染失败，请稍后重试。");
      }
    }
  } finally {
    // 仅最新请求有权摘除 busy，旧请求的收尾不得打断新请求的旋转。
    if (seq === requestSeq) $("gallery-refresh").classList.remove("busy");
  }
}

// 图库页取数：null 哨兵表示失败已分流展示或响应已过期，外层直接收场；
// 展示回调统一复查请求序号，被取代请求的失败不覆盖新请求的界面状态。
async function fetchGalleryPage(body, seq) {
  const showGalleryErrorIfLatest = (message) => {
    if (seq === requestSeq) showGalleryError(message);
  };
  try {
    return await postJson("/web/api/browse", body, IMAGE_FETCH_TIMEOUT_MS, {
      onNetworkError: () =>
        showGalleryErrorIfLatest(GALLERY_LOAD_FAILED_MESSAGE),
      // 结构化错误优先展示后端消息：offset 越界等可指导用户刷新，
      // 未知形态已由骨架归一回退 HTTP 状态码文案。
      onEndpointError: (failure) =>
        showGalleryErrorIfLatest(`图库加载失败：${failure.message}`),
      // PayloadError 按响应异常提示，图库刷新可安全重试。
      onPayloadError: () =>
        showGalleryErrorIfLatest("图库响应异常，请稍后重试。"),
    });
  } catch (error) {
    // 体读取失败上抛按请求失败提示，中止类继续上抛走外层超时档。
    rethrowIfAbort(error);
    showGalleryErrorIfLatest(GALLERY_LOAD_FAILED_MESSAGE);
    return null;
  }
}

// 请求主体：seq 为本次请求序号，过期响应在内部各检查点丢弃。
async function refreshGalleryForSeq(seq) {
  // 配置未就绪时提示刷新并复位翻页器，main 的补刷在 config-info 落地后重进。
  if (!state.configInfo) {
    $("gallery-empty").textContent = "配置未加载，请刷新页面重试。";
    $("gallery-empty").classList.remove("hidden");
    clearPagerDom();
    return;
  }
  if (!state.configInfo.images_root_available) {
    resetGalleryPaging();
    // 清网格前先回收图库缩略图池，已渲染页的对象 URL 不随本分支泄漏。
    revokeObjectUrls("gallery");
    $("gallery-grid").innerHTML = "";
    $("gallery-empty").textContent = state.configInfo.images_root_hint || "";
    $("gallery-empty").classList.remove("hidden");
    clearPagerDom();
    return;
  }
  const format = $("format-filter").value;
  const body = {
    limit: GALLERY_PAGE_SIZE,
    offset: state.gallery.offset,
    show_details: true,
  };
  if (format) body.format_filter = [format];
  const payload = await fetchGalleryPage(body, seq);
  if (!payload) return;
  if (seq !== requestSeq) return;
  // 新一页装载成功，清除上一轮遗留的加载失败提示。
  clearInlineError($("gallery-error"));
  // 页码一律以响应回显的 offset 为准，避免竞态期间本地偏移已被改写；回退栈
  // 复位仅经 resetGalleryPaging，回显路径不清栈。
  if (typeof payload.offset === "number") {
    state.gallery.offset = payload.offset;
  }
  // 显式 null 表示更多未知（扫描截断或停滞）；has_more 键缺失按无更多保守处理。
  // 严格等有意：自家后端恒下发布尔或 null，真值非布林的异构代理不在设防范围。
  state.gallery.hasMore =
    payload.has_more === null ? null : payload.has_more === true;
  state.gallery.nextOffset = payload.next_offset ?? null;
  // 键缺失的契约漂移按非限定态处理：永久停滞与截断徒劳都不误判。
  state.gallery.stallPermanent = payload.stall_permanent === true;
  state.gallery.rescanFutile = payload.rescan_futile === true;
  // 越界条目已在服务端剔除，前端条目均带 web_path。
  state.gallery.items = payload.images || [];

  // 只回收图库缩略图池，生成台结果图的 blob URL 不受翻页与刷新波及。
  revokeObjectUrls("gallery");
  const grid = $("gallery-grid");
  grid.innerHTML = "";
  $("gallery-empty").textContent = galleryEmptyText(
    state.gallery,
    payload.skipped_count,
    format,
  );
  $("gallery-empty").classList.toggle("hidden", state.gallery.items.length > 0);
  const pending = state.gallery.items.map((item) => {
    const figure = document.createElement("figure");
    figure.className = "gallery-item";
    figure.tabIndex = 0;
    figure.setAttribute("role", "button");
    figure.setAttribute("aria-label", `查看 ${item.path}`);
    const img = document.createElement("img");
    img.alt = item.path;
    img.classList.add("developing");
    img.addEventListener("load", () => img.classList.add("loaded"));
    figure.appendChild(img);
    const caption = document.createElement("figcaption");
    // size_mb 为 0（零字节文件）照常渲染，仅文件信息不可用的 null 省略。
    const detail = [
      Number.isFinite(item.size_mb) ? `${item.size_mb.toFixed(2)}MB` : "",
      item.modified || "",
    ]
      .filter(Boolean)
      .join(" · ");
    caption.textContent = `${item.path}${detail ? " — " + detail : ""}`;
    caption.title = item.path;
    figure.appendChild(caption);
    // 图库条目键盘可达：Enter 与 Space 等价点击；
    // size_mb 供灯箱缺 Content-Length 时推导体读取预算。
    const activate = () =>
      openLightbox({ web_path: item.path, size_mb: item.size_mb });
    figure.addEventListener("click", activate);
    figure.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      activate();
    });
    grid.appendChild(figure);
    return { img, path: item.path };
  });

  const markUnavailable = (entry) => {
    entry.img.classList.add("thumb-error");
    entry.img.alt = "缩略图不可用";
  };

  // 401 终态沿用失败占位形态，文案换令牌过期的重试导引。
  const markAuthExpired = (entry) => {
    entry.img.classList.add("thumb-error");
    entry.img.alt = TOKEN_EXPIRED_THUMB_MESSAGE;
  };

  let authStopped = false;
  // 固定 worker 消费队列，序号逐项守卫，过期请求的 blob URL 就地释放。
  const unconsumed = await runBoundedPool(pending, THUMBNAIL_BATCH_SIZE, async (entry) => {
    if (seq !== requestSeq) return false;
    try {
      // 超时由 fetchBlobUrl 的内建超时档承担。
      const blobUrl = await fetchBlobUrl(
        `/web/api/thumbnail?path=${encodeURIComponent(entry.path)}`,
        "gallery",
      );
      if (seq !== requestSeq) {
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        return false;
      }
      if (blobUrl) entry.img.src = blobUrl;
      else markUnavailable(entry);
      return true;
    } catch (error) {
      // 401 已弹令牌门，当前条目落过期终态并停止消费，免逐张重复弹门；
      // 被取代请求的 401 不盖终态。
      if (isUnauthorizedError(error)) {
        if (seq === requestSeq) {
          markAuthExpired(entry);
          authStopped = true;
        }
        return false;
      }
      markUnavailable(entry);
    }
  });
  // 善后仅限仍为最新请求的 401 停止：未消费条目统一落过期终态；
  // 被新请求取代的停止不盖终态，旧页未决占位留待新请求重渲染定夺。
  if (authStopped && seq === requestSeq) {
    for (const entry of unconsumed) markAuthExpired(entry);
  }
  // 过期请求不执行收尾：翻页器写回按新请求的状态由其自身完成。
  if (seq !== requestSeq) return;

  const count = state.gallery.items.length;
  $("gallery-page").textContent = count
    ? `${state.gallery.offset + 1} – ${state.gallery.offset + count}`
    : "0";
  $("gallery-count").textContent = galleryCountText(
    state.gallery.hasMore,
    payload.total_count,
    count,
    state.gallery.stallPermanent,
    state.gallery.rescanFutile,
  );
  // 不变式 offset>0 必有回退栈，prev 禁用单看偏移。
  $("gallery-prev").disabled = state.gallery.offset === 0;
  $("gallery-next").disabled = !canAdvanceGallery();
}

// 翻页谓词：后端已在 next_offset 编码推进决策（末页、永久停滞与截断徒劳均不下发），
// 直接以其为闸；hasMore 守卫仅拦 has_more 键缺失的契约漂移保守面。按钮渲染与点击
// handler 同此单点判定。
export function canAdvanceGallery() {
  return state.gallery.nextOffset !== null && state.gallery.hasMore !== false;
}

/**
 * 下一页推进的纯变更器：目标恒取后端 next_offset（has_more 真为下一页起点，
 * 截断或停滞为跳过本页条目的续扫偏移）并无条件赋值，目标不同于当前偏移时
 * 压回退栈供 prev 精确回跳；推进资格由调用方经 canAdvanceGallery 判定；
 * 空窗原地重扫（目标等于当前偏移）免压栈，重扫由调用方的无条件刷新承担；
 * next_offset 缺失仅余契约破坏形态，不改状态。
 */
export function advanceGalleryOffset() {
  if (state.gallery.nextOffset == null) return;
  if (state.gallery.nextOffset !== state.gallery.offset) {
    state.gallery.backStack.push(state.gallery.offset);
  }
  state.gallery.offset = state.gallery.nextOffset;
}

/** 上一页回退：弹回退栈回到上一屏真实起点，栈空时钳制到 0。 */
export function retreatGalleryOffset() {
  state.gallery.offset = state.gallery.backStack.pop() ?? 0;
}

/**
 * 分页状态整体置换回首页初始态，
 * 刷新按钮、筛选变更、筛选恢复失败与目录不可用分支共用。
 */
export function resetGalleryPaging() {
  state.gallery = initialGalleryState();
}

// 灯箱体读取预算常量：低速链路下限 512KB/s，50MB 上限原图据此约 100 秒；
// 600 秒封顶仅约 300MB 以上触及。
const LIGHTBOX_MIN_BYTES_PER_SEC = 512 * 1024;
const LIGHTBOX_MAX_TIMEOUT_MS = 600000;

// 灯箱体读取预算：Content-Length 按下限速率放大共享档并封顶；
// 缺长度响应回退条目 size_mb 同式推导，元数据同缺才维持共享档。
function lightboxBodyBudgetMs(response, sizeMb) {
  const bytes = Number(response.headers.get("Content-Length"));
  if (Number.isFinite(bytes) && bytes > 0) return scaledBodyBudgetMs(bytes);
  if (Number.isFinite(sizeMb) && sizeMb > 0)
    return scaledBodyBudgetMs(sizeMb * 1024 * 1024);
  return IMAGE_FETCH_TIMEOUT_MS;
}

function scaledBodyBudgetMs(bytes) {
  return Math.min(
    LIGHTBOX_MAX_TIMEOUT_MS,
    Math.max(
      IMAGE_FETCH_TIMEOUT_MS,
      Math.ceil((bytes / LIGHTBOX_MIN_BYTES_PER_SEC) * 1000),
    ),
  );
}

// 灯箱原图装载：null 哨兵表示失败已分流展示或响应已过期，不开灯箱。
async function fetchLightboxOriginal(webPath, seq, errorEl, sizeMb) {
  // 两段超时共用单控制器：fetch 前按共享档武装，
  // 头部就位后先撤旧臂再按体预算重臂。
  const controller = new AbortController();
  let disarm;
  try {
    disarm = armTimeout(controller, IMAGE_FETCH_TIMEOUT_MS);
    let response;
    try {
      response = await apiFetch(
        `/web/api/image?path=${encodeURIComponent(webPath)}`,
        { signal: controller.signal },
      );
    } catch (error) {
      // 401 已弹令牌门；其余网络异常与取图超时不开灯箱，缩略图仍在，可重试。
      if (isUnauthorizedError(error)) return null;
      if (seq !== lightboxSeq) return null;
      showInlineError(errorEl, "原图加载失败，请重试。");
      return null;
    }
    if (!response.ok) {
      await discardBodyQuietly(response);
      console.error("原图加载失败:", webPath);
      if (seq !== lightboxSeq) return null;
      showInlineError(errorEl, "原图加载失败，图片可能已被清理。");
      return null;
    }
    try {
      disarm();
      disarm = armTimeout(controller, lightboxBodyBudgetMs(response, sizeMb));
      return await response.blob();
    } catch {
      if (seq !== lightboxSeq) return null;
      showInlineError(errorEl, "原图加载失败，请重试。");
      return null;
    }
  } finally {
    disarm();
  }
}

/**
 * 打开灯箱并装载原图。原图 blob 就绪后才显示灯箱，入场一次到位，避免
 * 先开空框再被图片撑开造成的尺寸突变；序号守卫丢弃过期响应。
 *
 * @param {Object} item - 目标条目，仅使用 web_path 与 size_mb 字段，
 *   后者供缺 Content-Length 响应的体读取预算回退。
 * @param {HTMLElement} [errorEl=$("gallery-error")] - 失败提示落点，须在调用方
 *   当前可见的视图内；生成台复用本函数时传生成视图的错误节点。
 */
export async function openLightbox(item, errorEl = $("gallery-error")) {
  if (!item || !item.web_path) return;
  const seq = ++lightboxSeq;
  lightboxReturnFocus = document.activeElement;
  releaseLightboxUrl();
  const blob = await fetchLightboxOriginal(
    item.web_path,
    seq,
    errorEl,
    item.size_mb,
  );
  if (!blob) return;
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
  $("lightbox-close").focus();
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
  // 归还打开前的焦点，目标已从文档移除时放弃。
  if (lightboxReturnFocus) {
    if (lightboxReturnFocus.isConnected) lightboxReturnFocus.focus();
    lightboxReturnFocus = null;
  }
}

function releaseLightboxUrl() {
  if (currentLightboxUrl) {
    URL.revokeObjectURL(currentLightboxUrl);
    currentLightboxUrl = null;
  }
}

/**
 * 回填参考图：config-info 不再下发图片目录绝对路径，前端无从拼本地路径，改用
 * 灯箱已持有的 blob 转 data URI 作为参考图值。文生图工具自动切到图生图；满档
 * 时既有参考图经 ensureRefCapacity 统一让位，不因所在工具与进入路径而异。
 * 回填成功后 hash 跳回生成台，视图切换由浏览器原生 hashchange 事件驱动。
 */
export async function useLightboxAsReference() {
  const blob = currentLightboxBlob;
  if (!blob || !state.configInfo || !state.configInfo.images_root_available)
    return;
  if (state.tool === TOOL_TEXT_TO_IMAGE) {
    document.querySelector(`[data-tool="${TOOL_IMAGE_TO_IMAGE}"]`).click();
  }
  ensureRefCapacity();
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
