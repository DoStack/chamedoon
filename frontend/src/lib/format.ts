import type { Card, Listing } from '@/api/types'
import { getLocale, t } from './locale'

const TAG = { fa: 'fa-IR', en: 'en-CA', de: 'de-DE' } as const
const DATE_TAG = { fa: 'fa-IR-u-ca-gregory', en: 'en-CA', de: 'de-DE' } as const
const NUMBER = { fa: new Intl.NumberFormat(TAG.fa, { maximumFractionDigits: 0 }), en: new Intl.NumberFormat(TAG.en, { maximumFractionDigits: 0 }), de: new Intl.NumberFormat(TAG.de, { maximumFractionDigits: 0 }) }
const DATE = (['fa', 'en', 'de'] as const).reduce((all, tag) => ({ ...all, [tag]: new Intl.DateTimeFormat(DATE_TAG[tag], { day: 'numeric', month: 'long', timeZone: 'UTC' }) }), {} as Record<'fa' | 'en' | 'de', Intl.DateTimeFormat>)
const CURRENCY: Record<string, string> = { CAD: 'CA$', EUR: '€', USD: 'US$' }

export function fa(value: number) {
  return NUMBER[getLocale()].format(value)
}

const ROOMS = { fa: new Intl.NumberFormat(TAG.fa, { maximumFractionDigits: 1 }), en: new Intl.NumberFormat(TAG.en, { maximumFractionDigits: 1 }), de: new Intl.NumberFormat(TAG.de, { maximumFractionDigits: 1 }) }

// A listing can have half rooms: 2.5 stays «۲٫۵», it is not rounded up.
export function faRooms(value: number) {
  return ROOMS[getLocale()].format(value)
}

export function currency(code: string) {
  return CURRENCY[code] ?? code
}

