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

  function currentKey() {
    return location.pathname + location.search;
  }

  function loadStack() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      var stack = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(stack)) return [];
      return stack.filter(function (item) {
        return typeof item === "string" && item.indexOf("/app") === 0;
      });
    } catch (e) {
      return [];
    }
  }

  function saveStack(stack) {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(stack));
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

  function reconcileStack() {
    var key = currentKey();
    var stack = loadStack();
    var type = navType();
    var state = history.state;
    var fromHistory = state && state.kb && state.kbKey === key;
    if (fromHistory || type === "back_forward" || type === "reload") {
      var found = lastIndex(stack, key);
      if (found >= 0) stack = stack.slice(0, found + 1);
      else stack.push(key);
    } else if (stack[stack.length - 1] !== key) {
      stack.push(key);
    }
    saveStack(stack);
    return stack;
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
      if (stack.length && stack[stack.length - 1] !== key) {
        stack[stack.length - 1] = key;
        saveStack(stack);
      }
      syncBackButton();
    };
  }

  function overlayOpen() {
    if (document.documentElement.classList.contains("sheet-open")) return true;
    return Boolean(document.querySelector("dialog[open]"));
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
    return false;
  }

  function previousRoute(stack) {
    if (!stack || stack.length < 2) return "";
    return stack[stack.length - 2] || "";
  }

  function closeApp() {
    var tg = webApp();
    if (tg && typeof tg.close === "function") {
      try {
        tg.close();
        return;
      } catch (e) {}
    }
    if (window.history.length > 1) history.back();
  }

  function goBack() {
    if (!isMiniAppPath()) return;
    if (locked) return;
    if (closeOverlay()) return;
    var stack = loadStack();
    var prev = previousRoute(stack);
    if (!prev) {
      closeApp();
      return;
    }
    locked = true;
    window.setTimeout(function () {
      locked = false;
    }, LOCK_MS);
    var state = history.state;
    if (state && state.kb && state.kbIdx > 0) {
      history.back();
      return;
    }
    stack.pop();
    saveStack(stack);
    location.replace(prev);
  }

  function syncBackButton() {
    var tg = webApp();
    if (!tg || !tg.BackButton) return;
    var canBack = overlayOpen() || loadStack().length > 1;
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
})();
