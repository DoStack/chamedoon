// Site-wide constants. Public facts only (PLAN D16).
export const SITE_URL = (import.meta.env.SITE as string | undefined)?.replace(/\/$/, '') || 'https://chamedoon.bigde.li';
export const abs = (path: string) => new URL(path, SITE_URL + '/').toString();

export const BRAND = {
  name_fa: 'چمدون',
  name_en: 'Chamedoon',
  orange: '#B8410C',
  navy: '#0B1F33',
};

export const HOME_BOT = 'chamedoon_home_bot';
export const COURIER_BOT = 'chamedoon_courier_bot';
export const tme = (u: string) => `https://t.me/${u}`;

export const APP_HOME = '/app/home/';
export const APP_COURIER = '/app/';

export type Lang = 'fa' | 'en';
export type CitySlug = 'toronto' | 'montreal' | 'vancouver' | 'berlin' | 'hamburg' | 'cologne';

export interface CityMeta {
  slug: CitySlug;
  fa: string;
  en: string;
  country: 'CA' | 'DE';
  country_fa: string;
  country_en: string;
  currency: 'CAD' | 'EUR';
  wikidata: string;
  lat: number;
  lng: number;
  channel: string;
  region: string;
}

export const CITIES: CityMeta[] = [
  { slug: 'toronto', fa: 'تورنتو', en: 'Toronto', country: 'CA', country_fa: 'کانادا', country_en: 'Canada', currency: 'CAD', wikidata: 'Q172', lat: 43.65, lng: -79.38, channel: 'chamedoon_toronto_khane', region: 'ON' },
  { slug: 'montreal', fa: 'مونترال', en: 'Montreal', country: 'CA', country_fa: 'کانادا', country_en: 'Canada', currency: 'CAD', wikidata: 'Q340', lat: 45.5, lng: -73.57, channel: 'chamedoon_montreal_khane', region: 'QC' },
  { slug: 'vancouver', fa: 'ونکوور', en: 'Vancouver', country: 'CA', country_fa: 'کانادا', country_en: 'Canada', currency: 'CAD', wikidata: 'Q24639', lat: 49.28, lng: -123.12, channel: 'chamedoon_vancouver_khane', region: 'BC' },
  { slug: 'berlin', fa: 'برلین', en: 'Berlin', country: 'DE', country_fa: 'آلمان', country_en: 'Germany', currency: 'EUR', wikidata: 'Q64', lat: 52.52, lng: 13.405, channel: 'chamedoon_berlin_khane', region: 'BE' },
  { slug: 'hamburg', fa: 'هامبورگ', en: 'Hamburg', country: 'DE', country_fa: 'آلمان', country_en: 'Germany', currency: 'EUR', wikidata: 'Q1055', lat: 53.55, lng: 9.99, channel: 'chamedoon_hamburg_khane', region: 'HH' },
  { slug: 'cologne', fa: 'کلن', en: 'Cologne', country: 'DE', country_fa: 'آلمان', country_en: 'Germany', currency: 'EUR', wikidata: 'Q365', lat: 50.94, lng: 6.96, channel: 'chamedoon_koln_khane', region: 'NW' },
];
export const cityBySlug = (s: string) => CITIES.find((c) => c.slug === s)!;

export const IRAN_CITIES = [
  { fa: 'تهران', en: 'Tehran', lat: 35.69, lng: 51.39 },
  { fa: 'شیراز', en: 'Shiraz', lat: 29.59, lng: 52.58 },
  { fa: 'مشهد', en: 'Mashhad', lat: 36.3, lng: 59.6 },
];

