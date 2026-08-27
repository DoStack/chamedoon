"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchMatches } from "@/lib/api";
import { routeLabel } from "@/lib/catalog";
import { formatDateRange, formatKg } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Match } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";

export default function MatchesPage() {
  const { locale, messages } = useI18n();
  const { locations, loading: catalogLoading } = useCatalog();
  const [items, setItems] = useState<Match[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMatches()
      .then((payload) => {
        if (!cancelled) {
          setItems(payload);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(messages.common.error);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [messages.common.error]);

  if (!items || catalogLoading) {
    return <p className="text-sm text-slate-500">{messages.common.loading}</p>;
  }
  if (error) {
    return <p className="text-sm text-amber-800">{error}</p>;
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{messages.matches.title}</h1>
      {items.length === 0 ? (
        <p className="mt-4 text-slate-600">{messages.matches.empty}</p>
      ) : (
        <ul className="mt-6 space-y-3">
          {items.map((match) => {
            const demand = match.demand_request;
            return (
              <li key={match.id}>
                <Link
                  href={`/app/matches/${match.id}`}
                  className="block rounded-2xl border border-slate-200 bg-white p-4"
                >
                  <p className="text-base font-semibold">
                    {routeLabel(
                      locations,
                      demand.origin_country,
                      demand.origin_city,
                      demand.destination_country,
                      demand.destination_city,
                      locale,
                    )}
                  </p>
                  <p className="mt-2 text-sm text-slate-600">
                    {messages.matches.travel}:{" "}
                    {formatDateRange(
                      match.supply_request.date_from,
                      match.supply_request.date_to,
                      locale,
                    )}
                  </p>
                  <p className="text-sm text-slate-600">
                    {messages.matches.demand}: {formatKg(demand.weight_kg)} {messages.common.kg}
                  </p>
                  <p className="text-sm text-slate-600">
                    {messages.matches.supply}: {formatKg(match.supply_request.capacity_kg)}{" "}
                    {messages.common.kg}
                  </p>
                  <p className="mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                    {messages.status[match.status]}
                  </p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
