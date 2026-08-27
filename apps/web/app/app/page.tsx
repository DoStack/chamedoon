"use client";

import Link from "next/link";
import { Suspense } from "react";

import { MiniAppBoot } from "@/components/MiniAppBoot";
import { TguiIcon } from "@/components/TguiIcon";
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
        <Link className={`${primaryButtonClass} gap-2`} href="/app/demand/new">
          <TguiIcon name="archive" size={28} />
          {messages.home.send}
        </Link>
        <Link className={`${primaryButtonClass} gap-2`} href="/app/supply/new">
          <TguiIcon name="devices" size={28} />
          {messages.home.carry}
        </Link>
      </div>
      <hr className="my-8 border-slate-200" />
      <div className="flex flex-col gap-3">
        <Link className={`${secondaryButtonClass} gap-2`} href="/app/explore">
          <TguiIcon name="channel" size={24} />
          {messages.home.explore}
        </Link>
        <Link className={`${secondaryButtonClass} gap-2`} href="/app/requests">
          <TguiIcon name="attach" size={28} />
          {messages.home.requests}
        </Link>
        <Link className={`${secondaryButtonClass} gap-2`} href="/app/matches">
          <TguiIcon name="heart" size={28} />
          {messages.home.matches}
        </Link>
      </div>
    </div>
  );
}
