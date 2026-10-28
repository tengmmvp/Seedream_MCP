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
 * 图库分页状态的首页初始态工厂：state 初始化与 gallery 域的整体复位共用，
 * 每次返回全新对象。
 *
 * @returns {Object} 形如 {offset, hasMore, items, nextOffset, stallPermanent,
 *   rescanFutile, backStack} 的初始分页状态；hasMore 三态透传后端 has_more，
 *   null 表示扫描截断或停滞、可能还有更多；nextOffset 与 hasMore 同批更新，
 *   为后端回传的下一页起点或截断/停滞续扫偏移；stallPermanent 透传后端
 *   stall_permanent 的永久停滞态，rescanFutile 透传 rescan_futile 的截断页
 *   重扫徒劳态；backStack 依序记录 next 推进前的偏移，供 prev 精确回跳。
 */
export function initialGalleryState() {
  return {
    offset: 0,
    hasMore: false,
    items: [],
    nextOffset: null,
    stallPermanent: false,
    rescanFutile: false,
    backStack: [],
  };
}

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
 * @property {Object} gallery - 图库分页状态，字段语义与初始态见
 *   initialGalleryState。
 */
export const state = {
  token: readStoredToken() || "",
  configInfo: null,
  tool: TOOL_TEXT_TO_IMAGE,
  refs: [],
  parkedRefs: [],
  sizePreLayer: null,
  objectUrls: { generate: [], gallery: [] },
  gallery: initialGalleryState(),
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

/** 401 哨兵错误 message，抛出点与判定谓词共用。 */
const UNAUTHORIZED_MESSAGE = "unauthorized";

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
    await discardBodyQuietly(response);
    showTokenGate();
    throw new Error(UNAUTHORIZED_MESSAGE);
  }
  return response;
}

// 本服务取图与图库 browse 目录扫描共享超时档：60 秒兜底停滞请求，
// 介于外链取图（30 秒）与生成请求超时（生成端点 preflight 推导）之间。
export const IMAGE_FETCH_TIMEOUT_MS = 60000;

/**
 * 武装控制器的超时中止并返回撤销函数：到点以 TimeoutError 理由中止
 * （支持 reason 的浏览器按名分类），撤销函数清除计时器。
 *
 * @param {AbortController} controller - 待武装的控制器。
 * @param {number} ms - 超时毫秒数。
 * @returns {function(): void} 撤销本次武装的函数。
 */
export function armTimeout(controller, ms) {
  const timer = setTimeout(
    () =>
      controller.abort(new DOMException("signal timed out", "TimeoutError")),
    ms,
  );
  return () => clearTimeout(timer);
}

/**
 * 本服务图片走 blob 模式：令牌只进请求头不进 URL，对象 URL 登记入指定池后
 * 统一回收；内部恒武装共享档超时。仅用于 /web/api 路径。
 *
 * @param {string} path - /web/api 下的图片端点路径。
 * @param {"generate"|"gallery"} pool - 对象 URL 池名。
 * @returns {Promise<string|null>} 对象 URL；响应非 2xx 时为 null，展示回退
 *   由调用方定。
 */
export async function fetchBlobUrl(path, pool) {
  const controller = new AbortController();
  const disarm = armTimeout(controller, IMAGE_FETCH_TIMEOUT_MS);
  try {
    const response = await apiFetch(path, { signal: controller.signal });
    if (!response.ok) {
      await discardBodyQuietly(response);
      return null;
    }
    return registerBlobUrl(response, pool);
  } finally {
    disarm();
  }
}

/**
 * 超时限定执行：fn 在超时 signal 下运行，返回值与异常透传；
 * 计时器经 armTimeout 武装并在 finally 撤销，不残留活跃句柄。
 *
 * @param {number} ms - 超时毫秒数。
 * @param {function(AbortSignal): Promise<*>} fn - 接收超时 signal 的执行体。
 * @returns {Promise<*>} fn 的返回值。
 */
export async function withTimeoutSignal(ms, fn) {
  const controller = new AbortController();
  const disarm = armTimeout(controller, ms);
  try {
    return await fn(controller.signal);
  } finally {
    disarm();
  }
}

/** 401 哨兵异常判定，各捕获点共用。 */
export function isUnauthorizedError(error) {
  return error.message === UNAUTHORIZED_MESSAGE;
}

/** 中止类异常判定：超时与手动中止按 name 统一识别，各捕获点共用。 */
export function isAbortError(error) {
  return error.name === "TimeoutError" || error.name === "AbortError";
}

/** 中止类异常原样上抛，交上层超时档分流。 */
export function rethrowIfAbort(error) {
  if (isAbortError(error)) throw error;
}

