import type { Card } from '@/api/types'
import { cardTitle } from './format'

export const SORTS = ['new', 'value', 'cheap', 'dear', 'date'] as const
export type Sort = (typeof SORTS)[number]

export type View = {
  kind: 'offer' | 'wanted'
  types: string[]
  min: number | null
  max: number | null
  areas: string[]
  parts: string[]
  q: string
  sort: Sort
}

function toNumber(value: string | null) {
  if (!value) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function readView(params: URLSearchParams): View {
  const sort = params.get('sort') as Sort | null
  return {
    kind: params.get('kind') === 'wanted' ? 'wanted' : 'offer',
    types: params.getAll('types'),
    min: toNumber(params.get('min')),
    max: toNumber(params.get('max')),
    areas: params.getAll('areas'),
    parts: params.getAll('parts'),
    q: params.get('q') ?? '',
    sort: sort && SORTS.includes(sort) ? sort : 'new',
  }
}

const DIGITS = '۰۱۲۳۴۵۶۷۸۹'

function normalize(text: string) {
  return text.toLowerCase().replace(/[۰-۹]/g, (digit) => String(DIGITS.indexOf(digit)))
}

// Text only until P5 (audit P1-50): it searches what a card shows.
function matchesText(card: Card, q: string) {
  const text = normalize(`${cardTitle(card)} ${card.area} ${card.city}`)
  return normalize(q).split(/\s+/).filter(Boolean).every((word) => text.includes(word))
}

// Budget compares the monthly value, never price_amount (P0-13). A listing with no price stays in, as in the templates.
export function applyView(cards: Card[], view: View) {
  const shown = cards.filter((card) => {
    if (card.kind !== view.kind) return false
    if (view.types.length && !view.types.includes(card.property_type)) return false
    const price = view.kind === 'wanted' ? card.price_max : card.price_monthly
    if (price != null && view.min != null && price < view.min) return false
    if (price != null && view.max != null && price > view.max) return false
    if (view.areas.length && !view.areas.some((area) => card.area.includes(area))) return false
    if (view.parts.length && !view.parts.includes(card.city)) return false
    if (view.q && !matchesText(card, view.q)) return false
    return true
  })
  return sortCards(shown, view.sort)
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
