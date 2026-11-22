// Build-time statistics for the public site.
//
// Order of preference:
//   1. GET {API_BASE}/api/v1/public/stats/  (contract in PLAN §4) when it answers 200.
//   2. Compute the same shape from the public feed snapshots (/feeds/manifest.json + city feeds).
//   3. The committed fixture src/data/stats.fixture.json (offline builds, CI).
//
// The latest-listings sample for city hubs always comes from the public feeds when they answer.
// Raw post text is used only for de-duplication and is never written out.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const OUT = resolve(root, 'src/data/stats.json');
const FIXTURE = resolve(root, 'src/data/stats.fixture.json');
const API_BASE = (process.env.API_BASE || 'https://chamedoon.bigde.li').replace(/\/$/, '');
const OFFLINE = process.env.STATS_OFFLINE === '1';
// Build-only secret for the statistics endpoint (backend PUBLIC_STATS_TOKEN). Never PUBLIC_*, never in the output.
async function statsToken() {
  if (process.env.STATS_TOKEN) return process.env.STATS_TOKEN.trim();
  for (const name of ['.env.production.local', '.env.local', '.env']) {
    try {
      const line = (await readFile(resolve(root, name), 'utf8')).split(String.fromCharCode(10)).map((l) => l.trim()).find((l) => l.startsWith('STATS_TOKEN='));
      if (line) return line.slice('STATS_TOKEN='.length).trim();
    } catch {}
  }
  return '';
}

const CITIES = [
  { slug: 'toronto', name_fa: 'تورنتو', name_en: 'Toronto', country: 'CA', currency: 'CAD', tz: 'America/Toronto' },
  { slug: 'montreal', name_fa: 'مونترال', name_en: 'Montreal', country: 'CA', currency: 'CAD', tz: 'America/Toronto' },
  { slug: 'vancouver', name_fa: 'ونکوور', name_en: 'Vancouver', country: 'CA', currency: 'CAD', tz: 'America/Vancouver' },
  { slug: 'berlin', name_fa: 'برلین', name_en: 'Berlin', country: 'DE', currency: 'EUR', tz: 'Europe/Berlin' },
  { slug: 'hamburg', name_fa: 'هامبورگ', name_en: 'Hamburg', country: 'DE', currency: 'EUR', tz: 'Europe/Berlin' },
  { slug: 'cologne', name_fa: 'کلن', name_en: 'Cologne', country: 'DE', currency: 'EUR', tz: 'Europe/Berlin' },
];
const TYPES = ['room', 'studio', 'basement', 'apartment', 'house'];
const FIRST_WEEK = '2026-11-02'; // never compare with weeks before this (CONTENT-SPEC §4)
const MIN_N = 8;

async function getJson(url, ms = 20000, extra = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { 'user-agent': 'chamedoon-site-build/1', accept: 'application/json', ...extra } });
    if (r.status !== 200) return { status: r.status };
    return { status: 200, data: await r.json() };
  } catch (e) {
    return { status: 0, error: String(e) };
  } finally {
    clearTimeout(t);
  }
}

const quantile = (sorted, q) => {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
};
const round = (v, step = 5) => (v == null ? null : Math.round(v / step) * step);

function summarise(prices) {
  const n = prices.length;
  let s = [...prices].sort((a, b) => a - b);
  if (n >= 20) {
    const p5 = quantile(s, 0.05);
    const p95 = quantile(s, 0.95);
    s = s.filter((v) => v >= p5 && v <= p95);
  }
  if (n < MIN_N) return { n, median: null, p25: null, p75: null };
  return { n, median: round(quantile(s, 0.5)), p25: round(quantile(s, 0.25)), p75: round(quantile(s, 0.75)) };
}

// Local calendar date (YYYY-MM-DD) and ISO weekday (0 = Monday) in the city's time zone.
function localParts(iso, tz) {
  const d = new Date(iso);
  const f = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
  const p = Object.fromEntries(f.formatToParts(d).map((x) => [x.type, x.value]));
  const wd = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(p.weekday);
  return { date: `${p.year}-${p.month}-${p.day}`, weekday: wd };
}
function weekStart(dateStr, weekday) {
  const d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - weekday);
  return d.toISOString().slice(0, 10);
}