// Public Telegram surfaces (research/06 §1). No member counts, no private invite links.
export interface Surface {
  username: string;
  kind: 'channel' | 'group' | 'bot';
  product: 'khane' | 'barbari';
  scope: CitySlug | 'CA' | 'DE' | 'all';
  fa: string;
  en: string;
}
export const SURFACES: Surface[] = [
  { username: HOME_BOT, kind: 'bot', product: 'khane', scope: 'all', fa: 'ربات و مینی‌اپ چمدونِ خانه', en: 'Chamedoon Home bot and Mini App' },
  { username: 'Chamedoon_CA', kind: 'channel', product: 'khane', scope: 'CA', fa: 'چمدون - خانه | کانادا', en: 'Chamedoon Home | Canada' },
  { username: 'chamedoon_toronto_khane', kind: 'channel', product: 'khane', scope: 'toronto', fa: 'چمدون - خانه · تورنتو', en: 'Chamedoon Home · Toronto' },
  { username: 'chamedoon_montreal_khane', kind: 'channel', product: 'khane', scope: 'montreal', fa: 'چمدون - خانه · مونترال', en: 'Chamedoon Home · Montreal' },
  { username: 'chamedoon_khane_montreal_chat', kind: 'group', product: 'khane', scope: 'montreal', fa: 'گروه گفت‌وگوی چمدون - خانه · مونترال', en: 'Chamedoon Home · Montreal discussion group' },
  { username: 'chamedoon_vancouver_khane', kind: 'channel', product: 'khane', scope: 'vancouver', fa: 'چمدون - خانه · ونکوور', en: 'Chamedoon Home · Vancouver' },
  { username: 'Chamedoon_Germany', kind: 'channel', product: 'khane', scope: 'DE', fa: 'چمدون - خانه | آلمان', en: 'Chamedoon Home | Germany' },
  { username: 'chamedoon_berlin_khane', kind: 'channel', product: 'khane', scope: 'berlin', fa: 'چمدون - خانه · برلین', en: 'Chamedoon Home · Berlin' },
  { username: 'chamedoon_hamburg_khane', kind: 'channel', product: 'khane', scope: 'hamburg', fa: 'چمدون - خانه · هامبورگ', en: 'Chamedoon Home · Hamburg' },
  { username: 'chamedoon_koln_khane', kind: 'channel', product: 'khane', scope: 'cologne', fa: 'چمدون - خانه · کلن', en: 'Chamedoon Home · Cologne' },
  { username: COURIER_BOT, kind: 'bot', product: 'barbari', scope: 'all', fa: 'ربات و مینی‌اپ چمدونِ باربری', en: 'Chamedoon Courier bot and Mini App' },
  { username: 'chamedoon_courier', kind: 'channel', product: 'barbari', scope: 'all', fa: 'چمدون - باربری', en: 'Chamedoon Courier' },
  { username: 'chamedoon_courier_ca', kind: 'channel', product: 'barbari', scope: 'CA', fa: 'چمدون - باربری · کانادا', en: 'Chamedoon Courier · Canada' },
  { username: 'chamedoon_courier_de', kind: 'channel', product: 'barbari', scope: 'DE', fa: 'چمدون - باربری · آلمان', en: 'Chamedoon Courier · Germany' },
];

export const TYPE_LABEL: Record<string, { fa: string; en: string; fa_plural: string }> = {
  room: { fa: 'اتاق', en: 'Room', fa_plural: 'اتاق' },
  studio: { fa: 'استودیو', en: 'Studio', fa_plural: 'استودیو' },
  basement: { fa: 'بیسمنت', en: 'Basement unit', fa_plural: 'بیسمنت' },
  apartment: { fa: 'آپارتمان', en: 'Apartment', fa_plural: 'آپارتمان' },
  house: { fa: 'خانه', en: 'House', fa_plural: 'خانه' },
};

export const CURRENCY: Record<string, { fa: string; en: string; sym: string }> = {
  CAD: { fa: 'دلار کانادا', en: 'CAD', sym: 'CA$' },
  EUR: { fa: 'یورو', en: 'EUR', sym: '€' },
};

// Approved guide videos (research/06 §3), posted in all eight housing channels.
export const VIDEOS = [
  {
    slug: 'find-a-home',
    file: 'guide-seeker.mp4',
    poster: '/media/site/img/guide-seeker-poster.jpg',
    captions: '/media/site/captions/guide-seeker.fa.vtt',
    duration: 37,
    uploadDate: '2026-11-12',
    width: 1080,
    height: 1920,
    title_fa: 'راهنمای چمدونِ خانه: چطور خونه پیدا کنیم؟',
    title_en: 'Chamedoon Home guide: how to find a home',
    description_fa: 'در کمتر از یک دقیقه ببینید چطور با چمدونِ خانه آگهی‌های خونهٔ گروه‌ها و کانال‌های ایرانی شهرتان را یک‌جا ببینید، فیلتر کنید و گوش‌به‌زنگ بسازید.',
  },
  {
    slug: 'post-a-listing',
    file: 'guide-owner.mp4',
    poster: '/media/site/img/guide-owner-poster.jpg',
    captions: '/media/site/captions/guide-owner.fa.vtt',
    duration: 41,
    uploadDate: '2026-11-12',
    width: 1080,
    height: 1920,
    title_fa: 'راهنمای چمدونِ خانه: آگهی خونه‌تان را بگذارید',
    title_en: 'Chamedoon Home guide: post your listing',
    description_fa: 'ببینید چطور مشخصات و عکس‌های خونه‌تان را در چمدونِ خانه وارد کنید تا آگهی مرتب شود و به دست کسانی برسد که دنبال همین خونه‌اند.',
  },
] as const;

export const MEDIA_BASE = '/media/site/v';