// 弃响应体释放连接并吞异常：未读体会占住 socket 直至 GC 取消，坏体流无从
// 释放即放弃，失败分支收尾不劫持既有语义。
export async function discardBodyQuietly(response) {
  try {
    await response.body?.cancel();
  } catch {
    /* 弃体失败无可补救 */
  }
}

// blob 转对象 URL 并入池登记的共用尾部，入池生命周期单源。
async function registerBlobUrl(response, pool) {
  const objectUrl = URL.createObjectURL(await response.blob());
  state.objectUrls[pool].push(objectUrl);
  return objectUrl;
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
  // 超时防上游停滞挂死；失败按既有回退直连 src。
  try {
    return await withTimeoutSignal(30000, async (signal) => {
      const response = await fetch(url, { signal });
      if (!response.ok) {
        await discardBodyQuietly(response);
        return null;
      }
      return registerBlobUrl(response, pool);
    });
  } catch {
    return null;
  }
}

/**
 * 有界并发池：固定数量的 worker 经共享游标逐项消费条目；
 * worker 返回 false 时该 worker 提前退出、不再消费后续条目（401 等终态场景）；
 * worker 抛异常仅跳过该条目并记录。
 *
 * @param {Array} entries - 待消费条目。
 * @param {number} concurrency - worker 数量。
 * @param {function(*): Promise<boolean|void>} worker - 单条目处理回调。
 * @returns {Promise<Array>} 未被任何 worker 消费的剩余条目，供调用方对提前
 *   停止场景统一善后。
 */
