(function () {
  var ROUTES = {
    demand: "/app/demand/new/",
    supply: "/app/supply/new/",
    requests: "/app/requests/",
    matches: "/app/matches/",
    explore: "/app/explore/",
  };

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
    return "";
  }

  function samePath(left, right) {
    function norm(path) {
      if (!path) return "";
      return path.length > 1 ? path.replace(/\/+$/, "") : path;
    }
    return norm(left) === norm(right);
  }

  function shouldRedirect(pathname, dest) {
    if (!dest) return false;
    if (samePath(pathname, dest)) return false;
    return pathname === "/app" || pathname === "/app/";
  }

  var param = startParam();
  var dest = pathFor(param);
  window.koolbarStartParam = param;
  window.koolbarStartPath = dest;
  if (shouldRedirect(window.location.pathname, dest)) {
    window.location.replace(dest);
  }
})();
