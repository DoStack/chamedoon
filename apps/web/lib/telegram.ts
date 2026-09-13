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
  if (text) {
    void navigator.clipboard?.writeText(text).catch(() => undefined);
  }
  const username = usernameFromTelegramHref(url);
  const tg = getTelegramWebApp();
  const inTelegram = Boolean(tg?.initData || (tg && tg.initDataUnsafe?.user));
  if (username) {
    const resolve = `tg://resolve?domain=${encodeURIComponent(username)}${
      text ? `&text=${encodeURIComponent(text)}` : ""
    }`;
    const https = `https://t.me/${username}`;
    const httpsText = text && `${https}?text=${encodeURIComponent(text)}`.length <= 2048
      ? `${https}?text=${encodeURIComponent(text)}`
      : https;
    if (inTelegram) {
      try {
        window.location.href = resolve;
        return;
      } catch {
        /* try Mini App helper next */
      }
      try {
        tg?.openTelegramLink?.(httpsText);
        return;
      } catch {
        window.location.href = httpsText;
        return;
      }
    }
    window.location.href = httpsText;
    return;
  }
  if (url.startsWith("https://t.me/") || url.startsWith("https://telegram.me/")) {
    try {
      tg?.openTelegramLink?.(url);
      return;
    } catch {
      /* fall through */
    }
  }
  window.location.href = url;
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
