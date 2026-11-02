import { describe, expect, it } from 'vitest'
import { PRICE_KINDS, TORONTO, WANTED } from '@/mocks/fixtures'
import type { Card } from '@/api/types'
import { applyView, readView, sortCards } from './listing-view'

const view = (query = '') => readView(new URLSearchParams(query))
const ids = (cards: Card[]) => cards.map((card) => card.id)
const noPrice = PRICE_KINDS.find((card) => card.price_monthly == null)!

describe('applyView', () => {
  it('lists offers by default and wanted posts only by the type filter', () => {
    const all = [...TORONTO, ...WANTED]
    expect(applyView(all, view()).every((card) => card.kind === 'offer')).toBe(true)
    expect(applyView(all, view('kind=wanted')).every((card) => card.kind === 'wanted')).toBe(true)
  })

  it('compares budget on the monthly value, never the amount', () => {
    const nightly = PRICE_KINDS[0] // 90 a night, 2700 a month
    expect(applyView([nightly], view('max=1000'))).toEqual([])
    expect(applyView([nightly], view('max=3000'))).toHaveLength(1)
  })

  it('keeps a listing with no price inside a budget', () => {
    expect(applyView([noPrice], view('max=1000'))).toHaveLength(1)
  })

  it('filters by type, area, part and text, in Persian or Latin digits', () => {
    expect(applyView(TORONTO, view('types=room')).every((card) => card.property_type === 'room')).toBe(true)
    expect(applyView(TORONTO, view('areas=وان')).every((card) => card.area.includes('وان'))).toBe(true)
    expect(applyView(TORONTO, view('parts=Nowhere'))).toEqual([])
    expect(ids(applyView(TORONTO, view('q=۱۴ متر')))).toEqual(ids(applyView(TORONTO, view('q=14 متر'))))
    expect(applyView(TORONTO, view('q=Union'))).toHaveLength(1)
  })

  it('ignores a bad sort or number in the URL', () => {
    expect(view('sort=nonsense').sort).toBe('new')
    expect(view('min=abc').min).toBeNull()
  })
})

describe('sortCards', () => {
  it('puts nulls last in every sort', () => {
    for (const sort of ['cheap', 'dear', 'value', 'date'] as const) {
      const sorted = sortCards([{ ...noPrice, available_from: null }, { ...noPrice, id: 1, price_monthly: 500, size_m2: 10, available_from: '2026-12-01' }, { ...noPrice, id: 2, available_from: null }], sort)
      expect(sorted[0].id).toBe(1)
    }
  })

  it('sorts by the monthly value and by recency', () => {
    expect(sortCards(TORONTO, 'cheap')[0].price_monthly).toBe(750)
    expect(sortCards(TORONTO, 'dear')[0].price_monthly).toBe(2450)
    const newest = sortCards(TORONTO, 'new')[0]
    expect(TORONTO.every((card) => new Date(card.last_seen) <= new Date(newest.last_seen))).toBe(true)
  })

  it('value sort compares price per m² inside one neighbourhood', () => {
    const area = { area: 'A', available_from: null }
    const a = { ...noPrice, ...area, id: 1, price_monthly: 1000, size_m2: 100 } // 10 per m²
    const b = { ...noPrice, ...area, id: 2, price_monthly: 1000, size_m2: 50 } // 20 per m²
    expect(ids(sortCards([b, a], 'value'))).toEqual([1, 2])
  })
})
