import type { Card, Listing } from '@/api/types'

const NUMBER = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 0 })
const DATE = new Intl.DateTimeFormat('fa-IR-u-ca-gregory', { day: 'numeric', month: 'long', timeZone: 'UTC' })
const CURRENCY: Record<string, string> = { CAD: 'CA$', EUR: '€', USD: 'US$' }

export function fa(value: number) {
  return NUMBER.format(value)
}

export function currency(code: string) {
  return CURRENCY[code] ?? code
}

export function formatDate(iso: string) {
  return DATE.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`))
}

export function formatDuration(minutes: number) {
  return minutes >= 60 ? `${fa(Math.round(minutes / 60))} ساعت` : `${fa(minutes)} دقیقه`
}

const HOUR = 3_600_000
const DAY = 24 * HOUR

export function relativeTime(iso: string, now = Date.now()) {
  const age = now - new Date(iso).getTime()
  if (age < HOUR) return 'همین الان'
  if (age < DAY) return `${fa(Math.floor(age / HOUR))} ساعت پیش`
  if (age < 2 * DAY) return 'دیروز'
  return `${fa(Math.floor(age / DAY))} روز پیش`
}

export function isFresh(card: Pick<Card, 'first_seen'>, now = Date.now()) {
  return now - new Date(card.first_seen).getTime() < DAY
}

const PERIOD: Record<Card['price_period'], string> = { month: 'در ماه', week: 'در هفته', night: 'هر شب' }
const BASIS: Partial<Record<Card['price_basis'], string>> = { warm: 'گرم', cold: 'سرد' }

// «در ماه · گرم». Basis only when the listing states it.
export function priceTail(card: Pick<Card, 'price_period' | 'price_basis'>) {
  const basis = BASIS[card.price_basis]
  return basis ? `${PERIOD[card.price_period]} · ${basis}` : PERIOD[card.price_period]
}

const TYPE: Record<Card['property_type'], string> = {
  room: 'اتاق',
  studio: 'استودیو',
  apartment: 'آپارتمان',
  house: 'خانه',
  basement: 'بیسمنت',
  other: 'خانه',
}

export function typeLabel(card: Pick<Card, 'property_type'>) {
  return TYPE[card.property_type]
}

// German listings count Zimmer, not bedrooms, so the same number reads «۲ اتاقه» there.
export function roomsLabel(card: Pick<Card, 'property_type' | 'rooms' | 'country'>) {
  if (card.property_type !== 'apartment' && card.property_type !== 'house') return typeLabel(card)
  if (card.rooms == null) return typeLabel(card)
  return `${typeLabel(card)} ${fa(card.rooms)} ${card.country === 'DE' ? 'اتاقه' : 'خوابه'}`
}

export function cardTitle(card: Pick<Card, 'property_type' | 'rooms' | 'country' | 'size_m2'>) {
  const size = card.size_m2 != null ? ` · ${fa(card.size_m2)} متر` : ''
  return `${roomsLabel(card)}${size}`
}

export function cardPlace(card: Pick<Card, 'area' | 'city'>) {
  return card.area || card.city
}

const MODE: Partial<Record<Card['rental_mode'], { default: string; de?: string }>> = {
  sublet: { default: 'سابلت', de: 'موقت (Zwischenmiete)' },
  lease_transfer: { default: 'واگذاری قرارداد', de: 'Nachmieter' },
  short_term: { default: 'کوتاه‌مدت' },
}

export function modeLabel(card: Pick<Card, 'rental_mode' | 'country'>) {
  const mode = MODE[card.rental_mode]
  if (!mode) return ''
  return card.country === 'DE' && mode.de ? mode.de : mode.default
}

// The badge on a card: «سابلت · ۱ دسامبر تا ۲۸ فوریه». Long-term is the default, so it has no badge.
export function modeBadge(card: Pick<Card, 'rental_mode' | 'country' | 'available_from' | 'available_to'>) {
  const label = modeLabel(card)
  if (!label) return ''
  if (card.rental_mode === 'sublet' && card.available_from && card.available_to) {
    return `${label} · ${formatDate(card.available_from)} تا ${formatDate(card.available_to)}`
  }
  return label
}

const GENDER: Partial<Record<Listing['roommate_gender'], string>> = { couple: 'زوج', female: 'خانم', male: 'آقا' }

export function genderLabel(card: Pick<Card, 'roommate_gender'>) {
  return GENDER[card.roommate_gender] ?? ''
}

export function countryName(code: string) {
  return code === 'DE' ? 'آلمان' : code === 'CA' ? 'کانادا' : code
}

// Only regions that have a rule (P1-11). Same text as home.publish.REGION_WARNING_FA where the boards use it.
export const REGION_RULES: Record<string, string> = {
  ON: 'در انتاریو فقط ودیعه‌ی ماه آخر مجاز است.',
  QC: 'در کبک مالک نمی‌تواند ودیعه بگیرد.',
}
