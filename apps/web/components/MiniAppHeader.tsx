"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useI18n } from "@/lib/i18n";

export function MiniAppHeader() {
  const pathname = usePathname();
  const { locale, setLocale, messages } = useI18n();
  const showBack = pathname !== "/app";

  return (
    <header className="mb-6 flex items-center justify-between gap-3">
      {showBack ? (
        <Link href="/app" className="text-sm font-medium text-slate-700">
          ← {messages.common.back}
        </Link>
      ) : (
        <p className="text-sm font-medium uppercase tracking-[0.2em] text-slate-500">
          {messages.appName}
        </p>
      )}
      <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 text-xs font-medium">
        <button
          type="button"
          className={`rounded-md px-2 py-1 ${locale === "en" ? "bg-slate-900 text-white" : "text-slate-600"}`}
          onClick={() => setLocale("en")}
        >
          {messages.language.en}
        </button>
        <button
          type="button"
          className={`rounded-md px-2 py-1 ${locale === "fa" ? "bg-slate-900 text-white" : "text-slate-600"}`}
          onClick={() => setLocale("fa")}
        >
          {messages.language.fa}
        </button>
      </div>
    </header>
  );
}
