/* 装配层：hash 路由、事件绑定与启动流程。依赖其余全部模块。 */

"use strict";

import {
  $,
  hideTokenGate,
  showTokenGate,
  state,
  TOKEN_STORAGE_KEY,
} from "./api.js";
import { applyToolUI, loadConfigInfo, submitGenerate } from "./generate.js";
import {
  closeLightbox,
  refreshGallery,
  useLightboxAsReference,
} from "./gallery.js";
import { handleFiles, renderReferences } from "./refs.js";

export function applyRoute() {
  const view = location.hash === "#/gallery" ? "gallery" : "generate";
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
      renderReferences();
      $("ref-url").value = "";
    }
  });
  $("size").addEventListener("change", () => {
    $("custom-size-field").classList.toggle(
      "hidden",
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
    state.gallery.offset = Math.max(0, state.gallery.offset - 60);
    refreshGallery();
  });
  $("gallery-next").addEventListener("click", () => {
    if (state.gallery.hasMore) {
      state.gallery.offset += 60;
      refreshGallery();
    }
  });

  $("lightbox-close").addEventListener("click", closeLightbox);
  $("lightbox").addEventListener("click", (event) => {
    if (event.target === $("lightbox")) closeLightbox();
  });
  $("lightbox-use").addEventListener("click", useLightboxAsReference);

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
    } catch (error) {
      if (error.message !== "unauthorized") throw error;
      $("token-error").classList.remove("hidden");
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
  } catch (error) {
    if (error.message !== "unauthorized") {
      $("server-meta").textContent = "配置加载失败";
    }
  }
}

main();
