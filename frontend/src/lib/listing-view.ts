import type { Card } from '@/api/types'
import { cardTitle, fa, formatDate } from './format'

export const SORTS = ['new', 'value', 'cheap', 'dear', 'date'] as const
export type Sort = (typeof SORTS)[number]

export type View = {
  kind: 'offer' | 'wanted'
  types: string[]
  rooms: number | null // 3 means three or more
  min: number | null
  max: number | null
  unpriced: boolean
  areas: string[]
  parts: string[]
  q: string
  sort: Sort
  mode: string | null
  furnished: boolean
  bills: boolean
  owner: boolean
  warm: boolean
  reg: boolean
  gender: string | null
  from: string | null
  window: number
}

const DEFAULT_WINDOW = 7

function toNumber(value: string | null) {
  if (!value) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function readView(params: URLSearchParams): View {
  const sort = params.get('sort') as Sort | null
  const from = params.get('from')
  return {
    kind: params.get('kind') === 'wanted' ? 'wanted' : 'offer',
    types: params.getAll('types'),
    rooms: toNumber(params.get('rooms')),
    min: toNumber(params.get('min')),
    max: toNumber(params.get('max')),
    unpriced: params.get('unpriced') !== '0',
    areas: params.getAll('areas'),
    parts: params.getAll('parts'),
    q: params.get('q') ?? '',
    sort: sort && SORTS.includes(sort) ? sort : 'new',
    mode: params.get('mode'),
    furnished: params.has('furnished'),
    bills: params.has('bills'),
    owner: params.has('owner'),
    warm: params.has('warm'),
    reg: params.has('reg'),
    gender: params.get('gender'),
    from: from && /^\d{4}-\d{2}-\d{2}$/.test(from) ? from : null,
    window: toNumber(params.get('window')) ?? DEFAULT_WINDOW,
  }
}

const FILTER_KEYS = ['kind', 'types', 'rooms', 'min', 'max', 'unpriced', 'areas', 'parts', 'q', 'mode', 'furnished', 'bills', 'owner', 'warm', 'reg', 'gender', 'from', 'window']

// Everything else on the URL (sort, view, city) stays as it is.
export function writeView(params: URLSearchParams, view: View) {
  const next = new URLSearchParams(params)
  FILTER_KEYS.forEach((key) => next.delete(key))
  if (view.kind === 'wanted') next.set('kind', 'wanted')
  view.types.forEach((type) => next.append('types', type))
  view.areas.forEach((area) => next.append('areas', area))
  view.parts.forEach((part) => next.append('parts', part))
  if (view.rooms != null) next.set('rooms', String(view.rooms))
  if (view.min != null) next.set('min', String(view.min))
  if (view.max != null) next.set('max', String(view.max))
  if (!view.unpriced) next.set('unpriced', '0')
  if (view.q) next.set('q', view.q)
  if (view.mode) next.set('mode', view.mode)
  for (const flag of ['furnished', 'bills', 'owner', 'warm', 'reg'] as const) if (view[flag]) next.set(flag, '1')
  if (view.gender) next.set('gender', view.gender)
  if (view.from) {
    next.set('from', view.from)
    if (view.window !== DEFAULT_WINDOW) next.set('window', String(view.window))
  }
  return next
}

export function clearFilters(view: View): View {
  return { ...readView(new URLSearchParams()), sort: view.from ? 'new' : view.sort }
}

const DIGITS = '۰۱۲۳۴۵۶۷۸۹'

function normalize(text: string) {
  return text.toLowerCase().replace(/[۰-۹]/g, (digit) => String(DIGITS.indexOf(digit)))
}

// A neighbourhood is what comes before the street: «نورث یورک · Yonge و Finch». A wanted post can name several.
export function areaNames(card: Pick<Card, 'area'>) {
  return card.area
    .split(' · ')[0]
    .split(/[،,]/)
    .map((name) => name.trim())
    .filter(Boolean)
}

// By this kind's counts, and fixed while the sheet is open, so a chip never moves under a finger.
export function areasByCount(cards: Card[], kind: View['kind']) {
  const totals = new Map<string, number>()
  for (const card of cards) if (card.kind === kind) for (const name of areaNames(card)) totals.set(name, (totals.get(name) ?? 0) + 1)
  return [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name)
}

// Text only until P5 (audit P1-50): it searches what a card shows.
function matchesText(card: Card, q: string) {
  const text = normalize(`${cardTitle(card)} ${card.area} ${card.city}`)
  return normalize(q).split(/\s+/).filter(Boolean).every((word) => text.includes(word))
}

const DAY = 86_400_000

// The same rules as home.search.search_q where the snapshot carries the field. Budget compares the monthly value, never
// price_amount (P0-13).
export function matches(card: Card, view: View) {
  if (card.kind !== view.kind) return false
  if (view.types.length && !view.types.includes(card.property_type)) return false
  if (view.rooms != null) {
    if (card.rooms == null) return false
    if (view.rooms >= 3 ? card.rooms < 3 : card.rooms !== view.rooms) return false
  }
  const price = view.kind === 'wanted' ? card.price_max : card.price_monthly
  if (price == null && !view.unpriced) return false
  if (price != null && view.min != null && price < view.min) return false
  if (price != null && view.max != null && price > view.max) return false
  if (view.areas.length && !areaNames(card).some((name) => view.areas.includes(name))) return false
  if (view.parts.length && !view.parts.includes(card.city)) return false
  if (view.q && !matchesText(card, view.q)) return false
  if (view.mode && card.rental_mode !== view.mode) return false
  if (view.furnished && card.furnished !== 'yes' && card.furnished !== 'partial') return false
  if (view.bills && card.bills_included !== 'yes' && card.price_basis !== 'warm') return false
  if (view.owner && card.lister_type === 'agent') return false
  if (view.warm && card.price_basis === 'cold') return false
  if (view.reg && card.registration !== 'yes') return false
  if (view.gender === 'female' || view.gender === 'male') {
    if (card.roommate_gender !== view.gender) return false
  } else if (view.gender === 'couple') {
    // A whole place not limited to one gender counts too. The snapshot lacks `occupants_wanted` and `couples_ok`.
    const whole = card.property_type !== 'room' && card.roommate_gender !== 'female' && card.roommate_gender !== 'male'
    if (card.roommate_gender !== 'couple' && !whole) return false
  }
  if (view.from) {
    if (!card.available_from) return false
    const gap = Math.abs(new Date(card.available_from).getTime() - new Date(view.from).getTime()) / DAY
    if (gap > view.window) return false
  }
  return true
}

export function applyView(cards: Card[], view: View) {
  return sortCards(
    cards.filter((card) => matches(card, view)),
    view.sort,
  )
}

export function countMatches(cards: Card[], view: View) {
  return cards.reduce((sum, card) => sum + (matches(card, view) ? 1 : 0), 0)
}

export type Tile = { id: string; label: string; icon: 'all' | 'room' | 'studio' | 'base' | 'one' | 'two' | 'three' | 'house' | 'apartment' | 'wanted'; patch: Pick<View, 'kind' | 'types' | 'rooms'> }

const tile = (id: string, label: string, icon: Tile['icon'], types: string[], rooms: number | null = null): Tile => ({ id, label, icon, patch: { kind: 'offer', types, rooms } })

// The options of each market (P1-07, P1-10). A Quebec «۴½» has no rooms, so Quebec has one apartment tile.
export function typeTiles(country: string, region: string): Tile[] {
  const all = tile('all', 'همه', 'all', [])
  const wanted: Tile = { id: 'wanted', label: 'دنبال خانه', icon: 'wanted', patch: { kind: 'wanted', types: [], rooms: null } }
  if (country === 'DE') {
    return [all, tile('room', 'اتاق WG', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('1', '۱ اتاقه', 'one', ['apartment'], 1), tile('2', '۲ اتاقه', 'two', ['apartment'], 2), tile('3', '۳ اتاقه+', 'three', ['apartment'], 3), tile('house', 'خانه', 'house', ['house']), wanted]
  }
  if (region === 'QC') {
    return [all, tile('room', 'اتاق', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('base', 'بیسمنت', 'base', ['basement']), tile('apartment', 'آپارتمان', 'apartment', ['apartment']), tile('house', 'خانه‌ی کامل', 'house', ['house']), wanted]
  }
  return [all, tile('room', 'اتاق', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('base', 'بیسمنت', 'base', ['basement']), tile('1', '۱ خوابه', 'one', ['apartment'], 1), tile('2', '۲ خوابه', 'two', ['apartment'], 2), tile('3', '۳ خوابه+', 'three', ['apartment'], 3), tile('house', 'خانه‌ی کامل', 'house', ['house']), wanted]
}

export function tileOf(tiles: Tile[], view: View) {
  return tiles.find((item) => item.patch.kind === view.kind && item.patch.rooms === view.rooms && item.patch.types.join() === view.types.join())
}

export const MODES: Record<string, Array<{ id: string; label: string }>> = {
  CA: [
    { id: 'long_term', label: 'بلندمدت' },
    { id: 'sublet', label: 'سابلت' },
    { id: 'lease_transfer', label: 'انتقال لیز' },
    { id: 'short_term', label: 'کوتاه‌مدت' },
  ],
  DE: [
    { id: 'long_term', label: 'بلندمدت' },
    { id: 'sublet', label: 'موقت (Zwischenmiete)' },
    { id: 'lease_transfer', label: 'Nachmieter' },
  ],
}

export const GENDERS = [
  { id: null, label: 'فرقی نمی‌کند' },
  { id: 'female', label: 'فقط خانم' },
  { id: 'male', label: 'فقط آقا' },
  { id: 'couple', label: 'مناسب زوج' },
] as const

export const WINDOWS = [
  { days: 0, label: 'دقیق' },
  { days: 7, label: '± ۷ روز' },
  { days: 14, label: '± ۱۴ روز' },
  { days: 30, label: '± ۳۰ روز' },
] as const

export function budgetLabel(view: Pick<View, 'min' | 'max'>) {
  if (view.min != null && view.max != null) return `${fa(view.min)} تا ${fa(view.max)}`
  if (view.max != null) return `تا ${fa(view.max)}`
  if (view.min != null) return `از ${fa(view.min)}`
  return ''
}

export type Chip = { id: string; label: string; clear: (view: View) => View }

// One removable chip per applied filter, labelled as the sheet labels it.
export function appliedChips(view: View, tiles: Tile[], country: string): Chip[] {
  const chips: Chip[] = []
  const known = tileOf(tiles, view)
  if (view.kind === 'wanted') chips.push({ id: 'kind', label: 'دنبال خانه', clear: (v) => ({ ...v, kind: 'offer' }) })
  if (view.kind === 'offer' && known && known.id !== 'all') {
    chips.push({ id: 'type', label: known.label, clear: (v) => ({ ...v, types: [], rooms: null }) })
  } else if (view.kind === 'offer' && !known) {
    // A link can name a type the tiles do not, such as an apartment in Toronto.
    for (const type of view.types) chips.push({ id: `type-${type}`, label: tiles.find((item) => item.patch.types.join() === type)?.label ?? 'آپارتمان', clear: (v) => ({ ...v, types: v.types.filter((item) => item !== type), rooms: null }) })
  }
  const budget = budgetLabel(view)
  if (budget) chips.push({ id: 'budget', label: budget, clear: (v) => ({ ...v, min: null, max: null }) })
  for (const area of view.areas) chips.push({ id: `area-${area}`, label: area, clear: (v) => ({ ...v, areas: v.areas.filter((item) => item !== area) }) })
  if (view.mode) chips.push({ id: 'mode', label: (MODES[country] ?? MODES.CA).find((item) => item.id === view.mode)?.label ?? view.mode, clear: (v) => ({ ...v, mode: null }) })
  if (view.furnished) chips.push({ id: 'furnished', label: 'مبله', clear: (v) => ({ ...v, furnished: false }) })
  if (view.bills) chips.push({ id: 'bills', label: 'قبوض داخل قیمت', clear: (v) => ({ ...v, bills: false }) })
  if (view.owner) chips.push({ id: 'owner', label: 'فقط مالک', clear: (v) => ({ ...v, owner: false }) })
  if (view.warm) chips.push({ id: 'warm', label: 'اجاره‌ی سرد نباشد', clear: (v) => ({ ...v, warm: false }) })
  if (view.reg) chips.push({ id: 'reg', label: 'ملد', clear: (v) => ({ ...v, reg: false }) })
  const gender = GENDERS.find((item) => item.id === view.gender)
  if (view.gender && gender) chips.push({ id: 'gender', label: gender.label, clear: (v) => ({ ...v, gender: null }) })
  if (view.from) chips.push({ id: 'from', label: `از ${formatDate(view.from)}`, clear: (v) => ({ ...v, from: null, sort: v.sort === 'date' ? 'new' : v.sort }) })
  return chips
}

export type WayOut = { label: string; view: View }

const STEP = 250

// F05#5, #7, F06#4: what one change would bring back, by real count. At most three, and never one with no result.
export function waysOut(cards: Card[], view: View, top: number): WayOut[] {
  const ways: WayOut[] = []
  const add = (label: (n: string) => string, next: View) => {
    const count = countMatches(cards, next)
    if (count > 0) ways.push({ label: label(fa(count)), view: next })
  }
  if (view.max != null) {
    for (let max = view.max + STEP; max <= top + STEP; max += STEP) {
      const next = { ...view, max }
      if (countMatches(cards, next) > 0) {
        add((n) => `تا ${fa(max)}: ${n} آگهی`, next)
        break
      }
    }
  }
  if (view.areas.length) add((n) => `همه‌جا: ${n} آگهی`, { ...view, areas: [] })
  if (view.from && view.window < 14) add((n) => `± ۱۴ روز: ${n} آگهی`, { ...view, window: 14 })
  if (view.furnished) add((n) => `بدون مبله: ${n}`, { ...view, furnished: false })
  if (view.types.length || view.rooms != null) add((n) => `همه‌ی نوع‌ها: ${n} آگهی`, { ...view, types: [], rooms: null })
  return ways.slice(0, 3)
}

function median(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

// «ارزنده‌ترین»: price per m² against the median of the same neighbourhood (decision 2026-10-27).
function valueScores(cards: Card[]) {
  const perArea = new Map<string, number[]>()
  const perMeter = (card: Card) => (card.price_monthly != null && card.size_m2 ? card.price_monthly / card.size_m2 : null)
  for (const card of cards) {
    const value = perMeter(card)
    if (value != null) perArea.set(card.area, [...(perArea.get(card.area) ?? []), value])
  }
  return (card: Card) => {
    const value = perMeter(card)
    return value == null ? null : value / median(perArea.get(card.area)!)
  }
}

// Nulls last in every sort (P1-55). Ties keep the snapshot's order, so a sort never reshuffles equal cards.
export function sortCards(cards: Card[], sort: Sort) {
  const seen = (card: Card) => new Date(card.last_seen).getTime()
  const key: (card: Card) => number | null =
    sort === 'cheap'
      ? (card) => card.price_monthly
      : sort === 'dear'
        ? (card) => (card.price_monthly == null ? null : -card.price_monthly)
        : sort === 'date'
          ? (card) => (card.available_from ? new Date(card.available_from).getTime() : null)
          : sort === 'value'
            ? valueScores(cards)
            : (card) => -seen(card)
  return [...cards].sort((a, b) => {
    const left = key(a)
    const right = key(b)
    if (left == null && right == null) return 0
    if (left == null) return 1
    if (right == null) return -1
    return left - right
  })
}
