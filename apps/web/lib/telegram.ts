export type TelegramWebAppUser = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
};

export type TelegramBackButton = {
  show: () => void;
  hide: () => void;
  onClick: (callback: () => void) => void;
  offClick: (callback: () => void) => void;
  isVisible?: boolean;
};

export type TelegramWebApp = {
  ready: () => void;
  expand: () => void;
  close?: () => void;
  openTelegramLink?: (url: string) => void;
  platform?: string;
  onEvent?: (event: string, callback: () => void) => void;
  offEvent?: (event: string, callback: () => void) => void;
  BackButton?: TelegramBackButton;
  initData?: string;
  initDataUnsafe?: {
    start_param?: string;
    user?: TelegramWebAppUser;
  };
};

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getTelegramWebApp(): TelegramWebApp | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.Telegram?.WebApp;
}

function usernameFromTelegramHref(url: string): string {
  const https = url.match(
    /^https?:\/\/(?:t\.me|telegram\.me)\/([A-Za-z0-9_]{3,32})\/?(\?[^#]*)?(#.*)?$/i,
  );
  if (https) {
    const query = (https[2] || "").replace(/^\?/, "");
    if (query) {
      const params = new URLSearchParams(query);
      for (const key of params.keys()) {
        if (key !== "text") return "";
      }
    }
    return https[1];
  }
  const resolve = url.match(/^tg:\/\/resolve\?domain=([A-Za-z0-9_]{3,32})\b/i);
  return resolve?.[1] || "";
}

function withQueryText(base: string, separator: string, text: string): string {
  if (!text) return base;
  const encoded = encodeURIComponent(text);
  const url = `${base}${separator}${encoded}`;
  if (url.length <= 1800) return url;
  const budget = 1800 - base.length - separator.length;
  if (budget < 8) return base;
  let cut = text;
  while (cut.length > 1) {
    cut = cut.slice(0, Math.max(1, Math.floor(cut.length * 0.8)));
    const next = encodeURIComponent(cut);
    if (next.length <= budget) return `${base}${separator}${next}`;
  }
  return base;
}

export function openTelegramDm(url: string, draft = ""): void {
  if (typeof window === "undefined") return;
  let text = draft;
  if (!text) {
    try {
      text = new URL(url, "https://t.me").searchParams.get("text") || "";
    } catch {
      text = "";
    }
  }
  const username = usernameFromTelegramHref(url);
  const href = username ? withQueryText(`https://t.me/${username}`, "?text=", text) : url;
  const tg = getTelegramWebApp();
  if (tg && typeof tg.openTelegramLink === "function") {
    try {
      tg.ready();
    } catch {
      /* ignore */
    }
    try {
      tg.openTelegramLink(href);
      return;
    } catch {
      /* fall through */
    }
  }
  window.location.href = href;
}

export function waitForTelegramWebApp(timeoutMs = 800): Promise<TelegramWebApp | undefined> {
  const existing = getTelegramWebApp();
  if (existing?.initData) {
    return Promise.resolve(existing);
  }

  return new Promise((resolve) => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      const webApp = getTelegramWebApp();
      if (webApp?.initData || Date.now() - started >= timeoutMs) {
        window.clearInterval(timer);
        resolve(webApp);
      }
    }, 50);
  });
}
