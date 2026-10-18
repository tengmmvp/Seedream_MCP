/**
 * @fileoverview 基础层：全局状态、鉴权封装、响应解析与对象 URL 管理。其余
 * 模块均依赖本文件；本文件不依赖任何兄弟模块。
 */

"use strict";

/** sessionStorage 中 Bearer 令牌的存储键。 */
const TOKEN_STORAGE_KEY = "seedream_web_token";

// 阻断型存储环境（Safari「阻止所有 Cookie」等）下 sessionStorage 访问抛
// SecurityError，读写统一吞异常降级：令牌仅存于页面会话内存，刷新后重输。
function readStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 把令牌写入 sessionStorage，存储不可用时静默降级为仅内存持有。 */
export function writeStoredToken(token) {
  try {
    sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    /* 存储不可用，令牌仍可用但不跨刷新 */
  }
}

/** 清除 sessionStorage 中的令牌，存储不可用时无操作。 */
export function clearStoredToken() {
  try {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* 同 writeStoredToken 的降级 */
  }
}

/** 工具标识，与后端生成端点 slug 及页面 data-tool 标记一一对应。 */
export const TOOL_TEXT_TO_IMAGE = "text-to-image";
export const TOOL_IMAGE_TO_IMAGE = "image-to-image";
export const TOOL_MULTI_IMAGE_FUSION = "multi-image-fusion";
export const TOOL_SEQUENTIAL_GENERATION = "sequential-generation";

/**
 * 前端唯一的全局可变状态；令牌经 sessionStorage 暂存于当前浏览器会话，不跨
 * 会话落盘持久化，其余字段随页面会话存亡。
 *
 * @property {string} token - Bearer 令牌。
 * @property {Object|null} configInfo - config-info 响应，启动时加载。
 * @property {string} tool - 当前工具标识。
 * @property {Array<Object>} refs - 参考图列表，元素形如 {kind, value, preview}。
 * @property {Array<Object>} parkedRefs - 因目标工具上限收缩而暂存的参考图，
 *   切回支持的工具时按上限自动恢复。
 * @property {string|null} sizePreLayer - 进入图层拆分前记住的尺寸选择，离开时恢复。
 * @property {Object<string, Array<string>>} objectUrls - 对象 URL 按域分池
 *   登记，键为 generate 与 gallery；revokeObjectUrls 按池回收，图库翻页不
 *   波及生成台。
 * @property {Object} gallery - 图库分页状态，形如 {offset, hasMore, items}。
 */
export const state = {
  token: readStoredToken() || "",
  configInfo: null,
  tool: TOOL_TEXT_TO_IMAGE,
  refs: [],
  parkedRefs: [],
  sizePreLayer: null,
  objectUrls: { generate: [], gallery: [] },
  gallery: { offset: 0, hasMore: false, items: [] },
};

/**
 * getElementById 简写。
 *
 * @param {string} id - 元素 ID。
 * @returns {HTMLElement|null} 对应 DOM 元素。
 */
export const $ = (id) => document.getElementById(id);

/** 当前模型在 config-info 模型清单中的能力条目，未知模型返回 null。 */
export function currentModel() {
  if (!state.configInfo) return null;
  // config.model_id 已归一为完整 ID，lite 与 5.0 家族共享同一 ID 与能力字段，
  // 按 model_id 匹配即可。
  return (
    (state.configInfo.models || []).find(
      (m) => m.model_id === state.configInfo.model_id,
    ) || null
  );
}

/** 切换活动工具并同步高亮、选中态与焦点序，工具切换与能力回落共用。 */
export function setActiveTool(tool) {
  state.tool = tool;
  document.querySelectorAll("#tool-tabs button").forEach((b) => {
    const selected = b.dataset.tool === tool;
    b.classList.toggle("active", selected);
    b.setAttribute("aria-checked", String(selected));
    b.tabIndex = selected ? 0 : -1;
  });
}

/**
 * 行内错误提示的统一形态：写文本并显示节点。
 *
 * @param {HTMLElement|null} element - 提示节点。
 * @param {string} message - 提示文本。
 */
