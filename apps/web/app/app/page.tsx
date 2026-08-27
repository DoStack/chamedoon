"use client";

import Link from "next/link";
import { Suspense } from "react";

import { MiniAppBoot } from "@/components/MiniAppBoot";
import { primaryButtonClass, secondaryButtonClass } from "@/components/ui";
import { useI18n } from "@/lib/i18n";

export default function MiniAppHome() {
  const { messages } = useI18n();
  return (
    <div>
      <Suspense fallback={null}>
        <MiniAppBoot />
      </Suspense>
      <h1 className="text-3xl font-semibold tracking-tight">{messages.appName}</h1>
      <p className="mt-3 text-slate-600">{messages.home.prompt}</p>
      <div className="mt-8 flex flex-col gap-3">
        <Link className={primaryButtonClass} href="/app/demand/new">
          {messages.home.send}
        </Link>
        <Link className={primaryButtonClass} href="/app/supply/new">
          {messages.home.carry}
        </Link>
      </div>
      <hr className="my-8 border-slate-200" />
      <div className="flex flex-col gap-3">
        <Link className={secondaryButtonClass} href="/app/explore">
          {messages.home.explore}
        </Link>
        <Link className={secondaryButtonClass} href="/app/requests">
          {messages.home.requests}
        </Link>
        <Link className={secondaryButtonClass} href="/app/matches">
          {messages.home.matches}
        </Link>
      </div>
    </div>
  );
}
