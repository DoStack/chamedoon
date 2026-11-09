import type { Furnished, Household, MineForm, PostParsed, PostRequest, PriceBasis, PricePeriod, RentalMode, Tri } from '@/api/post-types'
import type { Card } from '@/api/types'
import { cardTitle, fa } from './format'
import { toE164 } from './phone'
import { getWebApp, isVersionAtLeast } from './telegram'

export type PostKind = 'offer' | 'wanted'
export type HomeType = 'room' | 'studio' | 'basement' | 'b1' | 'b2' | 'b3' | 'house'
export type DraftCity = { country: string; city: string; slug: string; name_fa: string }
export type DraftPhoto = { photo_id: string; url: string; thumb: string }
export type Stage = 'text' | 'parsed' | 'manual'

export const DESCRIPTION_MIN = 20
export const DESCRIPTION_MAX = 1500
export const PRICE_MAX = 10_000_000
const AREA_MAX = 96
const CLOUD_MAX = 4096

// What the form holds. Numbers stay as typed, so a half-typed value survives a reload.
export type Draft = {
  kind: PostKind
  city: DraftCity | null
  step: number
  stage: Stage
  text: string
  homeType: HomeType | null
  rooms: number | null
  wantedTypes: HomeType[]
  rentalMode: RentalMode | null
  price: string
  pricePeriod: PricePeriod
  priceBasis: PriceBasis
  bills: Tri
  furnished: Furnished
  registration: Tri
  deposit: string
  size: string
  availableFrom: string
  availableTo: string
  minTerm: string
  area: string
  areaId: number | null
  areas: string[]
  address: string
  showAddress: boolean
  phone: string
  showPhone: boolean
  household: Household
  photos: DraftPhoto[]
  // One publish, one key: a double tap or a retry after a lost answer makes one listing.
  idempotencyKey: string | null
  savedAt: number
}

export function newDraft(kind: PostKind, city: DraftCity | null): Draft {
  return {
    kind,
    city,
    step: 1,
    stage: 'text',
    text: '',
    homeType: null,
    rooms: null,
    wantedTypes: [],
    rentalMode: null,
    price: '',
    pricePeriod: 'month',
    priceBasis: 'unknown',
    bills: 'unknown',
    furnished: 'unknown',
    registration: 'unknown',
    deposit: '',
    size: '',
    availableFrom: '',
    availableTo: '',
    minTerm: '',
    area: '',
    areaId: null,
    areas: [],
    address: '',
    showAddress: true,
    phone: '',
    showPhone: true,
    household: '',
    photos: [],
    idempotencyKey: null,
    savedAt: 0,
  }
}

const TYPE_FIELDS: Record<HomeType, { property_type: NonNullable<PostRequest['property_type']>; rooms: number | null }> = {
  room: { property_type: 'room', rooms: null },
  studio: { property_type: 'studio', rooms: null },
  basement: { property_type: 'basement', rooms: null },
  b1: { property_type: 'apartment', rooms: 1 },
  b2: { property_type: 'apartment', rooms: 2 },
  b3: { property_type: 'apartment', rooms: 3 },
  house: { property_type: 'house', rooms: null },
}

// An apartment with no room count stays unset: «۱ خوابه» would be a fact the person never gave.
export function typeFrom(property: PostParsed['property_type'], rooms: number | null | undefined): HomeType | null {
  switch (property) {
    case 'room':
    case 'studio':
    case 'basement':
    case 'house':
      return property
    case 'apartment':
      if (rooms == null) return null
      return rooms >= 3 ? 'b3' : rooms >= 2 ? 'b2' : 'b1'
    default:
      return null
  }
}

export function pickType(draft: Draft, homeType: HomeType): Draft {
  return { ...draft, homeType, rooms: TYPE_FIELDS[homeType].rooms }
}

const DIGITS = /[۰-۹٠-٩]/g

