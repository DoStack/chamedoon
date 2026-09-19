(function () {
  var PARAMS = {
    bg_color: "--tg-theme-bg-color",
    text_color: "--tg-theme-text-color",
    hint_color: "--tg-theme-hint-color",
    link_color: "--tg-theme-link-color",
    button_color: "--tg-theme-button-color",
    button_text_color: "--tg-theme-button-text-color",
    secondary_bg_color: "--tg-theme-secondary-bg-color",
    header_bg_color: "--tg-theme-header-bg-color",
    accent_text_color: "--tg-theme-accent-text-color",
    section_bg_color: "--tg-theme-section-bg-color",
    section_header_text_color: "--tg-theme-section-header-text-color",
    subtitle_text_color: "--tg-theme-subtitle-text-color",
    destructive_text_color: "--tg-theme-destructive-text-color",
  };

  function applyTelegramTheme() {
    var root = document.documentElement;
    var tg = window.Telegram && window.Telegram.WebApp;
    var scheme = "light";
    if (tg) {
      try { tg.ready(); } catch (e) {}
      if (!window.koolbarStartParam) {
        try { tg.expand(); } catch (e) {}
      }
      scheme = tg.colorScheme === "dark" ? "dark" : "light";
      var params = tg.themeParams || {};
      Object.keys(PARAMS).forEach(function (key) {
        var value = params[key];
        if (value) root.style.setProperty(PARAMS[key], value);
      });
      try {
        tg.setHeaderColor("secondary_bg_color");
        tg.setBackgroundColor("secondary_bg_color");
      } catch (e) {}
      applyTelegramSafeArea(tg);
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      scheme = "dark";
    }
    root.dataset.tgColorScheme = scheme;
    root.classList.toggle("tgui-dark", scheme === "dark");
    root.style.colorScheme = scheme;
  }

  function applyTelegramSafeArea(tg) {
    var root = document.documentElement;
    var safe = (tg && tg.safeAreaInset) || {};
    var content = (tg && tg.contentSafeAreaInset) || {};
    root.style.setProperty("--tg-safe-area-inset-top", (Number(safe.top) || 0) + "px");
    root.style.setProperty("--tg-content-safe-area-inset-top", (Number(content.top) || 0) + "px");
  }

  applyTelegramTheme();
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.onEvent) {
    tg.onEvent("themeChanged", applyTelegramTheme);
    tg.onEvent("safeAreaChanged", function () { applyTelegramSafeArea(tg); });
    tg.onEvent("contentSafeAreaChanged", function () { applyTelegramSafeArea(tg); });
  } else if (window.matchMedia) {
    var media = window.matchMedia("(prefers-color-scheme: dark)");
    if (media.addEventListener) media.addEventListener("change", applyTelegramTheme);
    else if (media.addListener) media.addListener(applyTelegramTheme);
  }
})();