export async function runBoundedPool(entries, concurrency, worker) {
  let cursor = 0;
  const runners = Array.from(
    { length: Math.min(concurrency, entries.length) },
    async () => {
      while (cursor < entries.length) {
        const entry = entries[cursor++];
        try {
          if ((await worker(entry)) === false) return;
        } catch (error) {
          console.error("并发池 worker 异常:", error);
        }
      }
    },
  );
  await Promise.all(runners);
  return entries.slice(cursor);
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
 * 失败载荷归一：error 子对象（error.type/error.message）、error_description、
 * 状态码三级回退，未知响应形态也给出可读信息；
 * 结构化端点信封（browse/generate 与文件端点统一）与传输层扁平错误共用。
 *
 * @param {Object|null|undefined} payload - 已解析的响应体，非法 JSON 为 undefined。
 * @param {Response} response - 原始响应。
 * @returns {Object} 形如 {type, message} 的错误对象。
 */
export function normalizePayloadError(payload, response) {
  const error = payload && payload.error;
  if (error && typeof error === "object" && error.message) return error;
  // type 仅接受字符串，非字符串 error（如前置反向代理载荷的嵌套对象）
  // 回退 "error"，避免对象字面量漏入展示文案。
  const type = (typeof error === "string" && error) || "error";
  if (payload && typeof payload.error_description === "string") {
    return { type, message: payload.error_description };
  }
  return { type, message: `HTTP ${response.status}` };
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
 * 尽力而为读取已拒绝响应的错误载荷：非中止类异常吞为无载荷——
 * 失败定性不依赖体内容；中止类上抛交调用方分档处置。
 *
 * @param {Response} response - 已判定非 ok 的原始响应。
 * @returns {Promise<Object|null|undefined>} 解析结果，非中止类失败为 undefined。
 * @throws {DOMException} 响应体读取中止，name 为 TimeoutError/AbortError。
 */
export async function readErrorPayload(response) {
  try {
    return await parseJsonLoose(response);
  } catch (error) {
    rethrowIfAbort(error);
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

/**
 * JSON-POST 端点的共享消费骨架：超时 signal 下请求，401 吞为 null 哨兵
 * （令牌门已由请求层弹出）；非 ok 失败体尽力读取（读取异常含中止骨架内吸收）
 * 并归一后交展示回调，ok 载荷经严格解析；
 * ok 载荷的中止类与体读取失败原样上抛走调用方超时/网络档，
 * 展示差异全部上移为调用方回调。
 *
 * @param {string} path - 端点路径。
 * @param {Object} body - JSON 请求体。
 * @param {number} timeoutMs - 超时毫秒数。
 * @param {Object} handlers - 展示层回调，各失败档消费后统一回 null 哨兵。
 * @param {function(): void} [handlers.onUnauthorized] - 401 收场展示。
 * @param {function(Error): void} handlers.onNetworkError - 非中止类请求异常展示。
 * @param {function(Object): void} handlers.onEndpointError - 非 ok 归一化错误展示。
 * @param {function(Error): void} handlers.onPayloadError - ok 载荷缺陷展示。
 * @param {AbortSignal} [signal] - 调用方中止信号，与内部超时信号合并随请求发送。
 * @returns {Promise<Object|null>} 解析出的载荷对象，失败已消费时为 null。
 * @throws {DOMException|Error} 中止类异常为 DOMException；另含体读取失败
 * （TypeError 类）。
 */
export async function postJson(path, body, timeoutMs, handlers, signal) {
  return withTimeoutSignal(timeoutMs, async (timeoutSignal) => {
    // 外部取消与内部超时经 AbortSignal.any 汇流，中止类照常上抛交调用方分档。
    const requestSignal = signal
      ? AbortSignal.any([signal, timeoutSignal])
      : timeoutSignal;
    let response;
    try {
      response = await apiFetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: requestSignal,
      });
    } catch (error) {
      // 401 已弹令牌门，收场展示交调用方；中止类上抛走超时档，
      // 其余网络异常展示后收场。
      if (isUnauthorizedError(error)) {
        if (handlers.onUnauthorized) handlers.onUnauthorized();
        return null;
      }
      rethrowIfAbort(error);
      handlers.onNetworkError(error);
      return null;
    }
    if (!response.ok) {
      // 状态已是最终结论，读体触发的 abort 类异常按无载荷吸收，不上抛误入超时档。
      let errorPayload;
      try {
        errorPayload = await readErrorPayload(response);
      } catch {
        errorPayload = undefined;
      }
      const failure = normalizePayloadError(errorPayload, response);
      handlers.onEndpointError(failure);
      return null;
    }
    try {
      return await parseJsonRequired(response);
    } catch (error) {
      // 中止类上抛走超时档；载荷缺陷就地展示，体读取失败上抛走网络档。
      rethrowIfAbort(error);
      if (!(error instanceof PayloadError)) throw error;
      handlers.onPayloadError(error);
      return null;
    }
  });
}

/**
 * 原始 JSON 端点的共享响应尾：非 ok 弃体后按定制消息抛错，ok 载荷交严格
 * 解析；异常一律上抛由调用方 catch 分档。
 *
 * @param {Response} response - 原始响应。
 * @param {string} [statusMessage] - 非 2xx 失败的异常消息，缺省回退 HTTP 状态码。
 * @returns {Promise<Object>} 解析出的载荷对象。
 * @throws {Error} 非 2xx 状态。
 * @throws {PayloadError} 载荷缺陷。
 */
async function consumeJsonOrThrow(response, statusMessage) {
  if (!response.ok) {
    await discardBodyQuietly(response);
    throw new Error(statusMessage || `HTTP ${response.status}`);
  }
  return parseJsonRequired(response);
}

/**
 * 原始 JSON-POST：带令牌请求并返回解析后的载荷对象，不套端点信封语义；
 * body 缺省时发无体请求，供只携带查询参数的轻端点（生成 preflight）；
 * 401 经请求层弹令牌门后上抛，网络、非 2xx 与载荷异常一律上抛由调用方 catch。
 *
 * @param {string} path - 端点路径。
 * @param {number} timeoutMs - 超时毫秒数。
 * @param {Object} [body] - JSON 请求体，缺省不发。
 * @returns {Promise<Object>} 解析出的载荷对象。
 * @throws {DOMException|Error} 中止类为 DOMException，另含网络失败、非 2xx
 *   与载荷缺陷。
 */
export async function postJsonRaw(path, timeoutMs, body) {
  return withTimeoutSignal(timeoutMs, async (signal) => {
    const options = { method: "POST", signal };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const response = await apiFetch(path, options);
    return consumeJsonOrThrow(response);
  });
}

/**
 * 原始 GET-JSON：与 postJsonRaw 同一请求骨架的无体 GET 形态，不套端点信封
 * 语义，也不设内部超时（等待策略由调用方的 signal 决定）；非 2xx 的异常消息
 * 可由调用方定制；401 经请求层弹令牌门后上抛，网络与载荷异常一律上抛由调用方
 * catch。
 *
 * @param {string} path - 端点路径。
 * @param {AbortSignal} [signal] - 请求中止信号，是否限时由调用方决定。
 * @param {string} [statusMessage] - 非 2xx 失败的异常消息，缺省回退 HTTP 状态码。
 * @returns {Promise<Object>} 解析出的载荷对象。
 * @throws {DOMException|Error} 中止类为 DOMException，另含网络失败、非 2xx
 *   与载荷缺陷。
 */
export async function getJsonRaw(path, signal, statusMessage) {
  const response = await apiFetch(path, { method: "GET", signal });
  return consumeJsonOrThrow(response, statusMessage);
}
