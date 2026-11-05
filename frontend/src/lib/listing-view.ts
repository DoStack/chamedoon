import type { Card } from '@/api/types'
import { cardTitle, fa, formatDate } from './format'

export const SORTS = ['new', 'value', 'cheap', 'dear', 'date'] as const
export type Sort = (typeof SORTS)[number]

export type Term = 'lease' | 'sublet' | 'short' | 'lease_transfer'

// Fields of the card in the contract of #56. Optional until it lands; remove this type then.
export type FilterCard = Card & {
  term?: Term
  couples_ok?: boolean
  occupants_wanted?: number | null
  wbs_required?: boolean
  value_ratio?: number | null
  summary_fa?: string
  summary_en?: string
}

export type View = {
  kind: 'offer' | 'wanted'
  types: string[] // property_type values, plus bed1, bed2, bed3
  min: number | null
  max: number | null
  unpriced: boolean
  areas: string[]
  area_ids: number[]
  parts: string[]
  q: string
  sort: Sort
  term: string | null
  furnished: boolean
  bills: boolean
  owner: boolean
  warm: boolean
  reg: boolean
  nowbs: boolean
  gender: string | null
  move: string | null
  window: number
  by: string | null
}

const DEFAULT_WINDOW = 7

function toNumber(value: string | null) {
  if (!value) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const isoDate = (value: string | null) => (value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null)

export function readView(params: URLSearchParams): View {
  const sort = params.get('sort') as Sort | null
  return {
    kind: params.get('kind') === 'wanted' ? 'wanted' : 'offer',
    types: params.getAll('types'),
    min: toNumber(params.get('min')),
    max: toNumber(params.get('max')),
    unpriced: params.get('unpriced') !== '0',
    areas: params.getAll('areas'),
    area_ids: params.getAll('area_ids').map(Number).filter(Number.isFinite),
    parts: params.getAll('parts'),
    q: params.get('q') ?? '',
    sort: sort && SORTS.includes(sort) ? sort : 'new',
    term: params.get('term'),
    furnished: params.has('furnished'),
    bills: params.has('bills'),
    owner: params.has('owner'),
    warm: params.has('warm'),
    reg: params.has('reg'),
    nowbs: params.has('nowbs'),
    gender: params.get('gender'),
    move: isoDate(params.get('move')),
    window: toNumber(params.get('window')) ?? DEFAULT_WINDOW,
    by: isoDate(params.get('by')),
  }
}

export const FILTER_KEYS = ['kind', 'types', 'min', 'max', 'unpriced', 'areas', 'area_ids', 'parts', 'q', 'sort', 'term', 'furnished', 'bills', 'owner', 'warm', 'reg', 'nowbs', 'gender', 'move', 'window', 'by']

// Other keys (view, city) stay as they are. `sort` is on the URL unless it is the default.
export function writeView(params: URLSearchParams, view: View) {
  const next = new URLSearchParams(params)
  FILTER_KEYS.forEach((key) => next.delete(key))
  if (view.kind === 'wanted') next.set('kind', 'wanted')
  view.types.forEach((type) => next.append('types', type))
  view.areas.forEach((area) => next.append('areas', area))
  view.area_ids.forEach((id) => next.append('area_ids', String(id)))
  view.parts.forEach((part) => next.append('parts', part))
  if (view.min != null) next.set('min', String(view.min))
  if (view.max != null) next.set('max', String(view.max))
  if (!view.unpriced) next.set('unpriced', '0')
  if (view.q) next.set('q', view.q)
  if (view.sort !== 'new') next.set('sort', view.sort)
  if (view.term) next.set('term', view.term)
  for (const flag of ['furnished', 'bills', 'owner', 'warm', 'reg', 'nowbs'] as const) if (view[flag]) next.set(flag, '1')
  if (view.gender) next.set('gender', view.gender)
  if (view.move) {
    next.set('move', view.move)
    if (view.window !== DEFAULT_WINDOW) next.set('window', String(view.window))
  }
  if (view.by) next.set('by', view.by)
  return next
}

// The date sort needs a date, so it goes with the filters.
export function clearFilters(view: View): View {
  return { ...readView(new URLSearchParams()), sort: view.sort === 'date' ? 'new' : view.sort }
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

// The vectors' text fields. The title is added for what a card shows in Persian, «۱ خوابه», until P5.
function matchesText(card: FilterCard, q: string) {
  const text = normalize(`${cardTitle(card)} ${card.area} ${card.city} ${card.summary_fa ?? ''} ${card.summary_en ?? ''}`)
  return normalize(q).split(/\s+/).filter(Boolean).every((word) => text.includes(word))
}

export function typeTokens(card: Card) {
  const tokens: string[] = [card.property_type]
  if (card.property_type === 'apartment' && card.rooms != null) {
    const rooms = Math.floor(card.rooms)
    if (rooms >= 3) tokens.push('bed3')
    else if (rooms >= 1) tokens.push(`bed${rooms}`)
  }
  return tokens
}

const TERM_OF_MODE: Partial<Record<Card['rental_mode'], Term>> = { long_term: 'lease', sublet: 'sublet', short_term: 'short', lease_transfer: 'lease_transfer' }

function termOf(card: FilterCard) {
  return card.term ?? TERM_OF_MODE[card.rental_mode] ?? 'lease'
}

const DAY = 86_400_000

function daysFrom(date: string, iso: string) {
  return Math.abs(new Date(date).getTime() - new Date(iso).getTime()) / DAY
}

// The rules of backend/home_api/testdata/filter_vectors.json, which also run in pytest.
export function matches(card: FilterCard, view: View) {
  if (card.kind !== view.kind) return false
  if (view.types.length && !typeTokens(card).some((token) => view.types.includes(token))) return false
  // Monthly value, never price_amount (P0-13). A wanted post has no budget filter.
  if (view.kind === 'offer' && (view.min != null || view.max != null)) {
    const price = card.price_monthly
    if (price == null) {
      if (!view.unpriced) return false
    } else {
      if (view.min != null && price < view.min) return false
      if (view.max != null && price > view.max) return false
    }
  }
  if (view.areas.length) {
    const area = card.area.toLowerCase()
    if (!view.areas.some((name) => area.includes(name.toLowerCase()))) return false
  }
  if (view.area_ids.length && (card.area_id == null || !view.area_ids.includes(card.area_id))) return false
  if (view.parts.length && !view.parts.includes(card.city)) return false
  if (view.q && !matchesText(card, view.q)) return false
  if (view.term && termOf(card) !== view.term) return false
  if (view.furnished && card.furnished !== 'yes' && card.furnished !== 'partial') return false
  if (view.bills && card.bills_included !== 'yes' && card.price_basis !== 'warm') return false
  if (view.owner && card.lister_type === 'agent') return false
  if (view.warm && card.price_basis === 'cold') return false
  if (view.reg && card.registration !== 'yes') return false
  if (view.nowbs && card.wbs_required) return false
  if (view.gender === 'female' || view.gender === 'male') {
    if (card.roommate_gender !== view.gender) return false
  } else if (view.gender === 'couple') {
    const whole = card.property_type !== 'room' && card.roommate_gender !== 'female' && card.roommate_gender !== 'male' && card.occupants_wanted !== 1
    if (card.roommate_gender !== 'couple' && !card.couples_ok && !whole) return false
  }
  if (view.move) {
    if (!card.available_from || daysFrom(card.available_from, view.move) > view.window) return false
  }
  if (view.by && card.available_from && card.available_from > view.by) return false
  return true
}

export function applyView(cards: FilterCard[], view: View) {
  return sortCards(
    cards.filter((card) => matches(card, view)),
    view.sort,
    view.move,
  )
}

export function countMatches(cards: Card[], view: View) {
  return cards.reduce((sum, card) => sum + (matches(card, view) ? 1 : 0), 0)
}

export type Facets = { types: Record<string, number>; term: Record<string, number>; gender: Record<string, number>; areas: Record<string, number>; area_ids: Record<string, number> }

// Each section counts the cards passing every other section. A card counts under each of its tokens. An option with no
// card is absent, so a missing key reads as 0.
export function facets(cards: FilterCard[], view: View): Facets {
  const out: Facets = { types: {}, term: {}, gender: {}, areas: {}, area_ids: {} }
  const add = (section: Record<string, number>, key: string) => {
    section[key] = (section[key] ?? 0) + 1
  }
  for (const card of cards) {
    if (matches(card, { ...view, types: [] })) typeTokens(card).forEach((token) => add(out.types, token))
    if (matches(card, { ...view, term: null })) add(out.term, termOf(card))
    if (matches(card, { ...view, areas: [] })) areaNames(card).forEach((name) => add(out.areas, name))
    if (card.area_id != null && matches(card, { ...view, area_ids: [] })) add(out.area_ids, String(card.area_id))
    for (const gender of ['female', 'male', 'couple']) if (matches(card, { ...view, gender })) add(out.gender, gender)
  }
  return out
}

export type Tile = { id: string; label: string; icon: 'all' | 'room' | 'studio' | 'base' | 'one' | 'two' | 'three' | 'house' | 'apartment' | 'wanted'; patch: Pick<View, 'kind' | 'types'> }

const tile = (id: string, label: string, icon: Tile['icon'], types: string[]): Tile => ({ id, label, icon, patch: { kind: 'offer', types } })

// The options of each market (P1-07, P1-10). A Quebec «۴½» has no rooms, so Quebec has one apartment tile.
export function typeTiles(country: string, region: string): Tile[] {
  const all = tile('all', 'همه', 'all', [])
  const wanted: Tile = { id: 'wanted', label: 'دنبال خانه', icon: 'wanted', patch: { kind: 'wanted', types: [] } }
  if (country === 'DE') {
    return [all, tile('room', 'اتاق WG', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('1', '۱ اتاقه', 'one', ['bed1']), tile('2', '۲ اتاقه', 'two', ['bed2']), tile('3', '۳ اتاقه+', 'three', ['bed3']), tile('house', 'خانه', 'house', ['house']), wanted]
  }
  if (region === 'QC') {
    return [all, tile('room', 'اتاق', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('base', 'بیسمنت', 'base', ['basement']), tile('apartment', 'آپارتمان', 'apartment', ['apartment']), tile('house', 'خانه‌ی کامل', 'house', ['house']), wanted]
  }
  return [all, tile('room', 'اتاق', 'room', ['room']), tile('studio', 'استودیو', 'studio', ['studio']), tile('base', 'بیسمنت', 'base', ['basement']), tile('1', '۱ خوابه', 'one', ['bed1']), tile('2', '۲ خوابه', 'two', ['bed2']), tile('3', '۳ خوابه+', 'three', ['bed3']), tile('house', 'خانه‌ی کامل', 'house', ['house']), wanted]
}

export function tileOf(tiles: Tile[], view: View) {
  return tiles.find((item) => item.patch.kind === view.kind && item.patch.types.join() === view.types.join())
}

export const TERMS: Record<string, Array<{ id: Term; label: string }>> = {
  CA: [
    { id: 'lease', label: 'بلندمدت' },
    { id: 'sublet', label: 'سابلت' },
    { id: 'lease_transfer', label: 'لیز ترنسفر' },
    { id: 'short', label: 'کوتاه‌مدت' },
  ],
  DE: [
    { id: 'lease', label: 'بلندمدت' },
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

const TOKEN_LABELS: Record<string, string> = { room: 'اتاق', studio: 'استودیو', basement: 'بیسمنت', apartment: 'آپارتمان', house: 'خانه', other: 'خانه' }

// One removable chip per applied filter, labelled as the sheet labels it.
export function appliedChips(view: View, tiles: Tile[], country: string): Chip[] {
  const chips: Chip[] = []
  const known = tileOf(tiles, view)
  if (view.kind === 'wanted') chips.push({ id: 'kind', label: 'دنبال خانه', clear: (v) => ({ ...v, kind: 'offer' }) })
  if (view.kind === 'offer' && known && known.id !== 'all') {
    chips.push({ id: 'type', label: known.label, clear: (v) => ({ ...v, types: [] }) })
  } else if (!known) {
    // A link can name a type the tiles do not, such as an apartment in Toronto.
    for (const type of view.types) chips.push({ id: `type-${type}`, label: tiles.find((item) => item.patch.types.join() === type)?.label ?? TOKEN_LABELS[type] ?? type, clear: (v) => ({ ...v, types: v.types.filter((item) => item !== type) }) })
  }
  const budget = view.kind === 'offer' ? budgetLabel(view) : ''
  if (budget) chips.push({ id: 'budget', label: budget, clear: (v) => ({ ...v, min: null, max: null }) })
  for (const area of view.areas) chips.push({ id: `area-${area}`, label: area, clear: (v) => ({ ...v, areas: v.areas.filter((item) => item !== area) }) })
  if (view.term) chips.push({ id: 'term', label: (TERMS[country] ?? TERMS.CA).find((item) => item.id === view.term)?.label ?? view.term, clear: (v) => ({ ...v, term: null }) })
  if (view.furnished) chips.push({ id: 'furnished', label: 'مبله', clear: (v) => ({ ...v, furnished: false }) })
  if (view.bills) chips.push({ id: 'bills', label: 'قبوض داخل قیمت', clear: (v) => ({ ...v, bills: false }) })
  if (view.owner) chips.push({ id: 'owner', label: 'فقط مالک', clear: (v) => ({ ...v, owner: false }) })
  if (view.warm) chips.push({ id: 'warm', label: 'اجاره‌ی سرد نباشد', clear: (v) => ({ ...v, warm: false }) })
  if (view.reg) chips.push({ id: 'reg', label: 'ملد', clear: (v) => ({ ...v, reg: false }) })
  if (view.nowbs) chips.push({ id: 'nowbs', label: 'بدون نیاز به WBS', clear: (v) => ({ ...v, nowbs: false }) })
  const gender = GENDERS.find((item) => item.id === view.gender)
  if (view.gender && gender) chips.push({ id: 'gender', label: gender.label, clear: (v) => ({ ...v, gender: null }) })
  if (view.move) chips.push({ id: 'move', label: `از ${formatDate(view.move)}`, clear: (v) => ({ ...v, move: null, sort: v.sort === 'date' ? 'new' : v.sort }) })
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
  if (view.max != null && view.kind === 'offer') {
    for (let max = view.max + STEP; max <= top + STEP; max += STEP) {
      const next = { ...view, max }
      if (countMatches(cards, next) > 0) {
        add((n) => `تا ${fa(max)}: ${n} آگهی`, next)
        break
      }
    }
  }
  if (view.areas.length) add((n) => `همه‌جا: ${n} آگهی`, { ...view, areas: [] })
  if (view.move && view.window < 14) add((n) => `± ۱۴ روز: ${n} آگهی`, { ...view, window: 14 })
  if (view.furnished) add((n) => `بدون مبله: ${n}`, { ...view, furnished: false })
  if (view.types.length) add((n) => `همه‌ی نوع‌ها: ${n} آگهی`, { ...view, types: [] })
  return ways.slice(0, 3)
}

function byNew(a: Card, b: Card) {
  return new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime() || b.id - a.id
}

// Cards missing the value go last in every sort (P1-55). Ties and the missing group keep the newest-first order.
export function sortCards(cards: FilterCard[], sort: Sort, move: string | null = null) {
  const key: (card: FilterCard) => number | null =
    sort === 'cheap'
      ? (card) => card.price_monthly
      : sort === 'dear'
        ? (card) => (card.price_monthly == null ? null : -card.price_monthly)
        : sort === 'value'
          ? (card) => card.value_ratio ?? null
          : sort === 'date' && move
            ? (card) => (card.available_from ? daysFrom(card.available_from, move) : null)
            : () => 0
  return [...cards].sort((a, b) => {
    const left = key(a)
    const right = key(b)
    if (left == null && right == null) return byNew(a, b)
    if (left == null) return 1
    if (right == null) return -1
    return left - right || byNew(a, b)
  })
}