// One character for one, so a position here is a position in what was typed.
function latinDigits(text: string) {
  return text.replace(DIGITS, (digit) => String(Math.max('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit), '٠١٢٣٤٥٦٧٨٩'.indexOf(digit))))
}

export function amount(text: string): number | null {
  const clean = latinDigits(text).replace(/[٬,\s]/g, '').replace('٫', '.')
  if (!clean) return null
  const value = Number(clean)
  return Number.isFinite(value) && value >= 0 ? value : null
}

// Only what was read replaces what is there. `parsed` never holds a contact, by contract.
export function applyParsed(draft: Draft, parsed: PostParsed): Draft {
  const homeType = parsed.property_type ? typeFrom(parsed.property_type, parsed.rooms) : draft.homeType
  return {
    ...draft,
    homeType,
    rooms: parsed.rooms ?? (homeType ? TYPE_FIELDS[homeType].rooms : draft.rooms),
    rentalMode: parsed.rental_mode && parsed.rental_mode !== 'unknown' ? parsed.rental_mode : draft.rentalMode,
    price: parsed.price != null ? String(parsed.price) : draft.price,
    pricePeriod: parsed.price_period ?? draft.pricePeriod,
    priceBasis: parsed.price_basis ?? draft.priceBasis,
    bills: parsed.bills_included ?? draft.bills,
    furnished: parsed.furnished ?? draft.furnished,
    registration: parsed.registration ?? draft.registration,
    deposit: parsed.deposit != null ? String(parsed.deposit) : draft.deposit,
    size: parsed.size_m2 != null ? String(parsed.size_m2) : draft.size,
    availableFrom: parsed.available_from ?? draft.availableFrom,
    availableTo: parsed.available_to ?? draft.availableTo,
    minTerm: parsed.min_term_months != null ? String(parsed.min_term_months) : draft.minTerm,
    area: parsed.area ?? draft.area,
    areaId: parsed.area ? null : draft.areaId,
  }
}

const SEPARATOR = ' · '

// The contract has one place field, and cards read it as «محله · خیابان». An address that is switched off is never sent.
export function offerArea(draft: Pick<Draft, 'area' | 'address' | 'showAddress'>) {
  const address = draft.showAddress ? draft.address.trim() : ''
  return (address ? `${draft.area.trim()}${SEPARATOR}${address}` : draft.area.trim()).slice(0, AREA_MAX)
}

export function fromForm(form: MineForm, city: DraftCity): Draft {
  const kind = form.kind ?? 'offer'
  const [area = '', ...rest] = (form.area ?? '').split(SEPARATOR)
  const address = rest.join(SEPARATOR)
  const base = newDraft(kind, city)
  return {
    ...applyParsed({ ...base, stage: 'manual' }, { ...form, area: form.area }),
    stage: 'manual',
    text: form.description ?? '',
    area: kind === 'offer' ? area : '',
    areaId: form.area_id ?? null,
    areas: kind === 'wanted' ? (form.area ?? '').split('،').map((item) => item.trim()).filter(Boolean) : [],
    address,
    showAddress: Boolean(address),
    phone: form.phone,
    showPhone: Boolean(form.phone),
    household: form.household ?? '',
    wantedTypes: (form.wanted_types ?? []).map((type) => typeFrom(type, type === 'apartment' ? 1 : null)).filter((type): type is HomeType => type !== null),
    photos: form.photos.map((photo) => ({ photo_id: photo.photo_id, url: photo.url, thumb: photo.thumb })),
  }
}

const orNull = (value: number | null) => (value === null || value === 0 ? null : value)

// The one body a post or an edit sends. A contact travels in `phone` and nowhere else.
export function toRequest(draft: Draft, hasUsername: boolean): Omit<PostRequest, 'force_new'> {
  const offer = draft.kind === 'offer'
  const types = draft.wantedTypes.map((type) => TYPE_FIELDS[type].property_type)
  const wantedRooms = draft.wantedTypes.map((type) => TYPE_FIELDS[type].rooms).filter((rooms): rooms is number => rooms !== null)
  return {
    kind: draft.kind,
    property_type: offer ? (draft.homeType ? TYPE_FIELDS[draft.homeType].property_type : undefined) : types[0],
    ...(offer ? {} : { wanted_types: [...new Set(types)], household: draft.household }),
    rental_mode: draft.rentalMode ?? 'unknown',
    country: draft.city?.country,
    city: draft.city?.city,
    area: offer ? offerArea(draft) : draft.areas.join('، ').slice(0, AREA_MAX),
    area_id: offer ? draft.areaId : null,
    price: amount(draft.price),
    price_period: draft.pricePeriod,
    price_basis: draft.priceBasis,
    bills_included: draft.bills,
    furnished: draft.furnished,
    registration: draft.registration,
    deposit: orNull(amount(draft.deposit)),
    size_m2: orNull(amount(draft.size)),
    rooms: offer ? draft.rooms : wantedRooms.length ? Math.min(...wantedRooms) : null,
    available_from: draft.availableFrom || null,
    available_to: draft.availableTo || null,
    min_term_months: orNull(amount(draft.minTerm)),
    description: draft.text.trim(),
    phone: draft.showPhone || !hasUsername ? toE164(draft.phone, draft.city?.country) || draft.phone.trim() : '',
    photo_ids: offer ? draft.photos.map((photo) => photo.photo_id) : [],
  }
}

export type MissingField = 'type' | 'price' | 'area' | 'city'

export function missingFields(draft: Draft): MissingField[] {
  const out: MissingField[] = []
  if (!draft.homeType) out.push('type')
  if (!priceOk(draft.price)) out.push('price')
  if (!draft.area.trim()) out.push('area')
  if (!draft.city?.city) out.push('city')
  return out
}

export function priceOk(text: string) {
  const value = amount(text)
  return value !== null && value > 0 && value <= PRICE_MAX
}

export const textTooShort = (text: string) => text.trim().length < DESCRIPTION_MIN

const CURRENCY: Record<string, string> = { CA: 'CAD', DE: 'EUR', US: 'USD' }

export function currencyOf(country: string | undefined) {
  return CURRENCY[country ?? ''] ?? 'CAD'
}

// What the list card will show, for the preview. Never holds a contact, since a card has no such field.
export function draftCard(draft: Draft): Card {
  const fields = draft.homeType ? TYPE_FIELDS[draft.homeType] : TYPE_FIELDS.room
  const offer = draft.kind === 'offer'
  const now = new Date().toISOString()
  const price = amount(draft.price)
  return {
    id: 0,
    kind: draft.kind,
    property_type: fields.property_type,
    rental_mode: draft.rentalMode ?? 'unknown',
    country: draft.city?.country ?? '',
    region: '',
    city: draft.city?.city ?? '',
    metro: draft.city?.city ?? '',
    metro_slug: draft.city?.slug ?? '',
    area: offer ? offerArea(draft) : draft.areas.join('، '),
    area_id: draft.areaId,
    price_amount: offer ? price : null,
    price_max: offer ? null : price,
    price_currency: currencyOf(draft.city?.country),
    price_period: draft.pricePeriod,
    price_basis: draft.priceBasis,
    price_monthly: offer ? price : null,
    rooms: draft.rooms,
    size_m2: amount(draft.size),
    furnished: draft.furnished,
    bills_included: draft.bills,
    registration: draft.registration,
    roommate_gender: 'any',
    lister_type: 'owner',
    term: 'lease',
    occupants_wanted: null,
    couples_ok: false,
    wbs_required: false,
    value_ratio: null,
    summary_fa: '',
    summary_en: '',
    available_from: draft.availableFrom || null,
    available_to: draft.availableTo || null,
    first_seen: now,
    last_seen: now,
    thumb: draft.photos[0]?.thumb ?? '',
    photo_count: draft.photos.length,
  } as Card
}

// «آپارتمان ۱ خوابه · نورث یورک · ۴ عکس», on the save sheet and the draft card.
export function draftSummary(draft: Draft) {
  const parts = [draft.homeType ? cardTitle({ ...draftCard(draft), size_m2: null }) : '', draft.area.trim(), draft.photos.length ? `${fa(draft.photos.length)} عکس` : '']
  return parts.filter(Boolean).join(' · ')
}

export function hasContent(draft: Draft) {
  return Boolean(draft.text.trim() || draft.price || draft.homeType || draft.wantedTypes.length || draft.area || draft.areas.length || draft.photos.length || draft.phone)
}

// Same rules as home/contact_text.py, for the error as it is typed and for «حذف از متن». The server still decides at
// publish. No lookbehind: an old iOS webview cannot parse it, and that would take the whole chunk down.
const PREFIXED: RegExp[] = [
  /()\b(?:https?|tg):\/\/\S+/gi,
  /(^|[^\p{L}\p{N}_.])(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog|wa\.me|whatsapp\.com|chat\.whatsapp\.com|api\.whatsapp\.com|instagram\.com|signal\.me)(?:\/\S*)?/giu,
  /()@[A-Za-z][A-Za-z0-9_]{3,31}/g,
  /(^|\D)(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]?[2-9]\d{2}[\s.-]?\d{4}(?!\d)/g,
  /(^|\D)\+\d[\d\s().-]{8,16}\d/g,
  /(^|[^\d+])0\d{2,4}[\s/.-]?\d[\d\s/.-]{4,12}\d/g,
  /(^|[^\p{L}\p{N}_@])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\p{L}\p{N}_])/gu,
]
const LETTERS_DIGITS = /(^|[^\p{L}\p{N}_@])([A-Za-z]{3,})\d{2,}(?![\p{L}\p{N}_])/gu
const NOT_A_HANDLE = new Set(['apt', 'apartment', 'unit', 'suite', 'room', 'floor', 'bldg', 'block', 'haus', 'zimmer', 'wohnung', 'tel', 'studio', 'cad', 'usd', 'eur', 'gbp', 'euro', 'dollar', 'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec'])
const ANNOUNCED = /(آیدی|ایدی|تلگرام|واتساپ|واتس‌اپ|پی‌وی|پیوی|\b(?:id|telegram|tg|whatsapp|pv|contact|ig|insta|instagram|snap|snapchat)\b)\s*[:：-]?\s*([A-Za-z][A-Za-z0-9_.]{4,31})(?![\p{L}\p{N}_])/giu

function contactSpans(text: string): Array<[number, number]> {
  const source = latinDigits(text)
  const spans: Array<[number, number]> = []
  for (const pattern of PREFIXED) {
    for (const match of source.matchAll(pattern)) {
      const start = (match.index ?? 0) + match[1].length
      spans.push([start, (match.index ?? 0) + match[0].length])
    }
  }
  for (const match of source.matchAll(LETTERS_DIGITS)) {
    if (!NOT_A_HANDLE.has(match[2].toLowerCase())) spans.push([(match.index ?? 0) + match[1].length, (match.index ?? 0) + match[0].length])
  }
  for (const match of source.matchAll(ANNOUNCED)) {
    const end = (match.index ?? 0) + match[0].length
    spans.push([end - match[2].length, end])
  }
  return spans
}

export const hasContact = (text: string) => contactSpans(text).length > 0

export function removeContact(text: string) {
  const spans = contactSpans(text).sort((left, right) => left[0] - right[0])
  if (!spans.length) return text
  let out = ''
  let cursor = 0
  for (const [start, end] of spans) {
    if (start > cursor) out += text.slice(cursor, start)
    cursor = Math.max(cursor, end)
  }
  return (out + text.slice(cursor)).replace(/\s+/g, ' ').trim()
}

const localKey = (kind: PostKind) => `chamedoon.post.${kind}`
// CloudStorage keys take only letters, digits, _ and -.
const cloudKey = (kind: PostKind) => `chamedoon_post_${kind}`

function cloudStorage() {
  return isVersionAtLeast('6.9') ? getWebApp()?.CloudStorage : undefined
}

function parseDraft(raw: string | null | undefined, kind: PostKind): Draft | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Draft
    return value?.kind === kind ? { ...newDraft(kind, null), ...value } : null
  } catch {
    return null
  }
}

