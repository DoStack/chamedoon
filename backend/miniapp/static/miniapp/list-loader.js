(function () {
  var root = document.getElementById("listing");
  if (!root) return;

  var results = document.getElementById("listing-results");
  var src = root.getAttribute("data-list-src") || location.pathname + location.search;
  var errorText = root.getAttribute("data-list-error") || "";
  var retryText = root.getAttribute("data-list-retry") || "";
  var controller = null;

  function ready() {
    root.classList.add("is-ready");
    root.setAttribute("aria-busy", "false");
  }

  function showError() {
    results.innerHTML =
      '<p class="lede">' +
      errorText +
      '</p><p><button type="button" class="btn btn-secondary" data-list-retry>' +
      retryText +
      "</button></p>";
    var retry = results.querySelector("[data-list-retry]");
    if (retry) retry.addEventListener("click", load);
    ready();
  }

  function withListParam(url) {
    var parsed = new URL(url, location.origin);
    parsed.searchParams.set("list", "1");
    return parsed.pathname + parsed.search;
  }

  function isListFragment(html) {
    if (!html) return false;
    var head = html.slice(0, 400).toLowerCase();
    if (head.indexOf("<!doctype") !== -1 || head.indexOf("<html") !== -1) return false;
    return html.indexOf("koolbar-list") !== -1;
  }

  function applyListTitle() {
    var title = document.getElementById("explore-title");
    if (!title) return;
    var template = title.getAttribute("data-title-template") || "";
    var meta = results.querySelector("[data-list-count]");
    if (!template || !meta) return;
    title.textContent = template.replace("{count}", meta.getAttribute("data-list-count") || "0");
  }

  function load() {
    if (controller) controller.abort();
    controller = new AbortController();
    root.classList.remove("is-ready");
    root.setAttribute("aria-busy", "true");
    results.innerHTML = "";
    fetch(withListParam(src), {
      credentials: "same-origin",
      headers: { "X-Koolbar-List": "1", Accept: "text/html" },
      signal: controller.signal,
    })
      .then(function (response) {
        if (!response.ok) throw new Error("list");
        return response.text();
      })
      .then(function (html) {
        if (!isListFragment(html)) throw new Error("list");
        results.innerHTML = html;
        applyListTitle();
        ready();
      })
      .catch(function (error) {
        if (error && error.name === "AbortError") return;
        showError();
      });
  }

  window.koolbarLoadList = function (url) {
    src = url;
    root.setAttribute("data-list-src", url);
    load();
  };

  load();
})();
