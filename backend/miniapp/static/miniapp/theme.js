(function () {
  function applyTelegramTheme() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;
    tg.ready();
    tg.expand();
    var scheme = tg.colorScheme || "light";
    document.documentElement.dataset.tgColorScheme = scheme;
    document.documentElement.classList.toggle("tgui-dark", scheme === "dark");
    try {
      tg.setHeaderColor("secondary_bg_color");
      tg.setBackgroundColor("secondary_bg_color");
    } catch (e) {}
  }
  applyTelegramTheme();
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg && tg.onEvent) {
    tg.onEvent("themeChanged", applyTelegramTheme);
  }
})();
