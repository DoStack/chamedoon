"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { setupMiniAppBack } from "@/lib/nav";

export function MiniAppBack() {
  const pathname = usePathname();

  useEffect(() => setupMiniAppBack(), [pathname]);

  return null;
}
