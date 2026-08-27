"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, authTelegram, clearToken, fetchMe, getStoredToken, storeToken } from "@/lib/api";
import { localeFromLanguageCode, useI18n } from "@/lib/i18n";
import { waitForTelegramWebApp } from "@/lib/telegram";
import type { User } from "@/lib/types";

type DevLoginInput = {
  telegram_user_id: number;
  first_name: string;
  telegram_username?: string;
};

type AuthContextValue = {
  status: "loading" | "ready";
  user: User | null;
  error: string | null;
  loginDev: (input: DevLoginInput) => Promise<void>;
  retryTelegram: () => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { setLocale } = useI18n();
  const [status, setStatus] = useState<"loading" | "ready">("loading");
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  const applySession = useCallback((token: string, nextUser: User) => {
    storeToken(token);
    setUser(nextUser);
    setError(null);
    setStatus("ready");
  }, []);

  const boot = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const webApp = await waitForTelegramWebApp();
      webApp?.ready();
      webApp?.expand();
      const language = localeFromLanguageCode(webApp?.initDataUnsafe?.user?.language_code);
      if (language && !window.localStorage.getItem("koolbar_locale")) {
        setLocale(language);
      }
      if (webApp?.initData) {
        const payload = await authTelegram({ init_data: webApp.initData });
        applySession(payload.token, payload.user);
        return;
      }
      const token = getStoredToken();
      if (token) {
        const me = await fetchMe();
        setUser(me);
        setStatus("ready");
        return;
      }
      setUser(null);
      setStatus("ready");
    } catch (cause) {
      clearToken();
      setUser(null);
      setError(cause instanceof ApiError ? cause.message : "Could not sign in with Telegram.");
      setStatus("ready");
    }
  }, [applySession, setLocale]);

  useEffect(() => {
    void boot();
  }, [boot]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    setError(null);
    setStatus("ready");
  }, []);

  const loginDev = useCallback(
    async (input: DevLoginInput) => {
      setError(null);
      const payload = await authTelegram({
        dev_user: {
          telegram_user_id: input.telegram_user_id,
          first_name: input.first_name,
          telegram_username: input.telegram_username,
        },
      });
      applySession(payload.token, payload.user);
    },
    [applySession],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      error,
      loginDev,
      retryTelegram: boot,
      logout,
    }),
    [status, user, error, loginDev, boot, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return context;
}
