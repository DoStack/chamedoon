"use client";

import { useEffect, useState } from "react";

import { getHealthUrl } from "@/lib/api";

type HealthState =
  | { status: "loading" }
  | { status: "ok"; database: string }
  | { status: "error" };

export function HealthStatus() {
  const [state, setState] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function loadHealth() {
      try {
        const response = await fetch(getHealthUrl(), {
          signal: controller.signal,
        });
        const payload = (await response.json()) as {
          status?: string;
          database?: string;
        };
        if (response.ok && payload.status === "ok") {
          setState({ status: "ok", database: payload.database ?? "ok" });
          return;
        }
        setState({ status: "error" });
      } catch {
        if (!controller.signal.aborted) {
          setState({ status: "error" });
        }
      }
    }

    void loadHealth();
    return () => controller.abort();
  }, []);

  if (state.status === "loading") {
    return <p className="text-sm text-slate-500">Checking API…</p>;
  }

  if (state.status === "ok") {
    return (
      <p className="text-sm text-emerald-700">
        API connected · database {state.database}
      </p>
    );
  }

  return (
    <p className="text-sm text-amber-700">
      API unavailable. Start the backend on port 8000.
    </p>
  );
}
