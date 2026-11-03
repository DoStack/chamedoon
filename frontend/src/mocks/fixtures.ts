// Sample data for tests and the Preview's `?tgapi=mock`, in the contract's shapes. Same listings as the design boards.
import type { Card, Cities, Contact, DeepLink, Exchange, GateChannel, Listing, Manifest, Meta, SnapshotFile } from '../api/types'

const HOUR = 3_600_000
const ago = (hours: number) => new Date(Date.now() - hours * HOUR).toISOString()

export const META: Meta = {
  api_version: 1,
  min_api_version: 1,
  build_id: 'dev',
  snapshot_schema_version: 1,
  manifest_url: '/feeds/manifest.json',
  miniapp_v2: true,
  contact_gate_on: true,
  snapshots_on: true,
  alerts_on: false,
  maintenance: { on: false, until: null },
}

export const TORONTO_CHANNEL: GateChannel = { username: 'chamedoon_toronto_khane', title: 'کانال چمدون - خانه · تورنتو', city: 'Toronto' }
export const CANADA_CHANNEL: GateChannel = { username: 'chamedoon_canada_khane', title: 'کانال چمدون - خانه · کانادا', city: '' }

const channel = (city: string, title: string): GateChannel => ({ username: `chamedoon_${city.toLowerCase()}`, title, city })

export const CITIES: Cities = {
  countries: [
    {
      code: 'CA',
      channel: CANADA_CHANNEL,
      cities: [
        { slug: 'toronto', name: 'Toronto', name_fa: 'تورنتو', listing_count: 42, channel: TORONTO_CHANNEL },
        { slug: 'montreal', name: 'Montreal', name_fa: 'مونترال', listing_count: 18, channel: channel('Montreal', 'کانال چمدون - خانه · مونترال') },
        { slug: 'vancouver', name: 'Vancouver', name_fa: 'ونکوور', listing_count: 0, channel: channel('Vancouver', 'کانال چمدون - خانه · ونکوور') },
      ],
    },
    {
      code: 'DE',
      channel: channel('', 'کانال چمدون - خانه · آلمان'),
      cities: [
        { slug: 'berlin', name: 'Berlin', name_fa: 'برلین', listing_count: 24, channel: channel('Berlin', 'کانال چمدون - خانه · برلین') },
        { slug: 'hamburg', name: 'Hamburg', name_fa: 'هامبورگ', listing_count: 9, channel: channel('Hamburg', 'کانال چمدون - خانه · هامبورگ') },
        { slug: 'cologne', name: 'Cologne', name_fa: 'کلن و اطراف', listing_count: 48, channel: channel('Cologne', 'کانال چمدون - خانه · کلن') },
      ],
    },
  ],
}

function card(id: number, patch: Partial<Card>): Card {
  return {
    id,
    kind: 'offer',
    property_type: 'apartment',
    rental_mode: 'long_term',
    country: 'CA',
    region: 'ON',
    city: 'Toronto',
    metro: 'Toronto',
    metro_slug: 'toronto',
    area: 'نورث یورک · Yonge و Finch',
    area_id: null,
    price_amount: 1850,
    price_max: null,
    price_currency: 'CAD',
    price_period: 'month',
    price_basis: 'unknown',
    price_monthly: 1850,
    rooms: 1,
    size_m2: 55,
    furnished: 'yes',
    bills_included: 'yes',
    registration: 'unknown',
    roommate_gender: 'any',
    lister_type: 'owner',
    term: 'lease',
    occupants_wanted: null,
    couples_ok: false,
    wbs_required: false,
    value_ratio: null,
    summary_fa: '',
    summary_en: '',
    available_from: '2026-12-01',
    available_to: null,
    first_seen: ago(3),
    last_seen: ago(3),
    thumb: '',
    photo_count: 4,
    ...patch,
  }
}

