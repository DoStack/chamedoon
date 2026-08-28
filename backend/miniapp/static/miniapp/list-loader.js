(function () {
  var root = document.getElementById("listing");
  if (!root) return;

  var results = document.getElementById("listing-results");
  var src = root.getAttribute("data-list-src") || location.pathname + location.search;
  var errorText = root.getAttribute("data-list-error") || "";
  var retryText = root.getAttribute("data-list-retry") || "";

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

  function load() {
    root.classList.remove("is-ready");
    root.setAttribute("aria-busy", "true");
    results.innerHTML = "";
    fetch(src, {
      credentials: "same-origin",
      headers: { "X-Koolbar-List": "1", Accept: "text/html" },
    })
      .then(function (response) {
        if (!response.ok) throw new Error("list");
        return response.text();
      })
      .then(function (html) {
        results.innerHTML = html;
        ready();
      })
      .catch(showError);
  }

  load();
})();
