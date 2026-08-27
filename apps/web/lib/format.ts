import type { Locale } from "@/lib/types";

export function formatDateRange(startIso: string, endIso: string, locale: Locale): string {
  const start = parseIsoDate(startIso);
  const end = parseIsoDate(endIso);
  if (!start || !end) {
    return startIso === endIso ? startIso : `${startIso} – ${endIso}`;
  }
  const localeTag = locale === "fa" ? "fa-IR" : "en-US";
  if (start.getTime() === end.getTime()) {
    return new Intl.DateTimeFormat(localeTag, { month: "long", day: "numeric" }).format(start);
  }
  if (start.getMonth() === end.getMonth() && start.getFullYear() === end.getFullYear()) {
    const month = new Intl.DateTimeFormat(localeTag, { month: "short" }).format(start);
    return `${month} ${start.getDate()}–${end.getDate()}`;
  }
  const options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  return `${new Intl.DateTimeFormat(localeTag, options).format(start)} – ${new Intl.DateTimeFormat(localeTag, options).format(end)}`;
}

export function formatKg(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return value;
  }
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2).replace(/\.?0+$/, "");
}

export function formatScorePercent(score: string): string {
  const amount = Number(score);
  if (Number.isNaN(amount)) {
    return score;
  }
  return `${Math.round(amount)}%`;
}

export function todayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export function addDaysIso(iso: string, days: number): string {
  const date = parseIsoDate(iso);
  if (!date) {
    return iso;
  }
  date.setDate(date.getDate() + days);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function parseIsoDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return null;
  }
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}
