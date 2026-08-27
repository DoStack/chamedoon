"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { TguiIcon } from "@/components/TguiIcon";
import { inputClass, primaryButtonClass, secondaryButtonClass } from "@/components/ui";
import { ApiError, connectExplore, fetchExplore, fetchRequests, formatApiError, type ExploreFilters } from "@/lib/api";
import { categoryLabel, localizedName, routeLabel } from "@/lib/catalog";
import { formatDateRange, formatKg } from "@/lib/format";
import { interpolate, useI18n } from "@/lib/i18n";
import type { ItemRequest, OpenRequest, RequestType } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";

const EMPTY_FILTERS: ExploreFilters = {
  type: "",
  origin_country: "",
  origin_city: "",
  destination_country: "",
  destination_city: "",
  date_from: "",
  date_to: "",
  categories: [],
};

export default function ExplorePage() {
  const router = useRouter();
  const { locale, messages } = useI18n();
  const { categories, locations, loading: catalogLoading } = useCatalog();
  const [filters, setFilters] = useState<ExploreFilters>(EMPTY_FILTERS);
  const [draft, setDraft] = useState<ExploreFilters>(EMPTY_FILTERS);
  const [filterOpen, setFilterOpen] = useState(false);
  const [items, setItems] = useState<OpenRequest[] | null>(null);
  const [mine, setMine] = useState<ItemRequest[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pickingId, setPickingId] = useState<number | null>(null);
  const [chosenId, setChosenId] = useState<number | "">("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [cardError, setCardError] = useState<{ id: number; message: string } | null>(null);

  const originCities = useMemo(
    () => locations?.countries.find((country) => country.code === (filterOpen ? draft.origin_country : filters.origin_country))?.cities ?? [],
    [locations, filterOpen, draft.origin_country, filters.origin_country],
  );
  const destinationCities = useMemo(
    () =>
      locations?.countries.find((country) => country.code === (filterOpen ? draft.destination_country : filters.destination_country))?.cities ?? [],
    [locations, filterOpen, draft.destination_country, filters.destination_country],
  );

  useEffect(() => {
    let cancelled = false;
    fetchRequests()
      .then((payload) => {
        if (!cancelled) {
          setMine(payload.filter((item) => item.status === "ACTIVE"));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMine([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    fetchExplore(filters)
      .then((payload) => {
        if (!cancelled) {
          setItems(payload);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(messages.common.error);
          setItems([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [filters, messages.common.error]);

  function oppositeOf(type: RequestType): RequestType {
    return type === "DEMAND" ? "SUPPLY" : "DEMAND";
  }

  function oppositeMine(type: RequestType): ItemRequest[] {
    return mine.filter((item) => item.type === oppositeOf(type));
  }

  async function propose(item: OpenRequest, myRequestId?: number) {
    setBusyId(item.id);
    setCardError(null);
    try {
      const match = await connectExplore(item.id, myRequestId);
      router.push(`/app/matches/${match.id}`);
    } catch (cause) {
      const message =
        cause instanceof ApiError ? formatApiError(cause.body, messages.common.error) : messages.common.error;
      setCardError({ id: item.id, message });
    } finally {
      setBusyId(null);
    }
  }

  function onPropose(item: OpenRequest) {
    const candidates = oppositeMine(item.type);
    if (candidates.length === 0) {
      setCardError({
        id: item.id,
        message: item.type === "SUPPLY" ? messages.explore.needDemand : messages.explore.needSupply,
      });
      setPickingId(null);
      return;
    }
    if (candidates.length === 1) {
      void propose(item, candidates[0].id);
      return;
    }
    setPickingId(item.id);
    setChosenId("");
    setCardError(null);
  }

  function updateFilter<K extends keyof ExploreFilters>(key: K, value: ExploreFilters[K]) {
    setFilters((current) => {
      const next = { ...current, [key]: value };
      if (key === "origin_country") {
        next.origin_city = "";
      }
      if (key === "destination_country") {
        next.destination_city = "";
      }
      return next;
    });
  }

  function updateDraft<K extends keyof ExploreFilters>(key: K, value: ExploreFilters[K]) {
    setDraft((current) => {
      const next = { ...current, [key]: value };
      if (key === "origin_country") {
        next.origin_city = "";
      }
      if (key === "destination_country") {
        next.destination_city = "";
      }
      return next;
    });
  }

  function toggleDraftCategory(code: string) {
    setDraft((current) => {
      const selected = new Set(current.categories || []);
      if (selected.has(code)) {
        selected.delete(code);
      } else {
        selected.add(code);
      }
      return { ...current, categories: [...selected] };
    });
  }

  const extraFilterCount =
    Number(Boolean(filters.origin_country || filters.origin_city)) +
    Number(Boolean(filters.destination_country || filters.destination_city)) +
    Number(Boolean(filters.date_from || filters.date_to)) +
    (filters.categories?.length || 0);

  if (catalogLoading) {
    return <p className="text-sm text-slate-500">{messages.common.loading}</p>;
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{messages.explore.title}</h1>
      <p className="mt-3 text-sm leading-6 text-slate-600">{messages.explore.hint}</p>

      <fieldset className="mt-6 space-y-3">
        <legend className="text-sm font-medium text-slate-700">{messages.explore.type}</legend>
        <div className="grid grid-cols-3 gap-2">
          {(
            [
              ["", messages.explore.all],
              ["DEMAND", messages.requests.demand],
              ["SUPPLY", messages.requests.supply],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value || "all"}
              type="button"
              className={`h-10 rounded-xl border text-sm font-medium ${
                (filters.type || "") === value
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-900"
              }`}
              onClick={() => updateFilter("type", value)}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>

      <button
        className={`${secondaryButtonClass} mt-4`}
        type="button"
        onClick={() => {
          setDraft(filters);
          setFilterOpen(true);
        }}
      >
        {messages.explore.filters}
        {extraFilterCount ? ` (${extraFilterCount})` : ""}
      </button>

      {filterOpen ? (
        <div className="fixed inset-0 z-40 flex items-end bg-black/40 p-0 sm:items-center sm:p-4">
          <button
            className="absolute inset-0"
            type="button"
            aria-label={messages.explore.clearFilters}
            onClick={() => setFilterOpen(false)}
          />
          <div className="relative z-10 max-h-[88vh] w-full overflow-auto rounded-t-2xl bg-white p-5 sm:rounded-2xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold">{messages.explore.filters}</h2>
              <button className="text-sm text-slate-500" type="button" onClick={() => setFilterOpen(false)}>
                ✕
              </button>
            </div>
            <div className="space-y-3">
              <label className="block">
                <span className="mb-1 block text-sm text-slate-600">{messages.form.origin}</span>
                <div className="grid grid-cols-2 gap-2">
                  <select
                    className={inputClass}
                    value={draft.origin_country || ""}
                    onChange={(event) => updateDraft("origin_country", event.target.value)}
                  >
                    <option value="">{messages.explore.anyCountry}</option>
                    {(locations?.countries ?? []).map((country) => (
                      <option key={country.code} value={country.code}>
                        {localizedName(country, locale)}
                      </option>
                    ))}
                  </select>
                  <select
                    className={inputClass}
                    value={draft.origin_city || ""}
                    disabled={!draft.origin_country}
                    onChange={(event) => updateDraft("origin_city", event.target.value)}
                  >
                    <option value="">{messages.explore.anyCity}</option>
                    {originCities.map((city) => (
                      <option key={city.slug} value={city.slug}>
                        {localizedName(city, locale)}
                      </option>
                    ))}
                  </select>
                </div>
              </label>
              <label className="block">
                <span className="mb-1 block text-sm text-slate-600">{messages.form.destination}</span>
                <div className="grid grid-cols-2 gap-2">
                  <select
                    className={inputClass}
                    value={draft.destination_country || ""}
                    onChange={(event) => updateDraft("destination_country", event.target.value)}
                  >
                    <option value="">{messages.explore.anyCountry}</option>
                    {(locations?.countries ?? []).map((country) => (
                      <option key={country.code} value={country.code}>
                        {localizedName(country, locale)}
                      </option>
                    ))}
                  </select>
                  <select
                    className={inputClass}
                    value={draft.destination_city || ""}
                    disabled={!draft.destination_country}
                    onChange={(event) => updateDraft("destination_city", event.target.value)}
                  >
                    <option value="">{messages.explore.anyCity}</option>
                    {destinationCities.map((city) => (
                      <option key={city.slug} value={city.slug}>
                        {localizedName(city, locale)}
                      </option>
                    ))}
                  </select>
                </div>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-1 block text-sm text-slate-600">{messages.form.dateFrom}</span>
                  <input
                    className={inputClass}
                    type="date"
                    value={draft.date_from || ""}
                    onChange={(event) => updateDraft("date_from", event.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-sm text-slate-600">{messages.form.dateTo}</span>
                  <input
                    className={inputClass}
                    type="date"
                    value={draft.date_to || ""}
                    onChange={(event) => updateDraft("date_to", event.target.value)}
                  />
                </label>
              </div>
              <div>
                <p className="mb-2 text-sm font-medium text-slate-700">{messages.form.category}</p>
                <div className="flex flex-wrap gap-2">
                  {categories.map((category) => {
                    const on = (draft.categories || []).includes(category.code);
                    return (
                      <button
                        key={category.code}
                        type="button"
                        className={`rounded-lg px-3 py-2 text-sm ${
                          on ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-800"
                        }`}
                        onClick={() => toggleDraftCategory(category.code)}
                      >
                        {localizedName(category, locale)}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-2">
                <button
                  className={secondaryButtonClass}
                  type="button"
                  onClick={() => {
                    const cleared = { ...EMPTY_FILTERS, type: filters.type };
                    setDraft(cleared);
                    setFilters(cleared);
                    setFilterOpen(false);
                  }}
                >
                  {messages.explore.clearFilters}
                </button>
                <button
                  className={primaryButtonClass}
                  type="button"
                  onClick={() => {
                    setFilters(draft);
                    setFilterOpen(false);
                  }}
                >
                  {messages.explore.applyFilters}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {error ? <p className="mt-4 text-sm text-amber-800">{error}</p> : null}
      {!items ? (
        <p className="mt-6 text-sm text-slate-500">{messages.common.loading}</p>
      ) : items.length === 0 ? (
        <p className="mt-6 text-slate-600">{messages.explore.empty}</p>
      ) : (
        <ul className="mt-6 space-y-3">
          {items.map((item) => {
            const candidates = oppositeMine(item.type);
            return (
              <li key={item.id} className="rounded-2xl border border-slate-200 bg-white p-4">
                <p className="flex items-center gap-2 text-base font-medium">
                  <TguiIcon name={item.type === "DEMAND" ? "archive" : "devices"} size={28} />
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
                  {formatKg(item.type === "DEMAND" ? item.weight_kg : item.capacity_kg)} {messages.common.kg}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {formatDateRange(item.date_from, item.date_to, locale)}
                </p>
                <p className="mt-1 text-sm text-slate-600">
                  {item.item_category_codes
                    .map((code) => categoryLabel(categories, code, locale))
                    .join(", ")}
                </p>
                {item.excluded_category_codes.length > 0 ? (
                  <p className="mt-1 text-sm text-slate-500">
                    {messages.explore.willNotCarry}:{" "}
                    {item.excluded_category_codes
                      .map((code) => categoryLabel(categories, code, locale))
                      .join(", ")}
                  </p>
                ) : null}
                {item.description ? (
                  <p className="mt-2 text-sm text-slate-700">{item.description}</p>
                ) : null}
                <p className="mt-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                  {interpolate(messages.explore.owner, { name: item.owner_first_name })}
                </p>

                {pickingId === item.id ? (
                  <div className="mt-4 space-y-3">
                    <label className="block">
                      <span className="mb-1 block text-sm text-slate-600">{messages.explore.chooseYours}</span>
                      <select
                        className={inputClass}
                        value={chosenId}
                        onChange={(event) => setChosenId(event.target.value ? Number(event.target.value) : "")}
                      >
                        <option value="">{messages.explore.selectYours}</option>
                        {candidates.map((candidate) => (
                          <option key={candidate.id} value={candidate.id}>
                            {routeLabel(
                              locations,
                              candidate.origin_country,
                              candidate.origin_city,
                              candidate.destination_country,
                              candidate.destination_city,
                              locale,
                            )}{" "}
                            · {formatKg(candidate.type === "DEMAND" ? candidate.weight_kg : candidate.capacity_kg)}{" "}
                            {messages.common.kg}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      className={primaryButtonClass}
                      type="button"
                      disabled={busyId === item.id || chosenId === ""}
                      onClick={() => {
                        if (typeof chosenId === "number") {
                          void propose(item, chosenId);
                        }
                      }}
                    >
                      {messages.explore.confirm}
                    </button>
                  </div>
                ) : (
                  <button
                    className={`${primaryButtonClass} mt-4`}
                    type="button"
                    disabled={busyId === item.id}
                    onClick={() => onPropose(item)}
                  >
                    {messages.explore.propose}
                  </button>
                )}

                {cardError?.id === item.id ? (
                  <div className="mt-3 space-y-2">
                    <p className="text-sm text-amber-800">{cardError.message}</p>
                    {candidates.length === 0 ? (
                      <Link
                        className={secondaryButtonClass}
                        href={item.type === "SUPPLY" ? "/app/demand/new" : "/app/supply/new"}
                      >
                        {item.type === "SUPPLY" ? messages.home.send : messages.home.carry}
                      </Link>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