export function formatDate(iso: string) {
  const date = new Date(`${iso.slice(0, 10)}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? '' : DATE[getLocale()].format(date)
}

export function formatDuration(minutes: number) {
  return minutes >= 60 ? `${fa(Math.round(minutes / 60))} ساعت` : `${fa(minutes)} دقیقه`
}

const HOUR = 3_600_000
const DAY = 24 * HOUR

export function relativeTime(iso: string, now = Date.now()) {
  const age = now - new Date(iso).getTime()
  if (age < HOUR) return t('همین الان')
  if (age < DAY) return t('{n} ساعت پیش', { n: fa(Math.floor(age / HOUR)) })
  if (age < 2 * DAY) return t('دیروز')
  return t('{n} روز پیش', { n: fa(Math.floor(age / DAY)) })
}

export function isFresh(card: Pick<Card, 'first_seen'>, now = Date.now()) {
  return now - new Date(card.first_seen).getTime() < DAY
}

const PERIOD: Record<Card['price_period'], string> = { month: 'در ماه', week: 'در هفته', night: 'هر شب' }
const BASIS: Partial<Record<Card['price_basis'], string>> = { warm: 'گرم', cold: 'سرد' }

// «در ماه · گرم». Basis only when the listing states it.
// Warm and cold rent are German terms. A reader of a Canadian post may still guess one, so only German cards show it.
export function priceTail(card: Pick<Card, 'price_period' | 'price_basis' | 'country'>) {
  const basis = card.country === 'DE' ? BASIS[card.price_basis] : undefined
  const period = t(PERIOD[card.price_period])
  return basis ? `${period} · ${t(basis)}` : period
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
  return t(TYPE[card.property_type])
}

// German listings count Zimmer, not bedrooms, so the same number reads «۲ اتاقه» there.
export function roomsLabel(card: Pick<Card, 'property_type' | 'rooms' | 'country'>) {
  if (card.property_type !== 'apartment' && card.property_type !== 'house') return typeLabel(card)
  if (card.rooms == null) return typeLabel(card)
  const locale = getLocale()
  if (locale === 'en') return `${faRooms(card.rooms)}-bedroom ${typeLabel(card).toLowerCase()}`
  if (locale === 'de') return `${faRooms(card.rooms)}-Zimmer-${typeLabel(card)}`
  return `${typeLabel(card)} ${faRooms(card.rooms)} ${card.country === 'DE' ? 'اتاقه' : 'خوابه'}`
}

export function cardTitle(card: Pick<Card, 'property_type' | 'rooms' | 'country' | 'size_m2'>) {
  const size = card.size_m2 != null ? ` · ${t('{n} متر', { n: fa(card.size_m2) })}` : ''
  return `${roomsLabel(card)}${size}`
}

// F22 rules: listing text is not translated. EN takes the English summary when there is one; DE stays Persian.
export function listingText(listing: Pick<Listing, 'summary_fa' | 'summary_en'>) {
  return getLocale() === 'en' && listing.summary_en ? listing.summary_en : listing.summary_fa
}

// A city in the app's language: Persian names for Persian, the city's own name otherwise.
export function cityLabel(city: { name_fa: string; city: string }) {
  return getLocale() === 'fa' ? city.name_fa : city.city
}

// Cards carry the city's English name and its Persian one. A card from an older file has only the launch cities', from /public/cities/.
const CITY_FA = new Map<string, string>()

export function rememberCityNames(rows: Array<{ name: string; name_fa: string }>) {
  for (const row of rows) CITY_FA.set(row.name.toLowerCase(), row.name_fa)
}

export function cardPlace(card: Pick<Card, 'area' | 'city'> & { city_fa?: string }) {
  if (card.area) return card.area
  return (getLocale() === 'fa' && (card.city_fa || CITY_FA.get(card.city.toLowerCase()))) || card.city
}

// One address, for the one line under a detail's title. An area can hold «Persian · original»: the Persian part if there is one, else what it says.
export function placeLine(card: Pick<Card, 'area' | 'city'>) {
  const parts = cardPlace(card).split(' · ').map((part) => part.trim()).filter(Boolean)
  return parts.find((part) => /\p{Script=Arabic}/u.test(part)) ?? parts[0] ?? ''
}

// A run of Latin letters, digits and brackets with at least one letter. In RTL a run that opens with a number, like «5000 Yonge St»,
// or holds brackets, like «North York (Yonge between Steeles and Finch)», reads scrambled unless it is isolated.
const LATIN_RUN = /[(\d\p{Script=Latin}\u06F0-\u06F9](?:[()\d\p{Script=Latin}\u06F0-\u06F9 .,'’#/&:+-]*[)\d\p{Script=Latin}\u06F0-\u06F9.])?/gu

export function placeRuns(text: string): Array<{ text: string; ltr: boolean }> {
  const runs: Array<{ text: string; ltr: boolean }> = []
  let cursor = 0
  for (const match of text.matchAll(LATIN_RUN)) {
    if (!/\p{Script=Latin}/u.test(match[0])) continue
    const start = match.index ?? 0
    if (start > cursor) runs.push({ text: text.slice(cursor, start), ltr: false })
    runs.push({ text: match[0], ltr: true })
    cursor = start + match[0].length
  }
  if (cursor < text.length) runs.push({ text: text.slice(cursor), ltr: false })
  return runs
}

const MODE: Partial<Record<Card['rental_mode'], { default: string; de?: string }>> = {
  sublet: { default: 'سابلت', de: 'موقت (Zwischenmiete)' },
  lease_transfer: { default: 'لیز ترنسفر', de: 'Nachmieter' },
  short_term: { default: 'کوتاه‌مدت' },
}

export function modeLabel(card: Pick<Card, 'rental_mode' | 'country'>) {
  const mode = MODE[card.rental_mode]
  if (!mode) return ''
  return card.country === 'DE' && mode.de ? mode.de : mode.default
}

// The badge on a card: «سابلت · ۱ دسامبر تا ۲۸ فوریه». Long-term is the default, so it has no badge.
export function modeBadge(card: Pick<Card, 'rental_mode' | 'country' | 'available_from' | 'available_to'>) {
  const label = modeLabel(card) && t(modeLabel(card))
  if (!label) return ''
  if (card.rental_mode === 'sublet' && card.available_from && card.available_to) {
    return `${label} · ${formatDate(card.available_from)} تا ${formatDate(card.available_to)}`
  }
  return label
}

// When it can be moved into, for the top of a detail. A start already passed says nothing, so it reads as no date.
export function availabilityLine(card: Pick<Card, 'available_from' | 'available_to'>, now = Date.now()) {
  const from = card.available_from
  const to = card.available_to
  if (from && to) return t('{from} تا {to}', { from: formatDate(from), to: formatDate(to) })
  if (from && new Date(from).getTime() > now) return t('از {date}', { date: formatDate(from) })
  return ''
}

const GENDER: Partial<Record<Listing['roommate_gender'], string>> = { couple: 'زوج', female: 'خانم', male: 'آقا' }

export function genderLabel(card: Pick<Card, 'roommate_gender'>) {
  return GENDER[card.roommate_gender] ?? ''
}

export function countryName(code: string) {
  return code === 'DE' ? 'آلمان' : code === 'CA' ? 'کانادا' : code
}
