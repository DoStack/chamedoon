import { getTelegramWebApp } from "@/lib/telegram";

const STORAGE_KEY = "koolbar:nav-stack";
const LOCK_MS = 450;

type OverlayCloser = () => boolean;

let locked = false;
let backHandler: (() => void) | null = null;
const overlayClosers: OverlayCloser[] = [];

function currentKey() {
  return `${window.location.pathname}${window.location.search}`;
}

function isMiniAppPath(path = window.location.pathname) {
  return path === "/app" || path.startsWith("/app/");
}

function loadStack(): string[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const stack = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(stack)) return [];
    return stack.filter((item): item is string => typeof item === "string" && item.startsWith("/app"));
  } catch {
    return [];
  }
}

function saveStack(stack: string[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stack));
  } catch {
    /* ignore quota / private mode */
  }
}

function navType() {
  const entries = performance.getEntriesByType("navigation");
  const entry = entries[0] as PerformanceNavigationTiming | undefined;
  return entry?.type ?? "navigate";
}

function lastIndex(stack: string[], key: string) {
  for (let i = stack.length - 1; i >= 0; i -= 1) {
    if (stack[i] === key) return i;
  }
  return -1;
}

export function reconcileStack(pathKey = currentKey()) {
  let stack = loadStack();
  const type = navType();
  const state = window.history.state as { kb?: number; kbKey?: string } | null;
  const fromHistory = Boolean(state?.kb && state.kbKey === pathKey);
  if (fromHistory || type === "back_forward" || type === "reload") {
    const found = lastIndex(stack, pathKey);
    stack = found >= 0 ? stack.slice(0, found + 1) : [...stack, pathKey];
  } else if (stack[stack.length - 1] !== pathKey) {
    stack = [...stack, pathKey];
  }
  saveStack(stack);
  return stack;
}

function overlayOpen() {
  if (overlayClosers.length > 0) return true;
  if (document.documentElement.classList.contains("sheet-open")) return true;
  return Boolean(document.querySelector("dialog[open]"));
}

function closeOverlay() {
  for (let i = overlayClosers.length - 1; i >= 0; i -= 1) {
    if (overlayClosers[i]()) {
      syncBackButton();
      return true;
    }
  }
  if (document.documentElement.classList.contains("sheet-open")) {
    const closeBtn = document.querySelector<HTMLElement>("[data-close-filters]");
    if (closeBtn) closeBtn.click();
    else document.documentElement.classList.remove("sheet-open");
    syncBackButton();
    return true;
  }
  const dialog = document.querySelector("dialog[open]");
  if (dialog && "close" in dialog && typeof dialog.close === "function") {
    dialog.close();
    syncBackButton();
    return true;
  }
  return false;
}

function previousRoute(stack: string[]) {
  return stack.length >= 2 ? stack[stack.length - 2] : "";
}

function closeApp() {
  const tg = getTelegramWebApp();
  if (tg?.close) {
    try {
      tg.close();
      return;
    } catch {
      /* fall through */
    }
  }
  if (window.history.length > 1) window.history.back();
}

export function goBack() {
  if (typeof window === "undefined") return;
  if (!isMiniAppPath()) return;
  if (locked) return;
  if (closeOverlay()) return;
  const stack = loadStack();
  const prev = previousRoute(stack);
  if (!prev) {
    closeApp();
    return;
  }
  locked = true;
  window.setTimeout(() => {
    locked = false;
  }, LOCK_MS);
  const state = window.history.state as { kb?: number; kbIdx?: number } | null;
  if (state?.kb && (state.kbIdx ?? 0) > 0) {
    window.history.back();
    return;
  }
  stack.pop();
  saveStack(stack);
  window.location.replace(prev);
}

export function hasPreviousRoute() {
  if (typeof window === "undefined") return false;
  return overlayOpen() || loadStack().length > 1;
}

export function syncBackButton() {
  const button = getTelegramWebApp()?.BackButton;
  if (!button) return;
  try {
    if (hasPreviousRoute()) button.show();
    else button.hide();
  } catch {
    /* ignore */
  }
}

export function registerOverlayCloser(closer: OverlayCloser) {
  overlayClosers.push(closer);
  syncBackButton();
  return () => {
    const index = overlayClosers.indexOf(closer);
    if (index >= 0) overlayClosers.splice(index, 1);
    syncBackButton();
  };
}

function bindBackButton() {
  const tg = getTelegramWebApp();
  const button = tg?.BackButton;
  if (!button) return;
  unbindBackButton();
  backHandler = goBack;
  try {
    button.onClick(backHandler);
  } catch {
    tg?.onEvent?.("backButtonClicked", backHandler);
  }
}

function unbindBackButton() {
  const tg = getTelegramWebApp();
  if (!tg || !backHandler) return;
  try {
    tg.BackButton?.offClick(backHandler);
  } catch {
    /* ignore */
  }
  try {
    tg.offEvent?.("backButtonClicked", backHandler);
  } catch {
    /* ignore */
  }
}

function stampHistory(stack: string[]) {
  try {
    window.history.replaceState(
      { ...(typeof window.history.state === "object" && window.history.state ? window.history.state : {}), kb: 1, kbIdx: Math.max(0, stack.length - 1), kbKey: currentKey() },
      "",
      window.location.href,
    );
  } catch {
    /* ignore */
  }
}

export function setupMiniAppBack() {
  if (typeof window === "undefined" || !isMiniAppPath()) {
    return () => undefined;
  }
  const stack = reconcileStack();
  stampHistory(stack);
  bindBackButton();
  syncBackButton();
  return () => {
    unbindBackButton();
  };
}
