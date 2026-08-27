export type TelegramWebAppUser = {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
};

export type TelegramWebApp = {
  ready: () => void;
  expand: () => void;
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
