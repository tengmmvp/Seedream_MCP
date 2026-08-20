/* 基础层：全局状态、鉴权封装与对象 URL 管理。
   其余模块均依赖本文件；本文件不依赖任何兄弟模块。 */

"use strict";

export const TOKEN_STORAGE_KEY = "seedream_web_token";

export const state = {
  token: localStorage.getItem(TOKEN_STORAGE_KEY) || "",
  configInfo: null,
  tool: "text-to-image",
  refs: [],
  objectUrls: [],
  gallery: { offset: 0, hasMore: false, items: [] },
  lightboxPath: null,
};

export const $ = (id) => document.getElementById(id);

export function revokeObjectUrls() {
  for (const url of state.objectUrls) URL.revokeObjectURL(url);
  state.objectUrls = [];
}

/* 统一请求入口：自动携带令牌，401 时弹令牌门并以 unauthorized 异常上抛。 */
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

/* 图片走 blob 模式：令牌只进请求头不进 URL，对象 URL 登记后统一回收。 */
export async function fetchBlobUrl(path) {
  const response = await apiFetch(path);
  if (!response.ok) return null;
  const url = URL.createObjectURL(await response.blob());
  state.objectUrls.push(url);
  return url;
}

export function showTokenGate() {
  $("token-gate").classList.remove("hidden");
  $("token-input").focus();
}

export function hideTokenGate() {
  $("token-gate").classList.add("hidden");
}
