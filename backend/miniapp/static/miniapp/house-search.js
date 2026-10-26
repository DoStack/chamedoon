// Chamedoon Home feed: the filter sheet (chips, live «نمایش N آگهی»), and Telegram links.
(function () {
  var form = document.getElementById("filter-form");
  var sheet = document.getElementById("filter-sheet");
  var openBtn = document.getElementById("open-filters");
  var applyBtn = document.getElementById("apply-filters");
  var cityInput = document.getElementById("home_city");
  if (!form || !sheet) return;

  function params() {
    var out = new URLSearchParams();
    new FormData(form).forEach(function (value, key) {
      value = String(value || "").trim();
      if (value) out.append(key, value);
    });
    return out;
  }

  function filterUrl() {
    return location.pathname + "?" + params().toString();
  }

  // Checkbox and radio chips: the input does the work, the chip shows it.
  function paint() {
    form.querySelectorAll("label.house-pick").forEach(function (chip) {
      var input = chip.querySelector("input");
      if (input) chip.classList.toggle("is-on", input.checked);
    });
  }

  form.querySelectorAll("[data-city]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      cityInput.value = chip.getAttribute("data-city") || "";
      form.querySelectorAll("[data-city]").forEach(function (el) {
        var on = el === chip;
        el.classList.toggle("is-on", on);
        el.setAttribute("aria-pressed", on ? "true" : "false");
      });
      // Neighbourhoods belong to a city: picking another city clears them.
      form.querySelectorAll('input[name="area"]').forEach(function (input) {
        if (input.type === "checkbox") input.checked = false;
        else input.value = "";
      });
      paint();
      refreshCount();
    });
  });

  // Live count on the sheet button, a moment after the last change.
  var countUrl = form.getAttribute("data-count-url") || "";
  var timer = null;
  var controller = null;
  function refreshCount() {
    if (!countUrl || !applyBtn || !window.fetch) return;
    clearTimeout(timer);
    timer = setTimeout(function () {
      if (controller && controller.abort) controller.abort();
      controller = window.AbortController ? new AbortController() : null;
      applyBtn.classList.add("is-counting");
      fetch(countUrl + "?" + params().toString(), {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller ? controller.signal : undefined,
      })
        .then(function (response) {
          if (!response.ok) throw new Error("count");
          return response.json();
        })
        .then(function (data) {
          if (data && data.label) applyBtn.textContent = data.label;
          applyBtn.classList.remove("is-counting");
        })
        .catch(function (error) {
          if (error && error.name === "AbortError") return;
          applyBtn.classList.remove("is-counting");
        });
    }, 250);
  }
  form.addEventListener("change", function () {
    paint();
    refreshCount();
  });
  form.addEventListener("input", function (event) {
    if (event.target && (event.target.type === "number" || event.target.type === "search")) refreshCount();
  });

  // The alert link opens the housing bot; inside Telegram, without closing the Mini App.
  document.querySelectorAll("[data-tg-link]").forEach(function (link) {
    link.addEventListener("click", function (event) {
      var webApp = window.Telegram && window.Telegram.WebApp;
      if (webApp && typeof webApp.openTelegramLink === "function" && webApp.initData) {
        event.preventDefault();
        webApp.openTelegramLink(link.href);
      }
    });
  });

  function openFilters() {
    sheet.hidden = false;
    if (openBtn) openBtn.setAttribute("aria-expanded", "true");
    document.documentElement.classList.add("sheet-open");
  }
  function closeFilters() {
    sheet.hidden = true;
    if (openBtn) openBtn.setAttribute("aria-expanded", "false");
    document.documentElement.classList.remove("sheet-open");
  }
  if (openBtn) openBtn.addEventListener("click", openFilters);
  document.querySelectorAll("[data-open-filters]").forEach(function (el) {
    el.addEventListener("click", openFilters);
  });
  sheet.querySelectorAll("[data-close-filters]").forEach(function (el) {
    el.addEventListener("click", closeFilters);
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    location.href = filterUrl();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !sheet.hidden) closeFilters();
  });
  if (/[?&]sheet=1(&|$)/.test(location.search)) openFilters();
})();
