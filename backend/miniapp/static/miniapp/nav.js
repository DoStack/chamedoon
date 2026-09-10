(function () {
  var STORAGE_KEY = "koolbar:nav-stack";
  var LOCK_MS = 450;
  var locked = false;
  var backHandler = null;
  var nativeReplace = null;
  var observer = null;

  function webApp() {
    return window.Telegram && window.Telegram.WebApp;
  }

  function isMiniAppPath(path) {
    path = path || location.pathname;
    return path === "/app" || path.indexOf("/app/") === 0;
  }

  function normalizeKey(href) {
    if (!href) return "";
    var url;
    try {
      url = new URL(href, location.origin);
    } catch (e) {
      return "";
    }
    if (!isMiniAppPath(url.pathname)) return "";
    url.searchParams.delete("list");
    url.searchParams.delete("startapp");
    url.searchParams.delete("tgWebAppStartParam");
    var path = url.pathname || "/";
    if (path === "/app") path = "/app/";
    if (path.length > 1 && path.charAt(path.length - 1) !== "/") path += "/";
    var search = url.searchParams.toString();
    return path + (search ? "?" + search : "");
  }

  function currentKey() {
    return normalizeKey(location.pathname + location.search);
  }

  function routeFamily(key) {
    var path = String(key || "").split("?")[0];
    if (path.length > 1 && path.charAt(path.length - 1) === "/") {
      path = path.slice(0, -1);
    }
    return path;
  }

  function sameFamily(left, right) {
    return Boolean(left) && Boolean(right) && routeFamily(left) === routeFamily(right);
  }

  function collapse(stack) {
    var out = [];
    for (var i = 0; i < stack.length; i += 1) {
      var key = normalizeKey(stack[i]);
      if (!key) continue;
      if (out.length && out[out.length - 1] === key) continue;
      if (out.length && sameFamily(out[out.length - 1], key)) {
        out[out.length - 1] = key;
        continue;
      }
      out.push(key);
    }
    return out;
  }

  function loadStack() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      var stack = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(stack)) return [];
      return collapse(stack);
    } catch (e) {
      return [];
    }
  }

  function saveStack(stack) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(collapse(stack)));
    } catch (e) {}
  }

  function navType() {
    try {
      var entries = performance.getEntriesByType("navigation");
      if (entries && entries[0] && entries[0].type) return entries[0].type;
    } catch (e) {}
    return "navigate";
  }

  function lastIndex(stack, key) {
    for (var i = stack.length - 1; i >= 0; i -= 1) {
      if (stack[i] === key) return i;
    }
    return -1;
  }

  function lastFamilyIndex(stack, key) {
    for (var i = stack.length - 1; i >= 0; i -= 1) {
      if (sameFamily(stack[i], key)) return i;
    }
    return -1;
  }

  function parentFallback(key) {
    var path = String(key || currentKey()).split("?")[0];
    var created = /^\/app\/requests\/(\d+)\/created\/?$/.exec(path);
    if (created) return "/app/requests/" + created[1] + "/";
    var explore = /^\/app\/explore\/(\d+)\/?$/.exec(path);
    if (explore) return "/app/explore/";
    var request = /^\/app\/requests\/(\d+)\/?$/.exec(path);
    if (request) return "/app/requests/";
    var match = /^\/app\/matches\/(\d+)\/?$/.exec(path);
    if (match) return "/app/matches/";
    var ticket = /^\/app\/support\/(\d+)\/?$/.exec(path);
    if (ticket) return "/app/support/";
    if (path === "/app/support/new/" || path === "/app/support/new") return "/app/support/";
    if (
      path === "/app/explore/" ||
      path === "/app/requests/" ||
      path === "/app/matches/" ||
      path === "/app/support/" ||
      path === "/app/about/" ||
      path.indexOf("/app/demand/") === 0 ||
      path.indexOf("/app/supply/") === 0
    ) {
      return "/app/";
    }
    if (path === "/app/" || path === "/app/login/") return "";
    return "/app/";
  }

  function previousRoute(stack) {
    var key = currentKey();
    for (var i = stack.length - 1; i >= 0; i -= 1) {
      if (stack[i] !== key && !sameFamily(stack[i], key)) return stack[i];
    }
    return parentFallback(key);
  }

  function reconcileStack() {
    var key = currentKey();
    var stack = loadStack();
    var type = navType();
    var state = history.state;
    var fromHistory = state && state.kb && normalizeKey(state.kbKey || "") === key;
    if (fromHistory || type === "back_forward" || type === "reload") {
      var found = lastIndex(stack, key);
      if (found < 0) found = lastFamilyIndex(stack, key);
      if (found >= 0) stack = stack.slice(0, found + 1);
      else if (stack[stack.length - 1] !== key) stack.push(key);
    } else if (stack[stack.length - 1] !== key) {
      if (stack.length && sameFamily(stack[stack.length - 1], key)) {
        stack[stack.length - 1] = key;
      } else {
        stack.push(key);
      }
    }
    saveStack(stack);
    return loadStack();
  }

  function stampHistory(stack) {
    if (!nativeReplace) return;
    try {
      nativeReplace.call(history, { kb: 1, kbIdx: Math.max(0, stack.length - 1), kbKey: currentKey() }, "", location.href);
    } catch (e) {}
  }

  function patchReplaceState() {
    if (nativeReplace) return;
    nativeReplace = history.replaceState.bind(history);
    history.replaceState = function (state, title, url) {
      var prev = history.state && typeof history.state === "object" ? history.state : {};
      var merged = {};
      if (prev.kb) {
        merged.kb = 1;
        merged.kbIdx = prev.kbIdx;
        merged.kbKey = prev.kbKey;
      }
      if (state && typeof state === "object") {
        Object.keys(state).forEach(function (key) {
          merged[key] = state[key];
        });
      }
      nativeReplace(Object.keys(merged).length ? merged : state, title, url);
      var key = currentKey();
      var stack = loadStack();
      if (stack.length && !sameFamily(stack[stack.length - 1], key)) {
        return;
      }
      if (stack.length) {
        stack[stack.length - 1] = key;
        saveStack(stack);
      }
      syncBackButton();
    };
  }

  var backGuards = [];

  function guardIsActive(guard) {
    if (!guard) return false;
    if (typeof guard.active === "function") return Boolean(guard.active());
    return true;
  }

  function overlayOpen() {
    if (document.documentElement.classList.contains("sheet-open")) return true;
    if (document.querySelector("dialog[open]")) return true;
    for (var i = 0; i < backGuards.length; i += 1) {
      if (guardIsActive(backGuards[i])) return true;
    }
    return false;
  }

  function closeOverlay() {
    if (document.documentElement.classList.contains("sheet-open")) {
      var closeBtn = document.querySelector("[data-close-filters]");
      if (closeBtn) closeBtn.click();
      else {
        document.documentElement.classList.remove("sheet-open");
        var sheet = document.getElementById("filter-sheet");
        if (sheet) sheet.hidden = true;
      }
      syncBackButton();
      return true;
    }
    var dialog = document.querySelector("dialog[open]");
    if (dialog && typeof dialog.close === "function") {
      dialog.close();
      syncBackButton();
      return true;
    }
    for (var i = backGuards.length - 1; i >= 0; i -= 1) {
      var guard = backGuards[i];
      if (!guardIsActive(guard) || typeof guard.back !== "function") continue;
      if (guard.back()) {
        syncBackButton();
        return true;
      }
    }
    return false;
  }

  function registerBackGuard(guard) {
    if (!guard || typeof guard.back !== "function") return function () {};
    backGuards.push(guard);
    syncBackButton();
    return function () {
      var idx = backGuards.indexOf(guard);
      if (idx >= 0) backGuards.splice(idx, 1);
      syncBackButton();
    };
  }

  function closeApp() {
    var tg = webApp();
    if (tg && typeof tg.close === "function") {
      try {
        tg.close();
        return;
      } catch (e) {}
    }
    if (currentKey() !== "/app/") location.replace("/app/");
  }

  function goBack() {
    if (!isMiniAppPath()) return;
    if (locked) return;
    if (closeOverlay()) return;
    var stack = loadStack();
    var key = currentKey();
    var prev = previousRoute(stack);
    if (!prev || prev === key || sameFamily(prev, key)) {
      prev = parentFallback(key);
    }
    if (!prev || prev === key || sameFamily(prev, key)) {
      closeApp();
      return;
    }
    locked = true;
    window.setTimeout(function () {
      locked = false;
    }, LOCK_MS);
    while (stack.length && (stack[stack.length - 1] === key || sameFamily(stack[stack.length - 1], key))) {
      stack.pop();
    }
    saveStack(stack);
    location.replace(prev);
  }

  function syncBackButton() {
    var tg = webApp();
    if (!tg || !tg.BackButton) return;
    var canBack = overlayOpen() || Boolean(previousRoute(loadStack()) || parentFallback(currentKey()));
    try {
      if (canBack) tg.BackButton.show();
      else tg.BackButton.hide();
    } catch (e) {}
  }

  function bindBackButton() {
    var tg = webApp();
    if (!tg || !tg.BackButton) return;
    unbindBackButton();
    backHandler = goBack;
    try {
      tg.BackButton.onClick(backHandler);
    } catch (e) {
      if (tg.onEvent) tg.onEvent("backButtonClicked", backHandler);
    }
  }

  function unbindBackButton() {
    var tg = webApp();
    if (!tg || !backHandler) return;
    try {
      if (tg.BackButton && tg.BackButton.offClick) tg.BackButton.offClick(backHandler);
    } catch (e) {}
    try {
      if (tg.offEvent) tg.offEvent("backButtonClicked", backHandler);
    } catch (e) {}
  }

  function onHeaderClick(event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var link = target.closest("[data-nav-back]");
    if (!link || !isMiniAppPath()) return;
    event.preventDefault();
    goBack();
  }

  function onTelegramLinkClick(event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var link = target.closest('a[href^="https://t.me/"]');
    if (!link || !isMiniAppPath()) return;
    var tg = webApp();
    if (!tg || typeof tg.openTelegramLink !== "function") return;
    event.preventDefault();
    try {
      tg.openTelegramLink(link.href);
    } catch (e) {}
  }

  function watchOverlays() {
    if (observer || !window.MutationObserver) return;
    observer = new MutationObserver(syncBackButton);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  }

  function boot() {
    if (!isMiniAppPath()) return;
    patchReplaceState();
    var stack = reconcileStack();
    stampHistory(stack);
    bindBackButton();
    watchOverlays();
    syncBackButton();
  }

  function teardown() {
    unbindBackButton();
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  document.addEventListener("click", onHeaderClick);
  document.addEventListener("click", onTelegramLinkClick);
  window.addEventListener("pagehide", teardown);
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) boot();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.koolbarGoBack = goBack;
  window.koolbarSyncBack = syncBackButton;
  window.koolbarRegisterBack = registerBackGuard;
})();
