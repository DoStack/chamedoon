"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";

import { primaryButtonClass, secondaryButtonClass, inputClass } from "@/components/ui";
import { ApiError, createRequest, updateRequest } from "@/lib/api";
import { cityLabel, localizedName } from "@/lib/catalog";
import { addDaysIso, formatDateRange, formatKg, todayIso } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { ItemRequest, RequestType } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";
import { formatSuggestedKg, suggestedKg } from "@/lib/weights";

type FormState = {
  origin_country: string;
  origin_city: string;
  destination_country: string;
  destination_city: string;
  date_from: string;
  date_to: string;
  desired_date: string;
  flight_date: string;
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
      desired_date: existing.desired_date ?? existing.date_from,
      flight_date: existing.flight_date ?? existing.date_from,
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
    date_to: addDaysIso(from, 14),
    desired_date: from,
    flight_date: from,
    weight_kg: "",
    capacity_kg: "",
    item_category_codes: [],
    excluded_category_codes: [],
    excluded_other_text: "",
    description: "",
  };
}

const OTHER_CITY = "__other__";

function CityField({
  cities,
  value,
  onChange,
  disabled,
  locale,
  selectLabel,
  otherLabel,
  placeholder,
}: {
  cities: { slug: string; name_en: string; name_fa: string }[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  locale: "en" | "fa";
  selectLabel: string;
  otherLabel: string;
  placeholder: string;
}) {
  const known = cities.some((city) => city.slug === value);
  const [otherOpen, setOtherOpen] = useState(Boolean(value) && !known);
  const showOther = otherOpen || (Boolean(value) && !known);
  return (
    <>
      <select
        className={inputClass}
        value={showOther ? OTHER_CITY : value}
        disabled={disabled}
        required
        onChange={(event) => {
          const next = event.target.value;
          if (next === OTHER_CITY) {
            setOtherOpen(true);
            if (known) onChange("");
            return;
          }
          setOtherOpen(false);
          onChange(next);
        }}
      >
        <option value="">{selectLabel}</option>
        {cities.map((city) => (
          <option key={city.slug} value={city.slug}>
            {localizedName(city, locale)}
          </option>
        ))}
        <option value={OTHER_CITY}>{otherLabel}</option>
      </select>
      {showOther ? (
        <input
          className={inputClass}
          value={known ? "" : value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          required
        />
      ) : null}
    </>
  );
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
  const kgTouched = useRef(
    Boolean(
      existing &&
        ((type === "DEMAND" && existing.weight_kg) || (type === "SUPPLY" && existing.capacity_kg)),
    ),
  );
  const suggestedAmount = useMemo(
    () => suggestedKg(form.item_category_codes),
    [form.item_category_codes],
  );
  const suggestedText = formatSuggestedKg(suggestedAmount);
  const kgHint = suggestedText
    ? messages.form.kgFromCategories.replace("{kg}", suggestedText)
    : messages.form.kgPickCategories;

  useEffect(() => {
    if (kgTouched.current || !suggestedText) return;
    setForm((current) => {
      if (type === "DEMAND") {
        if (current.weight_kg === suggestedText) return current;
        return { ...current, weight_kg: suggestedText };
      }
      if (current.capacity_kg === suggestedText) return current;
      return { ...current, capacity_kg: suggestedText };
    });
  }, [suggestedText, type]);

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
      const other = field === "item_category_codes" ? "excluded_category_codes" : "item_category_codes";
      const otherSelected = selected.includes(code)
        ? current[other].filter((item) => item !== code)
        : current[other];
      return { ...current, [field]: selected, [other]: otherSelected };
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
    if (type === "DEMAND") {
      if (!form.desired_date) {
        return messages.common.required;
      }
    } else {
      if (!form.flight_date || !form.date_from || !form.date_to) {
        return messages.common.required;
      }
      if (form.date_from > form.date_to) {
        return messages.form.dateOrder;
      }
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
      description: form.description,
      item_category_codes: form.item_category_codes,
    };
    if (type === "DEMAND") {
      payload.desired_date = form.desired_date;
      payload.weight_kg = form.weight_kg;
    } else {
      payload.flight_date = form.flight_date;
      payload.date_from = form.date_from;
      payload.date_to = form.date_to;
      payload.capacity_kg = form.capacity_kg;
      payload.excluded_category_codes = form.excluded_category_codes;
      payload.excluded_other_text = form.excluded_other_text;
    }
    try {
      const saved = existing
        ? await updateRequest(existing.id, payload)
        : await createRequest(payload);
      router.push(existing ? `/app/requests/${saved.id}` : `/app/requests/${saved.id}/created`);
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
          <Row label={type === "SUPPLY" ? messages.form.flightDate : messages.form.desiredDate}>
            {type === "SUPPLY"
              ? form.flight_date
              : form.desired_date}
          </Row>
          {type === "SUPPLY" ? (
            <Row label={messages.form.carryWindow}>
              {formatDateRange(form.date_from, form.date_to, locale)}
            </Row>
          ) : null}
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
        <CityField
          key={`origin-${form.origin_country}`}
          cities={originCities}
          value={form.origin_city}
          onChange={(next) => update("origin_city", next)}
          disabled={!form.origin_country}
          locale={locale}
          selectLabel={messages.form.selectCity}
          otherLabel={messages.form.cityOther}
          placeholder={messages.form.cityOtherPlaceholder}
        />
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
        <CityField
          key={`destination-${form.destination_country}`}
          cities={destinationCities}
          value={form.destination_city}
          onChange={(next) => update("destination_city", next)}
          disabled={!form.destination_country}
          locale={locale}
          selectLabel={messages.form.selectCity}
          otherLabel={messages.form.cityOther}
          placeholder={messages.form.cityOtherPlaceholder}
        />
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-slate-900">
          {type === "SUPPLY" ? messages.form.flightDate : messages.form.desiredDate}
        </legend>
        {type === "DEMAND" ? (
          <label className="block text-sm text-slate-600">
            {messages.form.desiredDate}
            <input
              className={`${inputClass} mt-1`}
              type="date"
              value={form.desired_date}
              onChange={(event) => update("desired_date", event.target.value)}
              required
            />
          </label>
        ) : (
          <>
            <label className="block text-sm text-slate-600">
              {messages.form.flightDate}
              <input
                className={`${inputClass} mt-1`}
                type="date"
                value={form.flight_date}
                onChange={(event) => update("flight_date", event.target.value)}
                required
              />
            </label>
            <label className="block text-sm text-slate-600">
              {messages.form.carryFrom}
              <input
                className={`${inputClass} mt-1`}
                type="date"
                value={form.date_from}
                onChange={(event) => update("date_from", event.target.value)}
                required
              />
            </label>
            <label className="block text-sm text-slate-600">
              {messages.form.carryTo}
              <input
                className={`${inputClass} mt-1`}
                type="date"
                value={form.date_to}
                onChange={(event) => update("date_to", event.target.value)}
                required
              />
            </label>
          </>
        )}
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
            onChange={(event) => {
              kgTouched.current = true;
              update("weight_kg", event.target.value);
            }}
            required
          />
          <span className="mt-1 block text-sm text-slate-500">{kgHint}</span>
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
            onChange={(event) => {
              kgTouched.current = true;
              update("capacity_kg", event.target.value);
            }}
            required
          />
          <span className="mt-1 block text-sm text-slate-500">{kgHint}</span>
        </label>
      )}

      {type === "DEMAND" ? (
        <fieldset>
          <legend className="mb-2 text-sm font-medium">{messages.form.category}</legend>
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
      ) : (
        <>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">{messages.form.canCarry}</legend>
            <p className="mb-3 text-sm text-slate-500">{messages.form.categoryChoiceHint}</p>
            <div className="flex flex-col divide-y divide-slate-200 rounded-2xl border border-slate-200">
              {categories.map((category) => {
                const canCarry = form.item_category_codes.includes(category.code);
                const willNot = form.excluded_category_codes.includes(category.code);
                return (
                  <div key={category.code} className="flex items-center justify-between gap-3 px-3 py-2.5">
                    <span className="text-sm">{localizedName(category, locale)}</span>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        className={`rounded-lg px-3 py-1.5 text-xs ${
                          canCarry ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-500"
                        }`}
                        onClick={() => toggleCode("item_category_codes", category.code)}
                      >
                        {messages.form.canCarryShort}
                      </button>
                      <button
                        type="button"
                        className={`rounded-lg px-3 py-1.5 text-xs ${
                          willNot ? "bg-rose-100 text-rose-800" : "bg-slate-100 text-slate-500"
                        }`}
                        onClick={() => toggleCode("excluded_category_codes", category.code)}
                      >
                        {messages.form.willNotCarryShort}
                      </button>
                    </div>
                  </div>
                );
              })}
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
