// Statistics baked at build time (scripts/fetch-stats.mjs writes src/data/stats.json).
import raw from '../data/stats.json';
import { CITIES, type CitySlug } from './site';

export interface Median { type: string; n: number; median: number | null; p25?: number | null; p75?: number | null }
export interface Week { week_start: string; new: number; new_wanted?: number; medians: Median[] }
export interface Day { date: string; new: number }
export interface Area { name: string; name_fa?: string; n: number; median_room: number | null }
export interface CityStats {
  slug: CitySlug; name_fa: string; name_en: string; country: string; currency: string;
  live: { offers: number; wanted: number };
  new_24h: number; new_7d: number; new_30d: number;
  medians_30d: Median[]; weekly: Week[]; daily?: Day[]; areas_30d: Area[];
  by_weekday: number[]; with_photo_share: number; furnished_share: number;
}
export interface Latest { id: number; type: string; area_fa: string; area_en: string; price: number; currency: string; furnished: string; first_seen: string }
export interface Stats {
  generated_at: string; method_version: string; source: 'endpoint' | 'feeds' | 'fixture';
  cities: CityStats[];
  courier: {
    live_trips: number | null; live_requests: number | null; routes_30d: { from: string; to: string; trips: number; requests: number }[];
    weekly?: { week_start: string; trips: number; requests: number }[];
    daily?: { date: string; trips: number; requests: number }[];
  };
  totals: { sources_read: number | null; posts_reviewed_30d: number | null; listings_live: number | null };
  latest: Record<string, Latest[]>;
}

export const stats = raw as unknown as Stats;
export const MIN_N = 8;
export const TYPES = ['room', 'studio', 'basement', 'apartment', 'house'] as const;

export const generatedAt = stats.generated_at;
export const updatedDay = stats.generated_at.slice(0, 10);
export const reportMonth = updatedDay.slice(0, 7); // yyyy-mm

export const cityStats = (slug: string) => stats.cities.find((c) => c.slug === slug)!;

/** Median only when n ≥ 8, else null (shown as «—»). */
export const safeMedian = (m?: Median | null) => (m && m.n >= MIN_N && m.median != null ? m.median : null);

export const medianOf = (slug: string, type: string) => safeMedian(cityStats(slug)?.medians_30d.find((m) => m.type === type));

export const typesWithData = (slug: string) =>
  (cityStats(slug)?.medians_30d || []).filter((m) => safeMedian(m) != null).map((m) => m.type);

/** Headline type for a city: the most common type that has a median. */
export const headlineType = (slug: string) => {
  const ms = (cityStats(slug)?.medians_30d || []).filter((m) => safeMedian(m) != null);
  const pref = ['apartment', 'room', 'studio', 'basement'];
  return pref.find((t) => ms.some((m) => m.type === t)) || ms[0]?.type || 'room';
};

export const weeklySeries = (slug: string, type: string) =>
  (cityStats(slug)?.weekly || []).map((w) => {
    const m = w.medians.find((x) => x.type === type);
    return { week: w.week_start, value: safeMedian(m as Median), n: m?.n ?? 0, count: w.new };
  });

export const totals = () => ({
  listings_live: stats.totals.listings_live ?? stats.cities.reduce((s, c) => s + c.live.offers + c.live.wanted, 0),
  new_7d: stats.cities.reduce((s, c) => s + c.new_7d, 0),
  new_30d: stats.cities.reduce((s, c) => s + c.new_30d, 0),
  cities: CITIES.length,
  sources_read: stats.totals.sources_read,
  posts_reviewed_30d: stats.totals.posts_reviewed_30d,
  live_trips: stats.courier?.live_trips ?? null,
  live_requests: stats.courier?.live_requests ?? null,
});

export const latest = (slug: string) => stats.latest?.[slug] || [];

/** Complete local days since 2026-11-02 (endpoint `daily`). Empty when the build had no daily data. */
export const dailyNew = (slug: string) => cityStats(slug)?.daily || [];

/** Types that have a weekly median (n ≥ 8) in at least one complete week. */
export const weeklyTypes = (slug: string) =>
  TYPES.filter((t) => (cityStats(slug)?.weekly || []).some((w) => safeMedian(w.medians.find((m) => m.type === t)) != null));

/** Daily totals per country (sum of its cities), aligned on the dates every city has. */
export const dailyByCountry = () => {
  const dates = (stats.cities[0]?.daily || []).map((d) => d.date);
  const sum = (country: string) => dates.map((date) => stats.cities.filter((c) => c.country === country).reduce((s, c) => s + (c.daily?.find((d) => d.date === date)?.new ?? 0), 0));
  return { dates, CA: sum('CA'), DE: sum('DE') };
};

export const courierWeekly = () => stats.courier?.weekly || [];
export const courierDaily = () => stats.courier?.daily || [];
