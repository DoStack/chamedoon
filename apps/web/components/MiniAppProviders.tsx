"use client";

import { Vazirmatn } from "next/font/google";
import { type ReactNode, useState } from "react";

import { AuthProvider, useAuth } from "@/lib/auth";
import { I18nProvider, interpolate, useI18n } from "@/lib/i18n";
import { getTelegramWebApp } from "@/lib/telegram";
import { MiniAppBack } from "@/components/MiniAppBack";
import { MiniAppHeader } from "@/components/MiniAppHeader";

const vazirmatn = Vazirmatn({
  subsets: ["arabic", "latin"],
  display: "swap",
});

const inputClass =
  "h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-base text-slate-900 outline-none focus:border-slate-900";
const primaryButtonClass =
  "flex h-12 w-full items-center justify-center rounded-xl bg-slate-900 px-4 text-base font-medium text-white disabled:opacity-60";
const secondaryButtonClass =
  "flex h-12 w-full items-center justify-center rounded-xl border border-slate-300 bg-white px-4 text-base font-medium text-slate-900";

export function MiniAppProviders({ children }: { children: ReactNode }) {
  return (
    <I18nProvider>
      <AuthProvider>
        <MiniAppShell>{children}</MiniAppShell>
      </AuthProvider>
    </I18nProvider>
  );
}

function MiniAppShell({ children }: { children: ReactNode }) {
  const { dir, locale } = useI18n();
  return (
    <div
      dir={dir}
      lang={locale}
      className={`mx-auto min-h-full max-w-md px-4 py-6 ${locale === "fa" ? vazirmatn.className : ""}`}
    >
      <MiniAppBack />
      <MiniAppHeader />
      <AuthGate>{children}</AuthGate>
    </div>
  );
}

function AuthGate({ children }: { children: ReactNode }) {
  const { status, user, error, loginDev, retryTelegram, logout } = useAuth();
  const { messages } = useI18n();
  const [devId, setDevId] = useState("1");
  const [firstName, setFirstName] = useState("Dev");
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  if (status === "loading") {
    return <p className="mt-8 text-sm text-slate-500">{messages.common.loading}</p>;
  }

  if (!user) {
    return (
      <form
        className="mt-6 space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setFormError(null);
          try {
            await loginDev({
              telegram_user_id: Number(devId),
              first_name: firstName.trim() || "Dev",
              telegram_username: username.trim() || undefined,
            });
          } catch (cause) {
            setFormError(cause instanceof Error ? cause.message : messages.common.error);
          } finally {
            setBusy(false);
          }
        }}
      >
        <h1 className="text-xl font-semibold">{messages.auth.devTitle}</h1>
        <p className="text-sm leading-6 text-slate-600">{messages.auth.devHint}</p>
        {(error || formError) && <p className="text-sm text-amber-800">{formError || error}</p>}
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">{messages.auth.userId}</span>
          <input
            className={inputClass}
            inputMode="numeric"
            value={devId}
            onChange={(event) => setDevId(event.target.value)}
            required
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">{messages.auth.firstName}</span>
          <input
            className={inputClass}
            value={firstName}
            onChange={(event) => setFirstName(event.target.value)}
            required
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-slate-700">{messages.auth.username}</span>
          <input
            className={inputClass}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <button className={primaryButtonClass} disabled={busy} type="submit">
          {messages.auth.continue}
        </button>
        {error ? (
          <button className={secondaryButtonClass} type="button" onClick={() => void retryTelegram()}>
            {messages.common.retry}
          </button>
        ) : null}
      </form>
    );
  }

  return (
    <>
      <p className="mb-4 flex items-center justify-between gap-3 text-xs text-slate-500">
        <span>{interpolate(messages.common.signedInAs, { name: user.first_name })}</span>
        {!getTelegramWebApp()?.initData && (
          <button className="font-medium text-slate-700 underline" type="button" onClick={logout}>
            {messages.common.switchUser}
          </button>
        )}
      </p>
      {children}
    </>
  );
}
