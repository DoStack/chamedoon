"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { getTelegramWebApp } from "@/lib/telegram";

const START_ROUTES: Record<string, string> = {
  demand: "/app/demand/new",
  supply: "/app/supply/new",
  requests: "/app/requests",
  matches: "/app/matches",
  explore: "/app/explore",
};

export function MiniAppBoot() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const webApp = getTelegramWebApp();
    webApp?.ready();
    webApp?.expand();
    const start =
      searchParams.get("startapp") ||
      searchParams.get("tgWebAppStartParam") ||
      webApp?.initDataUnsafe?.start_param;
    if (start && START_ROUTES[start]) {
      router.replace(START_ROUTES[start]);
      return;
    }
    if (typeof start === "string" && start.startsWith("request_")) {
      const pk = start.slice("request_".length);
      if (/^\d+$/.test(pk)) {
        router.replace(`/app/requests/${pk}`);
      }
    }
  }, [router, searchParams]);

  return null;
}
