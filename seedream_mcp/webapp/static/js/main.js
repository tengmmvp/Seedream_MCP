/**
 * @fileoverview 装配层：hash 路由、事件绑定与启动流程。依赖其余全部模块。
 */

"use strict";

import {
  $,
  clearStoredToken,
  hideTokenGate,
  setActiveTool,
  state,
  writeStoredToken,
} from "./api.js";
import { applyToolUI, loadConfigInfo, submitGenerate } from "./generate.js";
import {
  closeLightbox,
  GALLERY_PAGE_SIZE,
  refreshGallery,
  useLightboxAsReference,
} from "./gallery.js";
import { addReference, handleFiles } from "./refs.js";

function currentView() {
  return location.hash === "#/gallery" ? "gallery" : "generate";
}

// 令牌补齐与首次启动共用的初始化序列：配置就绪后进入工作台并补刷图库。
async function bootstrapAfterAuth() {
  await loadConfigInfo();
  hideTokenGate();
  applyToolUI();
  // 直达 #/gallery 时首刷在 config-info 就绪前空转，配置就绪后补刷。
  if (currentView() === "gallery") refreshGallery();
}

/** 按 hash 切换生成台与图库视图，进入图库时触发刷新。 */
export function applyRoute() {
  const view = currentView();
  $("view-generate").classList.toggle("hidden", view !== "generate");
  $("view-gallery").classList.toggle("hidden", view !== "gallery");
  document.querySelectorAll(".view-switch a").forEach((link) => {
    link.classList.toggle("active", link.dataset.view === view);
  });
  if (view === "gallery") refreshGallery();
}

function bindEvents() {
  $("tool-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tool]");
    if (!button || button.disabled) return;
    setActiveTool(button.dataset.tool);
    applyToolUI();
  });
  // tablist 方向键导航：左右键在未禁用的工具间移动。
  $("tool-tabs").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const buttons = [...document.querySelectorAll("#tool-tabs button")];
    const enabled = buttons.filter((b) => !b.disabled);
    if (enabled.length < 2) return;
    const index = enabled.indexOf(document.activeElement);
    if (index === -1) return;
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = enabled[(index + delta + enabled.length) % enabled.length];
    next.focus();
    next.click();
  });

  $("ref-upload").addEventListener("click", () => $("ref-file").click());
  $("ref-file").addEventListener("change", (event) => {
    handleFiles(event.target.files);
    event.target.value = "";
  });
  $("ref-add-url").addEventListener("click", () => {
    const url = $("ref-url").value.trim();
    if (url) {
      // addReference 拒绝时保留输入，用户刚粘贴的 URL 不被抹掉。
      if (addReference("url", url) === null) {
        $("ref-url").value = "";
      }
    }
  });
  // URL 输入框内回车执行「添加」，不触发表单隐式提交。
  $("ref-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("ref-add-url").click();
    }
  });
  $("size").addEventListener("change", () => {
    $("custom-size-field").classList.toggle(
      "collapsed",
      $("size").value !== "custom",
    );
  });
  $("generate-form").addEventListener("submit", submitGenerate);

  $("gallery-refresh").addEventListener("click", () => {
    state.gallery.offset = 0;
    refreshGallery();
  });
  $("format-filter").addEventListener("change", () => {
    state.gallery.offset = 0;
    refreshGallery();
  });
  $("gallery-prev").addEventListener("click", () => {
    state.gallery.offset = Math.max(
      0,
      state.gallery.offset - GALLERY_PAGE_SIZE,
    );
    refreshGallery();
  });
  $("gallery-next").addEventListener("click", () => {
    if (state.gallery.hasMore) {
      state.gallery.offset += GALLERY_PAGE_SIZE;
      refreshGallery();
    }
  });

  $("lightbox-close").addEventListener("click", closeLightbox);
  $("lightbox").addEventListener("click", (event) => {
    if (event.target === $("lightbox")) closeLightbox();
  });
  $("lightbox-use").addEventListener("click", useLightboxAsReference);
  // 灯箱可见时 Escape 等价点击关闭。
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("lightbox").classList.contains("hidden")) {
      closeLightbox();
    }
  });

  $("token-submit").addEventListener("click", async () => {
    const token = $("token-input").value.trim();
    if (!token) return;
    state.token = token;
    writeStoredToken(token);
    try {
      await bootstrapAfterAuth();
      $("token-error").classList.add("hidden");
    } catch (error) {
      $("token-error").classList.remove("hidden");
      if (error.message === "unauthorized") {
        $("token-error").textContent = "令牌无效，请重试。";
        state.token = "";
        clearStoredToken();
      } else {
        // 瞬时失败（网络抖动、5xx）保留令牌，输入框未清空可直接重试。
        $("token-error").textContent = "服务器异常，请重试";
        console.error(error);
      }
    }
  });
  $("token-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("token-submit").click();
  });

  window.addEventListener("hashchange", applyRoute);
}

async function main() {
  bindEvents();
  applyRoute();
  try {
    await bootstrapAfterAuth();
  } catch (error) {
    if (error.message !== "unauthorized") {
      $("server-meta").textContent = "配置加载失败，请刷新页面重试";
    }
  }
}

main();
