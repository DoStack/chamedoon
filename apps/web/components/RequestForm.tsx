"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { primaryButtonClass, secondaryButtonClass, inputClass } from "@/components/ui";
import { ApiError, createRequest, updateRequest } from "@/lib/api";
import { cityLabel, localizedName } from "@/lib/catalog";
import { addDaysIso, formatDateRange, formatKg, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { ItemRequest, RequestType } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";

type FormState = {
  origin_country: string;
  origin_city: string;
  destination_country: string;
  destination_city: string;
  date_from: string;
  date_to: string;
  weight_kg: string;
  capacity_kg: string;
  item_category_codes: string[];
  excluded_category_codes: string[];
  excluded_other_text: string;
  description: string;
};

function emptyForm(type: RequestType, existing?: ItemRequest): FormState {
  if (existing) {
    return {
      origin_country: existing.origin_country,
      origin_city: existing.origin_city,
      destination_country: existing.destination_country,
      destination_city: existing.destination_city,
      date_from: existing.date_from,
      date_to: existing.date_to,
      weight_kg: existing.weight_kg ?? "",
      capacity_kg: existing.capacity_kg ?? "",
      item_category_codes: existing.item_category_codes,
      excluded_category_codes: existing.excluded_category_codes,
      excluded_other_text: existing.excluded_other_text ?? "",
      description: existing.description ?? "",
    };
  }
  const from = todayIso();
  return {
    origin_country: "",
    origin_city: "",
    destination_country: "",
    destination_city: "",
    date_from: from,
    date_to: type === "SUPPLY" ? from : addDaysIso(from, 14),
    weight_kg: "",
    capacity_kg: "",
    item_category_codes: [],
    excluded_category_codes: [],
    excluded_other_text: "",
    description: "",
  };
}

export function RequestForm({
  type,
  existing,
}: {
  type: RequestType;
  existing?: ItemRequest;
}) {
  const router = useRouter();
  const { locale, messages } = useI18n();
  const { categories, locations, loading, error: catalogError } = useCatalog();
  const [form, setForm] = useState<FormState>(() => emptyForm(type, existing));
  const [step, setStep] = useState<"edit" | "review">("edit");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const originCities = useMemo(
    () => locations?.countries.find((country) => country.code === form.origin_country)?.cities ?? [],
    [locations, form.origin_country],
  );
  const destinationCities = useMemo(
    () =>
      locations?.countries.find((country) => country.code === form.destination_country)?.cities ?? [],
    [locations, form.destination_country],
  );

  const title = type === "DEMAND" ? messages.demand.title : messages.supply.title;

  function categoryLabelSafe(code: string) {
    const category = categories.find((item) => item.code === code);
    return category ? localizedName(category, locale) : code;
  }

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function toggleCode(field: "item_category_codes" | "excluded_category_codes", code: string) {
    setForm((current) => {
      const selected = current[field].includes(code)
        ? current[field].filter((item) => item !== code)
        : [...current[field], code];
      return { ...current, [field]: selected };
    });
  }

  function validate(): string | null {
    if (!form.origin_country || !form.origin_city) {
      return messages.common.required;
    }
    if (!form.destination_country || !form.destination_city) {
      return messages.common.required;
    }
    if (
      form.origin_country === form.destination_country &&
      form.origin_city === form.destination_city
    ) {
      return messages.form.sameCity;
    }
    if (!form.date_from || !form.date_to) {
      return messages.common.required;
    }
    if (form.date_from > form.date_to) {
      return messages.form.dateOrder;
    }
    if (type === "DEMAND") {
      if (!form.weight_kg) {
        return messages.common.required;
      }
      if (form.item_category_codes.length === 0) {
        return messages.form.needCategory;
      }
    } else if (!form.capacity_kg) {
      return messages.common.required;
    }
    return null;
  }

  function goReview() {
    const problem = validate();
    setFieldError(problem);
    if (!problem) {
      setStep("review");
    }
  }

  async function submit() {
    setBusy(true);
    setSubmitError(null);
    const payload: Record<string, unknown> = {
      type,
      origin_country: form.origin_country,
      origin_city: form.origin_city,
      destination_country: form.destination_country,
      destination_city: form.destination_city,
      date_from: form.date_from,
      date_to: form.date_to,
      description: form.description,
      item_category_codes: form.item_category_codes,
    };
    if (type === "DEMAND") {
      payload.weight_kg = form.weight_kg;
    } else {
      payload.capacity_kg = form.capacity_kg;
      payload.excluded_category_codes = form.excluded_category_codes;
      payload.excluded_other_text = form.excluded_other_text;
    }
    try {
      const saved = existing
        ? await updateRequest(existing.id, payload)
        : await createRequest(payload);
      router.push(`/app/requests/${saved.id}`);
    } catch (cause) {
      setSubmitError(cause instanceof ApiError ? cause.message : messages.common.error);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">{messages.common.loading}</p>;
  }
  if (catalogError || !locations) {
    return <p className="text-sm text-amber-800">{catalogError || messages.common.error}</p>;
  }

  if (step === "review") {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{messages.form.review}</h1>
        <dl className="mt-6 space-y-3 text-sm">
          <Row label={messages.form.origin}>
            {cityLabel(locations, form.origin_country, form.origin_city, locale)}
          </Row>
          <Row label={messages.form.destination}>
            {cityLabel(locations, form.destination_country, form.destination_city, locale)}
          </Row>
          <Row label={type === "SUPPLY" ? messages.form.travelDates : messages.form.dates}>
            {formatDateRange(form.date_from, form.date_to, locale)}
          </Row>
          {type === "DEMAND" ? (
            <Row label={messages.form.weight}>
              {formatKg(form.weight_kg)} {messages.common.kg}
            </Row>
          ) : (
            <Row label={messages.form.capacity}>
              {formatKg(form.capacity_kg)} {messages.common.kg}
            </Row>
          )}
          {form.item_category_codes.length > 0 && (
            <Row label={type === "SUPPLY" ? messages.form.canCarry : messages.form.category}>
              {form.item_category_codes
                .map((code) => categoryLabelSafe(code))
                .join(", ")}
            </Row>
          )}
          {type === "SUPPLY" && form.excluded_category_codes.length > 0 && (
            <Row label={messages.form.willNotCarry}>
              {form.excluded_category_codes
                .map((code) => categoryLabelSafe(code))
                .join(", ")}
            </Row>
          )}
          {type === "SUPPLY" && form.excluded_other_text && (
            <Row label={messages.form.excludedOther}>{form.excluded_other_text}</Row>
          )}
          {form.description && <Row label={messages.form.description}>{form.description}</Row>}
        </dl>
        {submitError && <p className="mt-4 text-sm text-amber-800">{submitError}</p>}
        <div className="mt-8 flex flex-col gap-3">
          <button className={primaryButtonClass} disabled={busy} type="button" onClick={() => void submit()}>
            {existing
              ? messages.common.save
              : type === "DEMAND"
                ? messages.demand.submit
                : messages.supply.submit}
          </button>
          <button className={secondaryButtonClass} type="button" onClick={() => setStep("edit")}>
            {messages.form.editDetails}
          </button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="space-y-5"
      onSubmit={(event) => {
        event.preventDefault();
        goReview();
      }}
    >
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-slate-900">{messages.form.origin}</legend>
        <select
          className={inputClass}
          value={form.origin_country}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              origin_country: event.target.value,
              origin_city: "",
            }))
          }
          required
        >
          <option value="">{messages.form.selectCountry}</option>
          {locations.countries.map((country) => (
            <option key={country.code} value={country.code}>
              {localizedName(country, locale)}
            </option>
          ))}
        </select>
        <select
          className={inputClass}
          value={form.origin_city}
          onChange={(event) => update("origin_city", event.target.value)}
          required
          disabled={!form.origin_country}
        >
          <option value="">{messages.form.selectCity}</option>
          {originCities.map((city) => (
            <option key={city.slug} value={city.slug}>
              {localizedName(city, locale)}
            </option>
          ))}
        </select>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-slate-900">{messages.form.destination}</legend>
        <select
          className={inputClass}
          value={form.destination_country}
          onChange={(event) =>
            setForm((current) => ({
              ...current,
              destination_country: event.target.value,
              destination_city: "",
            }))
          }
          required
        >
          <option value="">{messages.form.selectCountry}</option>
          {locations.countries.map((country) => (
            <option key={country.code} value={country.code}>
              {localizedName(country, locale)}
            </option>
          ))}
        </select>
        <select
          className={inputClass}
          value={form.destination_city}
          onChange={(event) => update("destination_city", event.target.value)}
          required
          disabled={!form.destination_country}
        >
          <option value="">{messages.form.selectCity}</option>
          {destinationCities.map((city) => (
            <option key={city.slug} value={city.slug}>
              {localizedName(city, locale)}
            </option>
          ))}
        </select>
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-slate-900">
          {type === "SUPPLY" ? messages.form.travelDates : messages.form.dates}
        </legend>
        <label className="block text-sm text-slate-600">
          {messages.form.dateFrom}
          <input
            className={`${inputClass} mt-1`}
            type="date"
            value={form.date_from}
            onChange={(event) => update("date_from", event.target.value)}
            required
          />
        </label>
        <label className="block text-sm text-slate-600">
          {messages.form.dateTo}
          <input
            className={`${inputClass} mt-1`}
            type="date"
            value={form.date_to}
            onChange={(event) => update("date_to", event.target.value)}
            required
          />
        </label>
      </fieldset>

      {type === "DEMAND" ? (
        <label className="block">
          <span className="mb-1 block text-sm font-medium">{messages.form.weight}</span>
          <input
            className={inputClass}
            type="number"
            min="0.01"
            max="50"
            step="0.01"
            inputMode="decimal"
            value={form.weight_kg}
            onChange={(event) => update("weight_kg", event.target.value)}
            required
          />
        </label>
      ) : (
        <label className="block">
          <span className="mb-1 block text-sm font-medium">{messages.form.capacity}</span>
          <input
            className={inputClass}
            type="number"
            min="0.01"
            max="50"
            step="0.01"
            inputMode="decimal"
            value={form.capacity_kg}
            onChange={(event) => update("capacity_kg", event.target.value)}
            required
          />
        </label>
      )}

      <fieldset>
        <legend className="mb-2 text-sm font-medium">
          {type === "SUPPLY" ? messages.form.canCarry : messages.form.category}
          {type === "SUPPLY" ? ` (${messages.form.optional})` : ""}
        </legend>
        <div className="flex flex-col gap-2">
          {categories.map((category) => (
            <label key={category.code} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.item_category_codes.includes(category.code)}
                onChange={() => toggleCode("item_category_codes", category.code)}
              />
              {localizedName(category, locale)}
            </label>
          ))}
        </div>
      </fieldset>

      {type === "SUPPLY" && (
        <>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">
              {messages.form.willNotCarry} ({messages.form.optional})
            </legend>
            <div className="flex flex-col gap-2">
              {categories.map((category) => (
                <label key={category.code} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.excluded_category_codes.includes(category.code)}
                    onChange={() => toggleCode("excluded_category_codes", category.code)}
                  />
                  {localizedName(category, locale)}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="mb-1 block text-sm font-medium">
              {messages.form.excludedOther} ({messages.form.optional})
            </span>
            <input
              className={inputClass}
              value={form.excluded_other_text}
              onChange={(event) => update("excluded_other_text", event.target.value)}
            />
          </label>
        </>
      )}

      <label className="block">
        <span className="mb-1 block text-sm font-medium">
          {messages.form.description} ({messages.form.optional})
        </span>
        <textarea
          className="min-h-24 w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-base outline-none focus:border-slate-900"
          value={form.description}
          onChange={(event) => update("description", event.target.value)}
        />
      </label>

      {fieldError && <p className="text-sm text-amber-800">{fieldError}</p>}
      <button className={primaryButtonClass} type="submit">
        {messages.common.continue}
      </button>
    </form>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-end font-medium text-slate-900">{children}</dd>
    </div>
  );
}
