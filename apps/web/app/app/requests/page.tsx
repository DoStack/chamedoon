"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchRequests } from "@/lib/api";
import { routeLabel } from "@/lib/catalog";
import { formatDateRange, formatKg } from "@/lib/format";
import { interpolate, useI18n } from "@/lib/i18n";
import type { ItemRequest } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";

export default function RequestsPage() {
  const { locale, messages } = useI18n();
  const { locations, loading: catalogLoading } = useCatalog();
  const [items, setItems] = useState<ItemRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRequests()
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
      <h1 className="text-2xl font-semibold tracking-tight">{messages.requests.title}</h1>
      {items.length === 0 ? (
        <p className="mt-4 text-slate-600">{messages.requests.empty}</p>
      ) : (
        <ul className="mt-6 space-y-3">
          {items.map((item) => (
            <li key={item.id}>
              <Link
                href={`/app/requests/${item.id}`}
                className="block rounded-2xl border border-slate-200 bg-white p-4"
              >
                <p className="text-base font-medium">
                  {routeLabel(
                    locations,
                    item.origin_country,
                    item.origin_city,
                    item.destination_country,
                    item.destination_city,
                    locale,
                  )}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {item.type === "DEMAND" ? messages.requests.demand : messages.requests.supply}
                  {" · "}
                  {formatKg(item.type === "DEMAND" ? item.weight_kg : item.capacity_kg)}{" "}
                  {messages.common.kg}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {formatDateRange(item.date_from, item.date_to, locale)}
                </p>
                <p className="mt-2 text-sm text-slate-700">
                  {interpolate(messages.requests.matches, { count: item.match_count })}
                </p>
                <p className="mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                  {messages.status[item.status]}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
