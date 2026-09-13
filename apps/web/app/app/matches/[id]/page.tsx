"use client";

import { use, useEffect, useState } from "react";

import { TguiIcon } from "@/components/TguiIcon";
import { primaryButtonClass } from "@/components/ui";
import { ApiError, fetchMatch } from "@/lib/api";
import { routeLabel } from "@/lib/catalog";
import { formatDateRange, formatKg } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Match } from "@/lib/types";
import { openTelegramDm } from "@/lib/telegram";
import { useCatalog } from "@/lib/useCatalog";

export default function MatchDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { locale, messages } = useI18n();
  const { locations, loading: catalogLoading } = useCatalog();
  const [match, setMatch] = useState<Match | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchMatch(Number(id))
      .then((payload) => {
        if (!cancelled) {
          setMatch(payload);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(
            cause instanceof ApiError && cause.status === 404
              ? messages.matches.notFound
              : messages.common.error,
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, messages.common.error, messages.matches.notFound]);

  if (error) {
    return <p className="text-sm text-amber-800">{error}</p>;
  }
  if (!match || catalogLoading) {
    return <p className="text-sm text-slate-500">{messages.common.loading}</p>;
  }

  const demand = match.demand_request;
  const connected = match.status === "ACCEPTED" || match.status === "PENDING_APPROVAL";
  const finished = match.status === "COMPLETED";
  const showContact = Boolean((connected || finished) && match.counterpart);

  return (
    <div>
      <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-tight">
        {connected ? (
          <>
            <TguiIcon name="select" size={20} />
            {messages.matches.connected}
          </>
        ) : (
          <>
            <TguiIcon name="heart" size={28} />
            {messages.appName}
          </>
        )}
      </h1>
      <p className="mt-3 text-lg font-medium">
        {routeLabel(
          locations,
          demand.origin_country,
          demand.origin_city,
          demand.destination_country,
          demand.destination_city,
          locale,
        )}
      </p>
      <dl className="mt-6 space-y-3 text-sm">
        <Row
          label={messages.matches.travel}
          value={formatDateRange(match.supply_request.date_from, match.supply_request.date_to, locale)}
        />
        <Row
          label={messages.matches.demand}
          value={`${formatKg(demand.weight_kg)} ${messages.common.kg}`}
        />
        <Row
          label={messages.matches.supply}
          value={`${formatKg(match.supply_request.capacity_kg)} ${messages.common.kg}`}
        />
      </dl>
      <p className="mt-4 text-sm text-slate-600">
        {match.my_role === "demand" ? messages.matches.yourRoleDemand : messages.matches.yourRoleSupply}
      </p>

      {showContact && match.counterpart ? (
        <div className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
          <p className="font-medium text-emerald-900">{messages.matches.connectedBody}</p>
          <p className="mt-2 text-slate-800">
            {match.counterpart.telegram_username
              ? `@${match.counterpart.telegram_username}`
              : match.counterpart.first_name}
          </p>
          {match.counterpart.telegram_username ? (
            <a
              className={`${primaryButtonClass} mt-4`}
              href={match.counterpart.telegram_url}
              onClick={(event) => {
                event.preventDefault();
                openTelegramDm(match.counterpart.telegram_url, match.counterpart.draft || "");
              }}
            >
              {finished ? messages.matches.appreciateOnTelegram : messages.matches.messageOnTelegram}
            </a>
          ) : (
            <p className="mt-3 text-sm text-slate-600">{messages.matches.noUsername}</p>
          )}
          {match.counterpart.draft ? (
            <textarea
              className="mt-4 w-full rounded-xl border border-emerald-200 bg-white p-3 text-sm text-slate-800"
              readOnly
              rows={10}
              value={match.counterpart.draft}
            />
          ) : null}
        </div>
      ) : null}

      {error && <p className="mt-4 text-sm text-amber-800">{error}</p>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-end font-medium text-slate-900">{value}</dd>
    </div>
  );
}
