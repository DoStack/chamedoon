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

import en from "@/messages/en.json";
import fa from "@/messages/fa.json";
import type { Locale } from "@/lib/types";

const MESSAGES = { en, fa } as const;
const STORAGE_KEY = "koolbar_locale";

export type Messages = typeof en;

type I18nContextValue = {
  locale: Locale;
  dir: "ltr" | "rtl";
  messages: Messages;
  setLocale: (locale: Locale) => void;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function interpolate(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(vars[key] ?? ""));
}

function readStoredLocale(): Locale | null {
  if (typeof window === "undefined") {
    return null;
  }
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored === "fa" || stored === "en" ? stored : null;
}

export function localeFromLanguageCode(code: string | undefined): Locale | null {
  if (!code) {
    return null;
  }
  const lower = code.toLowerCase();
  if (lower.startsWith("fa")) {
    return "fa";
  }
  if (lower.startsWith("en")) {
    return "en";
  }
  return null;
}

export function I18nProvider({
  children,
  initialLocale = "en",
}: {
  children: ReactNode;
  initialLocale?: Locale;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  useEffect(() => {
    const stored = readStoredLocale();
    if (stored) {
      setLocaleState(stored);
    }
  }, []);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      dir: locale === "fa" ? "rtl" : "ltr",
      messages: MESSAGES[locale],
      setLocale,
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