export function showInlineError(element, message) {
  element.textContent = message;
  element.classList.remove("hidden");
}

/**
 * 清除行内错误提示：隐藏节点并清空残留文本。
 *
 * @param {HTMLElement|null} element - 提示节点。
 */
export function clearInlineError(element) {
  element.textContent = "";
  element.classList.add("hidden");
}

/**
 * 对象 URL 生命周期出口：回收指定池的全部登记并清空，与两个 fetch 的登记端
 * 配对，防止 blob URL 累积泄漏。
 *
 * @param {"generate"|"gallery"} pool - 对象 URL 池名。
 */
export function revokeObjectUrls(pool) {
  for (const url of state.objectUrls[pool]) URL.revokeObjectURL(url);
  state.objectUrls[pool] = [];
}

/** 401 哨兵错误 message，抛出点与全部捕获点共用同一来源。 */
export const UNAUTHORIZED_MESSAGE = "unauthorized";

/**
 * 统一请求入口：自动携带令牌。
 *
 * @param {string} path - 请求路径。
 * @param {RequestInit} [options] - fetch 选项，headers 会合并注入 Authorization。
 * @returns {Promise<Response>} fetch 响应。
 * @throws {Error} 401 时弹出令牌门并以 "unauthorized" 上抛。
 */
export async function apiFetch(path, options = {}) {
  // Headers 实例经 Object.assign 复制不到其条目，先归一为普通对象再合并令牌。
  const headers =
    options.headers instanceof Headers
      ? Object.fromEntries(options.headers.entries())
      : Object.assign({}, options.headers || {});
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  if (response.status === 401) {
    showTokenGate();
    throw new Error(UNAUTHORIZED_MESSAGE);
  }
  return response;
}

/**
 * 本服务图片走 blob 模式：令牌只进请求头不进 URL，对象 URL 登记入指定池后
 * 统一回收。仅用于 /web/api 路径。
 *
 * @param {string} path - /web/api 下的图片端点路径。
 * @param {"generate"|"gallery"} pool - 对象 URL 池名。
 * @returns {Promise<string|null>} 对象 URL；响应非 2xx 时为 null，展示回退
 *   由调用方定。
 */
export async function fetchBlobUrl(path, pool) {
  const response = await apiFetch(path);
  if (!response.ok) return null;
  const url = URL.createObjectURL(await response.blob());
  state.objectUrls[pool].push(url);
  return url;
}

/** AbortSignal.timeout 不可用时经 AbortController 定时中止，全浏览器兜底。 */
export function timeoutSignal(ms) {
  if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
  const controller = new AbortController();
  // 中止理由置为 TimeoutError，支持 reason 的浏览器按名分类。
  setTimeout(
    () =>
      controller.abort(new DOMException("signal timed out", "TimeoutError")),
    ms,
  );
  return controller.signal;
}

/** 中止类异常判定：超时与手动中止按 name 统一识别，各捕获点共用。 */
export function isAbortError(error) {
  return error.name === "TimeoutError" || error.name === "AbortError";
}

/** 中止类异常原样上抛，交上层超时档分流。 */
export function rethrowIfAbort(error) {
  if (isAbortError(error)) throw error;
}

/**
 * 外链图片走裸 fetch：不携带 Authorization，避免令牌外送到上游 CDN；对象 URL
 * 登记入指定池后统一回收。
 *
 * @param {string} url - 外链图片 URL。
 * @param {"generate"|"gallery"} pool - 对象 URL 池名。
 * @returns {Promise<string|null>} 对象 URL；fetch 失败（如 CORS）时为 null，
 *   由调用方回退直连 src。
 */
export async function fetchExternalBlobUrl(url, pool) {
  try {
    // 超时防上游停滞挂死；失败按既有回退直连 src。
    const response = await fetch(url, { signal: timeoutSignal(30000) });
    if (!response.ok) return null;
    const objectUrl = URL.createObjectURL(await response.blob());
    state.objectUrls[pool].push(objectUrl);
    return objectUrl;
  } catch {
    return null;
  }
}

