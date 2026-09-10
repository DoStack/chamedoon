(function () {
  var ROUTES = {
    demand: "/app/demand/new/",
    supply: "/app/supply/new/",
    requests: "/app/requests/",
    matches: "/app/matches/",
    explore: "/app/explore/",
    support: "/app/support/",
  };
  var ENTRY = {
    "/": true,
    "/app": true,
    "/app/": true,
    "/app/login": true,
    "/app/login/": true,
  };
  var CONSUMED_KEY = "koolbar:startapp-consumed";

  function webApp() {
    return window.Telegram && window.Telegram.WebApp;
  }

  function startParam() {
    var tg = webApp();
    try {
      if (tg && typeof tg.ready === "function") tg.ready();
    } catch (e) {}
    if (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) {
      return String(tg.initDataUnsafe.start_param);
    }
    var search = new URLSearchParams(window.location.search || "");
    var fromQuery = search.get("startapp") || search.get("tgWebAppStartParam") || "";
    if (fromQuery) return fromQuery;
    var hash = String(window.location.hash || "").replace(/^#/, "");
    if (!hash) return "";
    return new URLSearchParams(hash).get("tgWebAppStartParam") || "";
  }

  function pathFor(value) {
    value = String(value || "").trim();
    if (!value) return "";
    if (ROUTES[value]) return ROUTES[value];
    var requestMatch = /^request_(\d+)$/.exec(value);
    if (requestMatch) return "/app/requests/" + requestMatch[1] + "/";
    var exploreMatch = /^explore_(\d+)$/.exec(value);
    if (exploreMatch) return "/app/explore/" + exploreMatch[1] + "/";
    var matchMatch = /^match_(\d+)$/.exec(value);
    if (matchMatch) return "/app/matches/" + matchMatch[1] + "/";
    var ticketMatch = /^ticket_(\d+)$/.exec(value);
    if (ticketMatch) return "/app/support/" + ticketMatch[1] + "/";
    return "";
  }

  function samePath(left, right) {
    function norm(path) {
      if (!path) return "";
      return path.length > 1 ? path.replace(/\/+$/, "") : path;
    }
    return norm(left) === norm(right);
  }

  function consumed() {
    try {
      return sessionStorage.getItem(CONSUMED_KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function markConsumed(value) {
    if (!value) return;
    try {
      sessionStorage.setItem(CONSUMED_KEY, value);
    } catch (e) {}
  }

  function navType() {
    try {
      var entries = performance.getEntriesByType("navigation");
      if (entries && entries[0] && entries[0].type) return entries[0].type;
    } catch (e) {}
    return "navigate";
  }

  function isLoginPath(pathname) {
    return pathname.indexOf("/app/login") === 0;
  }

  function shouldRedirect(pathname, dest) {
    if (!dest) return false;
    if (samePath(pathname, dest)) return false;
    if (isLoginPath(pathname)) return false;
    return Boolean(ENTRY[pathname]);
  }

  function stripStartappFromUrl() {
    if (isLoginPath(window.location.pathname)) return;
    var url = new URL(window.location.href);
    if (!url.searchParams.has("startapp") && !url.searchParams.has("tgWebAppStartParam")) return;
    url.searchParams.delete("startapp");
    url.searchParams.delete("tgWebAppStartParam");
    history.replaceState(history.state, "", url.pathname + url.search);
  }

  var param = startParam();
  var dest = pathFor(param);
  window.koolbarStartParam = param;
  window.koolbarStartPath = dest;
  var already = Boolean(param) && consumed() === param;
  var goingBack = navType() === "back_forward";
  if (param && dest && !already && !goingBack && shouldRedirect(window.location.pathname, dest)) {
    markConsumed(param);
    window.location.replace(dest);
    return;
  }
  if (param) markConsumed(param);
  stripStartappFromUrl();
})();