function dedupe(listings) {
  const seen = new Set();
  const out = [];
  for (const l of listings) {
    const words = (l.find || '').replace(/[^\p{L}\p{N}]+/gu, '').slice(0, 120);
    const key = words ? `${words}|${l.price_monthly ?? ''}` : `id:${l.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(l);
  }
  return out;
}

function areaName(raw) {
  const [fa, en] = String(raw || '').split(' · ');
  return { name_fa: (fa || '').trim(), name: (en || fa || '').replace(/\s*\(.*\)\s*$/, '').trim() };
}

function computeCity(meta, feed, now) {
  const all = dedupe(feed.listings || []);
  const offers = all.filter((l) => l.kind === 'offer');
  const wanted = all.filter((l) => l.kind === 'wanted');
  const ageH = (l) => (now - new Date(l.first_seen).getTime()) / 3.6e6;
  const priced = offers.filter((l) => l.price_period === 'month' && Number(l.price_monthly) > 0);
  const last30 = priced.filter((l) => ageH(l) <= 24 * 30);

  const medians_30d = TYPES.map((type) => ({ type, ...summarise(last30.filter((l) => l.property_type === type).map((l) => Number(l.price_monthly))) }));

  // Weekly rows from the first comparable week to now (local weeks, Monday start).
  const byWeek = new Map();
  for (const l of all) {
    const { date, weekday } = localParts(l.first_seen, meta.tz);
    const ws = weekStart(date, weekday);
    if (ws < FIRST_WEEK) continue;
    if (!byWeek.has(ws)) byWeek.set(ws, []);
    byWeek.get(ws).push(l);
  }
  const weekly = [...byWeek.keys()].sort().map((ws) => {
    const rows = byWeek.get(ws);
    const p = rows.filter((l) => l.kind === 'offer' && l.price_period === 'month' && Number(l.price_monthly) > 0);
    return {
      week_start: ws,
      new: rows.filter((l) => l.kind === 'offer').length,
      new_wanted: rows.filter((l) => l.kind === 'wanted').length,
      medians: TYPES.map((type) => {
        const s = summarise(p.filter((l) => l.property_type === type).map((l) => Number(l.price_monthly)));
        return { type, n: s.n, median: s.median };
      }),
    };
  });

  // Areas: rooms in the last 30 days.
  const areas = new Map();
  for (const l of last30.filter((x) => x.property_type === 'room' && x.area)) {
    const key = l.area_id ? `id:${l.area_id}` : l.area;
    if (!areas.has(key)) areas.set(key, { raw: l.area, prices: [] });
    areas.get(key).prices.push(Number(l.price_monthly));
  }
  const areas_30d = [...areas.values()]
    .map((a) => ({ ...areaName(a.raw), n: a.prices.length, median_room: summarise(a.prices).median }))
    .filter((a) => a.n >= 4)
    .sort((a, b) => b.n - a.n)
    .slice(0, 10);

  const by_weekday = [0, 0, 0, 0, 0, 0, 0];
  for (const l of offers.filter((x) => ageH(x) <= 24 * 30)) by_weekday[localParts(l.first_seen, meta.tz).weekday]++;

  const share = (arr, fn) => (arr.length ? Math.round((arr.filter(fn).length / arr.length) * 1000) / 1000 : 0);

  return {
    slug: meta.slug,
    name_fa: meta.name_fa,
    name_en: meta.name_en,
    country: meta.country,
    currency: meta.currency,
    live: { offers: offers.length, wanted: wanted.length },
    new_24h: offers.filter((l) => ageH(l) <= 24).length,
    new_7d: offers.filter((l) => ageH(l) <= 24 * 7).length,
    new_30d: offers.filter((l) => ageH(l) <= 24 * 30).length,
    medians_30d,
    weekly,
    areas_30d,
    by_weekday,
    with_photo_share: share(offers, (l) => Number(l.photo_count) > 0),
    furnished_share: share(offers, (l) => l.furnished === 'yes'),
  };
}

function latestSample(feed) {
  return dedupe(feed.listings || [])
    .filter((l) => l.kind === 'offer' && l.price_period === 'month' && Number(l.price_monthly) > 0 && TYPES.includes(l.property_type))
    .sort((a, b) => b.first_seen.localeCompare(a.first_seen))
    .slice(0, 6)
    .map((l) => ({
      id: l.id,
      type: l.property_type,
      area_fa: areaName(l.area).name_fa,
      area_en: areaName(l.area).name,
      price: Number(l.price_monthly),
      currency: l.price_currency,
      furnished: l.furnished,
      first_seen: l.first_seen,
    }));
}

async function fromFeeds() {
  const m = await getJson(`${API_BASE}/feeds/manifest.json`);
  if (m.status !== 200) return null;
  const now = Date.now();
  const cities = [];
  const latest = {};
  for (const meta of CITIES) {
    const entry = (m.data.cities || []).find((c) => c.city_slug === meta.slug);
    if (!entry) return null;
    const f = await getJson(`${API_BASE}${entry.url}`, 60000);
    if (f.status !== 200) return null;
    cities.push(computeCity(meta, f.data, now));
    latest[meta.slug] = latestSample(f.data);
  }
  return { generated_at: m.data.generated_at, cities, latest };
}

function validEndpoint(d) {
  return d && Array.isArray(d.cities) && d.cities.length >= 6 && d.cities.every((c) => c.slug && Array.isArray(c.medians_30d)) && (d.totals?.listings_live ?? 1) > 0 && d.cities.some((c) => c.live?.offers > 0);
}

async function main() {
  let out = null;
  if (!OFFLINE) {
    const token = await statsToken();
    if (!token) console.log('[stats] no STATS_TOKEN: the endpoint is locked, using the public feeds');
    const ep = token ? await getJson(`${API_BASE}/api/v1/public/stats/`, 60000, { 'x-stats-token': token }) : { status: 'no-token' };
    const feeds = await fromFeeds();
    if (ep.status === 200 && validEndpoint(ep.data)) {
      out = { ...ep.data, source: 'endpoint', latest: feeds?.latest || {} };
      console.log(`[stats] endpoint 200, generated_at ${ep.data.generated_at}`);
    } else if (feeds) {
      const listings_live = feeds.cities.reduce((s, c) => s + c.live.offers + c.live.wanted, 0);
      out = {
        generated_at: feeds.generated_at,
        method_version: '1-feed',
        source: 'feeds',
        cities: feeds.cities,
        courier: { live_trips: null, live_requests: null, routes_30d: [] },
        totals: { sources_read: null, posts_reviewed_30d: null, listings_live },
        latest: feeds.latest,
      };
      console.log(`[stats] endpoint ${ep.status}; computed from public feeds (${feeds.generated_at})`);
    }
  }
  const LASTGOOD = resolve(root, 'src/data/stats.lastgood.json');
  if (!out) {
    // Keep the last good numbers (never zeros): last successful build on this machine, else the committed fixture.
    try { out = { ...JSON.parse(await readFile(LASTGOOD, 'utf8')), source: 'fixture' }; console.log('[stats] using last good stats'); }
    catch { out = { ...JSON.parse(await readFile(FIXTURE, 'utf8')), source: 'fixture' }; console.log('[stats] using committed fixture'); }
  } else {
    await writeFile(LASTGOOD, JSON.stringify(out));
  }
  await mkdir(dirname(OUT), { recursive: true });
  await writeFile(OUT, JSON.stringify(out, null, 1));
  // Monthly report snapshot: the current month is overwritten until the month ends; older months stay frozen,
  // so /fa/reports/{yyyy-mm}/ never changes once the month is over.
  if (out.source !== 'fixture') {
    const month = String(out.generated_at).slice(0, 7);
    const dir = resolve(root, 'src/data/reports');
    await mkdir(dir, { recursive: true });
    const { latest, ...snap } = out;
    await writeFile(resolve(dir, `${month}.json`), JSON.stringify(snap, null, 1));
  }
  if (process.argv.includes('--write-fixture')) {
    await writeFile(FIXTURE, JSON.stringify({ ...out, source: undefined }, null, 1));
    console.log('[stats] fixture refreshed');
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