const PIXEL = (hue: number) =>
  `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 3"><rect width="4" height="3" fill="hsl(${hue},25%,80%)"/></svg>`)}`

export const TORONTO: Card[] = [
  card(101, { thumb: PIXEL(20) }),
  card(102, { rooms: 2, size_m2: 75, price_amount: 2450, price_monthly: 2450, area: 'وان · Jane و Rutherford', first_seen: ago(40), last_seen: ago(5), thumb: PIXEL(60) }),
  card(103, { property_type: 'room', rooms: null, size_m2: 14, price_amount: 950, price_monthly: 950, area: 'ریچموند هیل · Hillcrest', rental_mode: 'sublet', available_from: '2026-12-01', available_to: '2027-02-28', first_seen: ago(60), last_seen: ago(50), thumb: PIXEL(100) }),
  card(104, { property_type: 'studio', rooms: null, size_m2: 40, price_amount: 1400, price_monthly: 1400, area: 'داون‌تاون · Union', first_seen: ago(40), last_seen: ago(30), thumb: PIXEL(140) }),
  card(105, { property_type: 'room', rooms: null, size_m2: 12, price_amount: 750, price_monthly: 750, area: 'وان · Jane و Rutherford', first_seen: ago(40), last_seen: ago(6), thumb: PIXEL(180) }),
  card(106, { size_m2: 60, price_amount: 1950, price_monthly: 1950, area: 'نورث یورک · ایستگاه Finch', first_seen: ago(40), last_seen: ago(30), thumb: PIXEL(220) }),
]

export const PRICE_KINDS: Card[] = [
  card(201, { property_type: 'studio', rooms: null, size_m2: 30, price_amount: 90, price_period: 'night', price_monthly: 2700, rental_mode: 'short_term', area: 'داون‌تاون · Union', last_seen: ago(5), first_seen: ago(40) }),
  card(202, { property_type: 'room', rooms: null, size_m2: 15, price_amount: 450, price_period: 'week', price_monthly: 1950, area: 'نورث یورک · Sheppard', last_seen: ago(30), first_seen: ago(40) }),
  card(203, { rooms: 2, size_m2: 75, price_amount: 2100, price_monthly: 2100, area: 'اسکاربرو · Kennedy', lister_type: 'agent', last_seen: ago(30), first_seen: ago(40) }),
  card(204, { property_type: 'basement', rooms: null, size_m2: 60, price_amount: null, price_monthly: null, area: 'اسکاربرو · Kennedy', last_seen: ago(4), first_seen: ago(40), photo_count: 0 }),
]

export const WANTED: Card[] = [
  card(301, { kind: 'wanted', property_type: 'room', rooms: null, size_m2: null, price_amount: null, price_monthly: null, price_max: 1600, area: 'نورث یورک، ریچموند هیل', first_seen: ago(2), last_seen: ago(2), photo_count: 0 }),
  card(302, { kind: 'wanted', rooms: 2, size_m2: null, price_amount: null, price_monthly: null, price_max: 2200, roommate_gender: 'couple', area: 'داون‌تاون', first_seen: ago(5), last_seen: ago(5), photo_count: 0 }),
]

const BERLIN: Card[] = [
  card(401, { country: 'DE', region: '', city: 'Berlin', metro: 'Berlin', metro_slug: 'berlin', property_type: 'room', rooms: null, size_m2: 14, price_amount: 680, price_currency: 'EUR', price_monthly: 680, price_basis: 'warm', area: 'نویکلن · U Hermannplatz', rental_mode: 'sublet', available_from: '2026-11-15', available_to: '2027-01-31' }),
  card(402, { country: 'DE', region: '', city: 'Berlin', metro: 'Berlin', metro_slug: 'berlin', rooms: 2, size_m2: 52, price_amount: 1150, price_currency: 'EUR', price_monthly: 1150, price_basis: 'cold', area: 'ودینگ · U Leopoldplatz', first_seen: ago(40), last_seen: ago(30) }),
  card(403, { country: 'DE', region: '', city: 'Berlin', metro: 'Berlin', metro_slug: 'berlin', rooms: 1, size_m2: 35, price_amount: 950, price_currency: 'EUR', price_monthly: 950, area: 'پانکو · U Vinetastraße', first_seen: ago(40), last_seen: ago(30) }),
]

const MONTREAL: Card[] = [
  card(501, { region: 'QC', city: 'Montreal', metro: 'Montreal', metro_slug: 'montreal', size_m2: 60, price_amount: 1450, price_monthly: 1450, area: 'پلاتو · Mont-Royal و Saint-Denis', first_seen: ago(5), last_seen: ago(5) }),
  card(502, { region: 'QC', city: 'Montreal', metro: 'Montreal', metro_slug: 'montreal', property_type: 'room', rooms: null, size_m2: 12, price_amount: 850, price_monthly: 850, area: 'کوت‌دنژ · Côte-des-Neiges', first_seen: ago(40), last_seen: ago(30) }),
]

const COLOGNE: Card[] = [
  ...Array.from({ length: 18 }, (_, i) => card(600 + i, { country: 'DE', region: '', city: 'Köln', metro: 'Cologne', metro_slug: 'cologne', price_currency: 'EUR', area: 'کلن' })),
  ...Array.from({ length: 24 }, (_, i) => card(620 + i, { country: 'DE', region: '', city: 'Düsseldorf', metro: 'Cologne', metro_slug: 'cologne', price_currency: 'EUR', area: 'دوسلدورف' })),
  ...Array.from({ length: 6 }, (_, i) => card(650 + i, { country: 'DE', region: '', city: 'Bonn', metro: 'Cologne', metro_slug: 'cologne', price_currency: 'EUR', area: 'بن' })),
]

export const SNAPSHOT_CARDS: Record<string, Card[]> = {
  toronto: [...TORONTO, ...PRICE_KINDS, ...WANTED],
  montreal: MONTREAL,
  vancouver: [],
  berlin: BERLIN,
  hamburg: [],
  cologne: COLOGNE,
}

export const VERSION = 'v1'

export function snapshot(slug: string, generatedAt = ago(1)): SnapshotFile {
  const row = CITIES.countries.flatMap((c) => c.cities.map((city) => ({ city, country: c.code }))).find(({ city }) => city.slug === slug)
  return { schema_version: 1, country: row?.country ?? 'CA', city: row?.city.name ?? slug, city_slug: slug, version: VERSION, generated_at: generatedAt, listings: SNAPSHOT_CARDS[slug] ?? [] }
}

export function manifest(generatedAt = ago(1)): Manifest {
  return {
    schema_version: 1,
    generated_at: generatedAt,
    alerts_on: false,
    maintenance: { on: false, until: null },
    cities: CITIES.countries.flatMap((c) =>
      c.cities.map((city) => ({
        country: c.code,
        city: city.name,
        city_slug: city.slug,
        version: VERSION,
        url: `/feeds/${city.slug}.${VERSION}.json`,
        listing_count: (SNAPSHOT_CARDS[city.slug] ?? []).length,
        bytes: 1000,
      })),
    ),
  }
}

export const LISTING_DESC = 'آپارتمان ۱ خوابه‌ی مبله نزدیک Yonge و Finch، ۵ دقیقه پیاده تا مترو. قبوض و اینترنت داخل قیمت. از ۱ دسامبر، حداقل ۱۲ ماه. مناسب زوج یا یک نفر.'

export function listing(from: Card, patch: Partial<Listing> = {}): Listing {
  return {
    ...from,
    state: 'active',
    closed_reason: null,
    can_confirm: false,
    poster_count: null,
    deposit_amount: null,
    deposit_months: null,
    min_term_months: from.rental_mode === 'long_term' ? 12 : null,
    occupants_wanted: null,
    wbs_required: false,
    summary_fa: LISTING_DESC,
    summary_en: '',
    last_confirmed_at: null,
    photos: Array.from({ length: from.photo_count }, (_, i) => ({ url: PIXEL(20 + i * 30), thumb: PIXEL(20 + i * 30) })),
    has_telegram: true,
    has_phone: true,
    has_source: true,
    channel: from.metro === 'Toronto' ? TORONTO_CHANNEL : CANADA_CHANNEL,
    ...patch,
  }
}

export const CONTACT: Contact = {
  telegram: 'username',
  telegram_url: 'https://t.me/username',
  phone: '+1 416 555 0100',
  source_url: 'https://t.me/source_group/123',
  has_any: true,
  gate: 'member',
  membership_checked: true,
  reveals_left: 29,
}

export const EXCHANGE: Exchange = {
  access_token: 'mock-jwt',
  expires_in: 3600,
  user: { id: 1, telegram_user_id: 770001, first_name: 'Test', telegram_username: 'test' },
  prefs: { country: '', city: '', city_active: true, locale: 'fa', theme: 'auto', notify_enabled: true, notify_alerts: true, notify_saved: true, notify_news: true, quiet_from: null, quiet_to: null, first_src: '' },
  miniapp_v2: true,
}

export const DEEPLINK_LANDING: DeepLink = {
  kind: 'landing',
  malformed: false,
  country: '',
  city: '',
  city_slug: '',
  city_known: false,
  listing_id: null,
  listing_state: null,
  filters: { types: [], min: null, max: null },
  src: '',
  legacy_path: '',
}
