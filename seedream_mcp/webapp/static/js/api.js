/**
 * @fileoverview 基础层：全局状态、鉴权封装与对象 URL 管理。其余模块均依赖
 * 本文件；本文件不依赖任何兄弟模块。
 */

"use strict";

/** sessionStorage 中 Bearer 令牌的存储键。 */
export const TOKEN_STORAGE_KEY = "seedream_web_token";

/**
 * 前端唯一的全局可变状态；令牌经 sessionStorage 暂存于当前浏览器会话，不跨
 * 会话落盘持久化，其余字段随页面会话存亡。
 *
 * @property {string} token - Bearer 令牌。
 * @property {Object|null} configInfo - config-info 响应，启动时加载。
 * @property {string} tool - 当前工具标识。
 * @property {Array<Object>} refs - 参考图列表，元素形如 {kind, value, preview}。
 * @property {Object<string, Array<string>>} objectUrls - 对象 URL 按域分池
 *   登记，键为 generate 与 gallery；revokeObjectUrls 按池回收，图库翻页不
 *   波及生成台。
 * @property {Object} gallery - 图库分页状态，形如 {offset, hasMore, items}。
 */
export const state = {
  token: sessionStorage.getItem(TOKEN_STORAGE_KEY) || "",
  configInfo: null,
  tool: "text-to-image",
  refs: [],
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

/**
 * 统一请求入口：自动携带令牌。
 *
 * @param {string} path - 请求路径。
 * @param {RequestInit} [options] - fetch 选项，headers 会合并注入 Authorization。
 * @returns {Promise<Response>} fetch 响应。
 * @throws {Error} 401 时弹出令牌门并以 "unauthorized" 上抛。
 */
export async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  if (response.status === 401) {
    showTokenGate();
    throw new Error("unauthorized");
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
    const response = await fetch(url);
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
