"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { RequestForm } from "@/components/RequestForm";
import { dangerButtonClass, primaryButtonClass, secondaryButtonClass } from "@/components/ui";
import { ApiError, cancelRequest, closeRequest, fetchRequest } from "@/lib/api";
import { categoryLabel, routeLabel } from "@/lib/catalog";
import { formatDateRange, formatKg } from "@/lib/format";
import { interpolate, useI18n } from "@/lib/i18n";
import type { ItemRequest } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";

export default function RequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { locale, messages } = useI18n();
  const { categories, locations, loading: catalogLoading } = useCatalog();
  const [item, setItem] = useState<ItemRequest | null>(null);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchRequest(Number(id))
      .then((payload) => {
        if (!cancelled) {
          setItem(payload);
        }
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof ApiError && cause.status === 404 ? messages.requests.notFound : messages.common.error);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, messages.common.error, messages.requests.notFound]);

  if (error) {
    return <p className="text-sm text-amber-800">{error}</p>;
  }
  if (!item || catalogLoading) {
    return <p className="text-sm text-slate-500">{messages.common.loading}</p>;
  }

  if (editing && item.status === "ACTIVE") {
    return <RequestForm type={item.type} existing={item} />;
  }

  const requestId = item.id;

  async function onCancel() {
    if (!window.confirm(messages.requests.cancelConfirm)) {
      return;
    }
    setBusy(true);
    try {
      const updated = await cancelRequest(requestId);
      setItem(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : messages.common.error);
    } finally {
      setBusy(false);
    }
  }

  async function onClose(packageSent: boolean) {
    setBusy(true);
    try {
      const updated = await closeRequest(requestId, packageSent);
      setItem(updated);
      setClosing(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : messages.common.error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="text-sm text-slate-500">
        {item.type === "DEMAND" ? messages.requests.demand : messages.requests.supply}
      </p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">
        {routeLabel(
          locations,
          item.origin_country,
          item.origin_city,
          item.destination_country,
          item.destination_city,
          locale,
        )}
      </h1>
      <dl className="mt-6 space-y-3 text-sm">
        <Row
          label={item.type === "SUPPLY" ? messages.form.travelDates : messages.form.dates}
          value={formatDateRange(item.date_from, item.date_to, locale)}
        />
        <Row
          label={item.type === "DEMAND" ? messages.form.weight : messages.form.capacity}
          value={`${formatKg(item.type === "DEMAND" ? item.weight_kg : item.capacity_kg)} ${messages.common.kg}`}
        />
        {item.item_category_codes.length > 0 && (
          <Row
            label={item.type === "SUPPLY" ? messages.form.canCarry : messages.form.category}
            value={item.item_category_codes
              .map((code) => categoryLabel(categories, code, locale))
              .join(", ")}
          />
        )}
        {item.excluded_category_codes.length > 0 && (
          <Row
            label={messages.form.willNotCarry}
            value={item.excluded_category_codes
              .map((code) => categoryLabel(categories, code, locale))
              .join(", ")}
          />
        )}
        {item.description ? <Row label={messages.form.description} value={item.description} /> : null}
        <Row
          label={messages.home.matches}
          value={interpolate(messages.requests.matches, { count: item.match_count })}
        />
        <Row label={messages.common.status} value={messages.status[item.status]} />
      </dl>
      <div className="mt-8 flex flex-col gap-3">
        {item.match_count > 0 && (
          <button className={secondaryButtonClass} type="button" onClick={() => router.push("/app/matches")}>
            {messages.home.matches}
          </button>
        )}
        {item.status === "ACTIVE" && (
          <>
            {!closing && (
              <button className={secondaryButtonClass} type="button" onClick={() => setEditing(true)}>
                {messages.common.edit}
              </button>
            )}
            {item.type === "SUPPLY" ? (
              closing ? (
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <p className="font-medium text-slate-900">{messages.requests.closeTitle}</p>
                  <p className="mt-2 text-sm text-slate-600">{messages.requests.closeHint}</p>
                  <p className="mt-4 font-medium text-slate-900">{messages.requests.closeQuestion}</p>
                  <div className="mt-4 flex flex-col gap-3">
                    <button
                      className={primaryButtonClass}
                      disabled={busy}
                      type="button"
                      onClick={() => void onClose(true)}
                    >
                      {messages.requests.closeSent}
                    </button>
                    <button
                      className={dangerButtonClass}
                      disabled={busy}
                      type="button"
                      onClick={() => void onClose(false)}
                    >
                      {messages.requests.closeWithout}
                    </button>
                    <button className={secondaryButtonClass} type="button" onClick={() => setClosing(false)}>
                      {messages.requests.closeBack}
                    </button>
                  </div>
                </div>
              ) : (
                <button className={dangerButtonClass} disabled={busy} type="button" onClick={() => setClosing(true)}>
                  {messages.requests.closeAction}
                </button>
              )
            ) : (
              <button className={dangerButtonClass} disabled={busy} type="button" onClick={() => void onCancel()}>
                {messages.requests.cancelAction}
              </button>
            )}
          </>
        )}
      </div>
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