/** 显示令牌门并聚焦输入框。 */
export function showTokenGate() {
  $("token-gate").classList.remove("hidden");
  $("token-input").focus();
}

/** 隐藏令牌门。 */
export function hideTokenGate() {
  $("token-gate").classList.add("hidden");
}

/**
 * 失败载荷归一：error.message、error_description、状态码三级回退，未知响应
 * 形态也给出可读信息。生成与图库端点共用。
 *
 * @param {Object|null|undefined} payload - 已解析的响应体，非法 JSON 为 undefined。
 * @param {Response} response - 原始响应。
 * @returns {Object} 形如 {type, message} 的错误对象。
 */
export function normalizePayloadError(payload, response) {
  const error = payload && payload.error;
  if (error && typeof error === "object" && error.message) return error;
  if (payload && typeof payload.error_description === "string") {
    return { type: error || "error", message: payload.error_description };
  }
  return { type: error || "error", message: `HTTP ${response.status}` };
}

/**
 * 宽松解析响应体为 JSON：非法 JSON（SyntaxError）归 undefined 哨兵，与合法
 * null 区分；中止类与其余体读取异常原样上抛，交调用方分档处置。框定一致的
 * 截断体同样以 SyntaxError 归 undefined（无法与真非法 JSON 可靠区分），
 * 为接受的残余缺口。
 *
 * @param {Response} response - 原始响应。
 * @returns {Promise<Object|null|undefined>} 解析结果，非法 JSON 为 undefined。
 * @throws {DOMException} 响应体读取中止，name 为 TimeoutError/AbortError。
 */
export async function parseJsonLoose(response) {
  try {
    return await response.json();
  } catch (error) {
    rethrowIfAbort(error);
    if (error instanceof SyntaxError) return undefined;
    throw error;
  }
}

/**
 * 尽力而为读取已拒绝响应的错误载荷：读取与解析的一切异常（含中止类）吞为
 * 无载荷——状态码已知，失败定性不依赖体内容，非 ok 分支共用。
 *
 * @param {Response} response - 已判定非 ok 的原始响应。
 * @returns {Promise<Object|null|undefined>} 解析结果，任何失败为 undefined。
 */
export async function readErrorPayload(response) {
  try {
    return await parseJsonLoose(response);
  } catch {
    return undefined;
  }
}

/**
 * ok 响应的载荷异常：bad_response（非 JSON 或非对象）与 empty_response
 * （字面 null）两档，message 携带用户可见文案，消费方按档位直接展示。
 */
export class PayloadError extends Error {
  /**
   * @param {"bad_response"|"empty_response"} type - 载荷异常档位。
   * @param {string} message - 用户可见文案。
   */
  constructor(type, message) {
    super(message);
    this.name = "PayloadError";
    this.type = type;
  }
}

/**
 * 严格解析 ok 响应体为 JSON 对象：非 JSON、非对象（数组/标量）与字面 null
 * 的载荷抛 PayloadError；体读取失败与中止类异常经 parseJsonLoose 原样上抛。
 *
 * @param {Response} response - 已判定 ok 的原始响应。
 * @returns {Promise<Object>} 解析出的载荷对象。
 * @throws {PayloadError} 响应体非 JSON、非对象或为字面 null。
 */
export async function parseJsonRequired(response) {
  const payload = await parseJsonLoose(response);
  if (payload === undefined) {
    throw new PayloadError(
      "bad_response",
      "响应不是有效的 JSON，服务可能异常，请稍后重试。",
    );
  }
  if (payload === null) {
    throw new PayloadError("empty_response", "服务返回了空响应，请稍后重试。");
  }
  // 数组与标量是合法 JSON 但非载荷对象，与非法 JSON 分开如实提示。
  if (typeof payload !== "object" || Array.isArray(payload)) {
    throw new PayloadError(
      "bad_response",
      "响应不是预期的 JSON 对象，服务可能异常，请稍后重试。",
    );
  }
  return payload;
}
