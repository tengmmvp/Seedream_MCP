/**
 * @fileoverview 装配层：hash 路由、事件绑定与启动流程。依赖其余全部模块。
 */

"use strict";

import { $, hideTokenGate, state, TOKEN_STORAGE_KEY } from "./api.js";
import { applyToolUI, loadConfigInfo, submitGenerate } from "./generate.js";
import {
  closeLightbox,
  GALLERY_PAGE_SIZE,
  refreshGallery,
  useLightboxAsReference,
} from "./gallery.js";
import { handleFiles, renderReferences } from "./refs.js";

function currentView() {
  return location.hash === "#/gallery" ? "gallery" : "generate";
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
    if (!button) return;
    state.tool = button.dataset.tool;
    document.querySelectorAll("#tool-tabs button").forEach((b) => {
      b.classList.toggle("active", b === button);
    });
    applyToolUI();
  });

  $("ref-upload").addEventListener("click", () => $("ref-file").click());
  $("ref-file").addEventListener("change", (event) => {
    handleFiles(event.target.files);
    event.target.value = "";
  });
  $("ref-add-url").addEventListener("click", () => {
    const url = $("ref-url").value.trim();
    if (url) {
      state.refs.push({ kind: "url", value: url, preview: null });
      renderReferences(state.refs.length - 1);
      $("ref-url").value = "";
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
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    try {
      await loadConfigInfo();
      hideTokenGate();
      $("token-error").classList.add("hidden");
      applyToolUI();
      // 直达 #/gallery 时首刷发生在 config-info 之前而误判空态，令牌补齐后重刷。
      if (currentView() === "gallery") refreshGallery();
    } catch (error) {
      $("token-error").classList.remove("hidden");
      if (error.message === "unauthorized") {
        $("token-error").textContent = "令牌无效，请重试。";
      } else {
        $("token-error").textContent = "服务器异常，请重试";
        console.error(error);
      }
      state.token = "";
      localStorage.removeItem(TOKEN_STORAGE_KEY);
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
    await loadConfigInfo();
    hideTokenGate();
    applyToolUI();
    // 直达 #/gallery 时首刷发生在 config-info 之前而误判空态，配置就绪后补刷。
    if (currentView() === "gallery") refreshGallery();
  } catch (error) {
    if (error.message !== "unauthorized") {
      $("server-meta").textContent = "配置加载失败";
    }
  }
}

main();
