import { CURRENCY, TYPE_LABEL, type Lang } from './site';

const faNf = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 0 });
const enNf = new Intl.NumberFormat('en-CA', { maximumFractionDigits: 0 });

export const num = (n: number | null | undefined, lang: Lang = 'fa') =>
  n == null ? '—' : (lang === 'fa' ? faNf : enNf).format(n);

export const pct = (share: number | null | undefined, lang: Lang = 'fa') => {
  if (share == null) return '—';
  const v = Math.round(share * 100);
  return lang === 'fa' ? `${faNf.format(v)}٪` : `${v}%`;
};

export const money = (n: number | null | undefined, cur: string, lang: Lang = 'fa') => {
  if (n == null) return '—';
  const c = CURRENCY[cur] || { fa: cur, en: cur, sym: cur };
  return lang === 'fa' ? `${faNf.format(n)} ${c.fa}` : `${c.sym}${enNf.format(n)}`;
};

export const moneyShort = (n: number | null | undefined, cur: string, lang: Lang = 'fa') => {
  if (n == null) return '—';
  const c = CURRENCY[cur];
  if (lang === 'en') return `${c?.sym ?? ''}${enNf.format(n)}`;
  return `${faNf.format(n)} ${cur === 'EUR' ? 'یورو' : 'دلار'}`;
};

export const typeLabel = (t: string, lang: Lang = 'fa') => TYPE_LABEL[t]?.[lang] ?? t;

const greg = new Intl.DateTimeFormat('fa-IR', { calendar: 'gregory', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const gregShort = new Intl.DateTimeFormat('fa-IR', { calendar: 'gregory', day: 'numeric', month: 'long', timeZone: 'UTC' });
const shamsi = new Intl.DateTimeFormat('fa-IR-u-ca-persian', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const enDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const gregMonth = new Intl.DateTimeFormat('fa-IR', { calendar: 'gregory', month: 'long', year: 'numeric', timeZone: 'UTC' });
const enMonth = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });

const toDate = (d: string | Date) => (d instanceof Date ? d : new Date(d.length === 10 ? d + 'T12:00:00Z' : d));

/** Gregorian first, Shamsi second (owner rule). */
export const dateParts = (d: string | Date) => {
  const x = toDate(d);
  return { iso: x.toISOString().slice(0, 10), greg: greg.format(x), shamsi: shamsi.format(x).replace(/ هجری شمسی$/, ''), en: enDate.format(x) };
};
export const dateFa = (d: string | Date) => {
  const p = dateParts(d);
  return `${p.greg} (${p.shamsi})`;
};
export const dateShortFa = (d: string | Date) => gregShort.format(toDate(d));
export const monthName = (d: string | Date, lang: Lang = 'fa') => (lang === 'fa' ? gregMonth : enMonth).format(toDate(d));

/** Strip ZWNJ etc. for slugs; slugs are ASCII anyway. */
export const plain = (s: string) => s.replace(/[‌‏‎]/g, '');

export const faDigits = (s: string | number) => String(s).replace(/\d/g, (d) => '۰۱۲۳۴۵۶۷۸۹'[+d]);