export function loadDraft(kind: PostKind): Draft | null {
  try {
    return parseDraft(localStorage.getItem(localKey(kind)), kind)
  } catch {
    return null
  }
}

// A draft touched in this run is not one that came back after a reopen.
let touchedThisRun = false

export const draftTouchedThisRun = () => touchedThisRun

export function writeDraft(draft: Draft): boolean {
  touchedThisRun = true
  try {
    localStorage.setItem(localKey(draft.kind), JSON.stringify({ ...draft, savedAt: Date.now() }))
    return true
  } catch {
    return false
  }
}

// The device copy answers at once; CloudStorage brings it back after a reinstall or on another device.
export async function restoreDraft(kind: PostKind): Promise<Draft | null> {
  const local = loadDraft(kind)
  const cloud = cloudStorage()
  if (local || !cloud) return local
  const raw = await new Promise<string>((resolve) => {
    const timer = setTimeout(() => resolve(''), 800)
    try {
      cloud.getItem(cloudKey(kind), (error, stored) => {
        clearTimeout(timer)
        resolve(error ? '' : (stored ?? ''))
      })
    } catch {
      clearTimeout(timer)
      resolve('')
    }
  })
  return parseDraft(raw, kind)
}

// False only when neither copy took it (F18#31). The cloud copy leaves the phone out, and gives up on what does not fit.
export async function saveDraft(draft: Draft): Promise<boolean> {
  const local = writeDraft(draft)
  const cloud = cloudStorage()
  const value = JSON.stringify({ ...draft, phone: '', savedAt: Date.now() })
  if (!cloud || value.length > CLOUD_MAX) return local
  const stored = await new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => resolve(false), 800)
    try {
      cloud.setItem(cloudKey(draft.kind), value, (error) => {
        clearTimeout(timer)
        resolve(!error)
      })
    } catch {
      clearTimeout(timer)
      resolve(false)
    }
  })
  return local || stored
}

export function clearDraft(kind: PostKind) {
  try {
    localStorage.removeItem(localKey(kind))
  } catch {
    // Nothing kept to clear.
  }
  try {
    cloudStorage()?.setItem(cloudKey(kind), '')
  } catch {
    // Same.
  }
}

// A new offer started while a draft is saved has its own copy on the device, so typing and leaving it never touch the
// saved one. Only «ذخیره پیش‌نویس» replaces that. It stays off CloudStorage: this is a working copy, not a saved draft.
const FRESH_KEY = 'chamedoon.post.offer.fresh'

export function loadFresh(): Draft | null {
  try {
    return parseDraft(localStorage.getItem(FRESH_KEY), 'offer')
  } catch {
    return null
  }
}

export function writeFresh(draft: Draft): boolean {
  touchedThisRun = true
  try {
    localStorage.setItem(FRESH_KEY, JSON.stringify({ ...draft, savedAt: Date.now() }))
    return true
  } catch {
    return false
  }
}

export function clearFresh() {
  try {
    localStorage.removeItem(FRESH_KEY)
  } catch {
    // Nothing kept to clear.
  }
}
