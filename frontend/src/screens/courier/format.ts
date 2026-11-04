import { fa, formatDate, relativeTime } from '@/lib/format'
import type { Place, RequestCard } from './types'

export type Side = 'DEMAND' | 'SUPPLY'
export type SizeCode = 'doc' | '1' | '3' | '3p'

const KG = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 1 })
const MONTH = new Intl.DateTimeFormat('fa-IR-u-ca-gregory', { month: 'long', timeZone: 'UTC' })

export const kg = (value: string | number) => `${KG.format(Number(value))} کیلو`

// The form offers sizes and stores one weight for each (courier/texts.py SIZES), so a person who chose «۳ کیلو +» must not
// read «۱۰ کیلو». A weight from the old form or an import is exact, and stays as it is.
const PACKAGE_SIZE: Record<string, string> = { '0.5': 'مدرک', '1': 'تا ۱ کیلو', '3': '۱ تا ۳ کیلو', '5': '۳ کیلو +' }
const TRIP_SIZE: Record<string, string> = { '0.5': 'فقط مدرک', '1': 'تا ۱ کیلو', '3': '۱ تا ۳ کیلو', '10': '۳ کیلو +' }

export const packageSize = (value: string | number) => PACKAGE_SIZE[String(Number(value))] ?? kg(value)
export const tripSpace = (value: string | number) => TRIP_SIZE[String(Number(value))] ?? kg(value)

export const placeName = (place: Pick<Place, 'name_fa'>) => place.name_fa

export const placeKey = (place: Pick<Place, 'country' | 'city'>) => `${place.country}:${place.city}`

// For telemetry: only a well-formed key leaves, as its two parts. Anything else, as from a hand-edited link, reads as none.
export function placeProps(key: string) {
  const [country, city] = /^[A-Za-z]{2}:[\w-]+$/.test(key) ? key.split(':') : [null, null]
  return { country, city }
}

export function routeProps(from: string, to: string) {
  const origin = placeProps(from)
  const destination = placeProps(to)
  return { from_country: origin.country, from_city: origin.city, to_country: destination.country, to_city: destination.city }
}

// Latin city names stay in reading order inside Persian: the card wraps them in <bdi>.
export function stopsLabel(stops: Pick<Place, 'name_fa'>[]) {
  const names = stops.map(placeName)
  if (names.length < 2) return names[0] ?? ''
  return `${names.slice(0, -1).join('، ')} و ${names[names.length - 1]}`
}

export const routeLabel = (card: Pick<RequestCard, 'origin' | 'stops'>) => `${placeName(card.origin)} ← ${stopsLabel(card.stops)}`

export const isTrip = (card: Pick<RequestCard, 'type'>) => card.type === 'SUPPLY'

export function dateLabel(card: Pick<RequestCard, 'type' | 'date' | 'date_kind'>) {
  if (!card.date) return isTrip(card) ? 'تاریخ پرواز نامشخص' : 'بدون عجله'
  return card.date_kind === 'by' ? `تا ${formatDate(card.date)}` : formatDate(card.date)
}

export function sizeLabel(card: Pick<RequestCard, 'type' | 'capacity_kg' | 'weight_kg'>) {
  if (isTrip(card)) return card.capacity_kg ? `جا: ${tripSpace(card.capacity_kg)}` : 'جا: نامشخص'
  return card.weight_kg ? packageSize(card.weight_kg) : 'وزن نامشخص'
}

export const categoriesLabel = (rows: { name_fa: string }[]) => rows.map((row) => row.name_fa).join('، ')

export const trustLabel = (count: number) => `${fa(count)} ارسال موفق`

export function ageLabel(iso: string) {
  const age = relativeTime(iso)
  return age === 'دیروز' || age === 'همین الان' ? `${age === 'دیروز' ? 'دیروز' : 'امروز'} ثبت شد` : age
}

export const nounFor = (side: Side) => (side === 'DEMAND' ? 'مسافر' : 'بسته')

// The API speaks in the searcher's side. DEMAND has a package and sees travelers.
export const sideOfCard = (card: Pick<RequestCard, 'type'>): Side => (card.type === 'SUPPLY' ? 'DEMAND' : 'SUPPLY')

export const SIZES: Record<Side, Array<{ code: SizeCode; label: string }>> = {
  DEMAND: [
    { code: 'doc', label: 'مدرک / نامه' },
    { code: '1', label: 'تا ۱ کیلو' },
    { code: '3', label: '۱ تا ۳ کیلو' },
    { code: '3p', label: '۳ کیلو +' },
  ],
  SUPPLY: [
    { code: 'doc', label: 'فقط مدرک' },
    { code: '1', label: 'تا ۱ کیلو' },
    { code: '3', label: '۱ تا ۳ کیلو' },
    { code: '3p', label: '۳ کیلو +' },
  ],
}

export const sizeName = (side: Side, code: string) => SIZES[side].find((row) => row.code === code)?.label ?? ''

// The first key is this month, which has only its remaining days: «تا آخر نوامبر».
export function monthLabel(key: string, first: boolean) {
  const name = MONTH.format(new Date(`${key}-01T00:00:00Z`))
  return first ? `تا آخر ${name}` : name
}

// The three month keys the API offers, from today. Also what a bad month in the URL is checked against.
export function upcomingMonths(now = new Date()) {
  return [0, 1, 2].map((step) => {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + step, 1))
    return `${day.getUTCFullYear()}-${String(day.getUTCMonth() + 1).padStart(2, '0')}`
  })
}
