import { describe, expect, it } from 'vitest'
import { PRICE_KINDS, TORONTO, WANTED } from '@/mocks/fixtures'
import type { Card } from '@/api/types'
import { appliedChips, applyView, clearFilters, countMatches, readView, sortCards, typeTiles, waysOut, writeView } from './listing-view'

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
    expect(applyView(TORONTO, view('areas=وان'))).toHaveLength(2)
    expect(applyView(TORONTO, view('types=apartment&rooms=2'))).toHaveLength(1)
    expect(applyView(TORONTO, view('furnished=1'))).toHaveLength(TORONTO.length)
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

describe('filters, same rules as home.search', () => {
  const one = (patch: Partial<Card>) => [{ ...TORONTO[0], ...patch }]

  it('takes partly furnished as furnished, and warm rent as bills included', () => {
    expect(applyView(one({ furnished: 'partial' }), view('furnished=1'))).toHaveLength(1)
    expect(applyView(one({ furnished: 'no' }), view('furnished=1'))).toHaveLength(0)
    expect(applyView(one({ bills_included: 'no', price_basis: 'warm' }), view('bills=1'))).toHaveLength(1)
  })

  it('«اجاره‌ی سرد نباشد» keeps an unstated basis, and «فقط مالک» only drops agents', () => {
    expect(applyView(one({ price_basis: 'unknown' }), view('warm=1'))).toHaveLength(1)
    expect(applyView(one({ price_basis: 'cold' }), view('warm=1'))).toHaveLength(0)
    expect(applyView(one({ lister_type: 'unknown' }), view('owner=1'))).toHaveLength(1)
    expect(applyView(one({ lister_type: 'agent' }), view('owner=1'))).toHaveLength(0)
  })

  it('«مناسب زوج» takes a whole place not limited to one gender', () => {
    expect(applyView(one({ property_type: 'apartment', roommate_gender: 'any' }), view('gender=couple'))).toHaveLength(1)
    expect(applyView(one({ property_type: 'room', roommate_gender: 'any' }), view('gender=couple'))).toHaveLength(0)
    expect(applyView(one({ property_type: 'apartment', roommate_gender: 'female' }), view('gender=couple'))).toHaveLength(0)
    expect(applyView(one({ roommate_gender: 'female' }), view('gender=female'))).toHaveLength(1)
    expect(applyView(one({ roommate_gender: 'any' }), view('gender=female'))).toHaveLength(0)
  })

  it('moves in within the date window, and leaves out a listing with no date', () => {
    expect(applyView(one({ available_from: '2026-12-05' }), view('from=2026-12-01'))).toHaveLength(1)
    expect(applyView(one({ available_from: '2026-12-20' }), view('from=2026-12-01'))).toHaveLength(0)
    expect(applyView(one({ available_from: '2026-12-20' }), view('from=2026-12-01&window=30'))).toHaveLength(1)
    expect(applyView(one({ available_from: null }), view('from=2026-12-01'))).toHaveLength(0)
  })

  it('three rooms means three or more, and no rooms never matches a rooms tile', () => {
    expect(applyView(one({ rooms: 4 }), view('rooms=3'))).toHaveLength(1)
    expect(applyView(one({ rooms: null }), view('rooms=1'))).toHaveLength(0)
  })

  it('unpriced stays unless switched off', () => {
    const none = one({ price_amount: null, price_monthly: null })
    expect(applyView(none, view())).toHaveLength(1)
    expect(applyView(none, view('unpriced=0'))).toHaveLength(0)
  })

  it('offers each market its own type tiles', () => {
    expect(typeTiles('DE', '').map((tile) => tile.label)).toContain('۲ اتاقه')
    expect(typeTiles('CA', 'QC').map((tile) => tile.label)).toContain('آپارتمان')
    expect(typeTiles('CA', 'ON').map((tile) => tile.label)).toContain('۲ خوابه')
  })

  it('round-trips through the URL and leaves sort and city alone', () => {
    const params = new URLSearchParams('city=montreal&sort=cheap&view=compact')
    const next = writeView(params, { ...readView(params), types: ['apartment'], rooms: 1, max: 2000, furnished: true, unpriced: false, areas: ['وان'], from: '2026-12-01', window: 14 })
    expect(next.get('city')).toBe('montreal')
    expect(next.get('sort')).toBe('cheap')
    expect(next.get('view')).toBe('compact')
    const back = readView(next)
    expect(back).toMatchObject({ types: ['apartment'], rooms: 1, max: 2000, furnished: true, unpriced: false, areas: ['وان'], from: '2026-12-01', window: 14 })
    expect(appliedChips(back, typeTiles('CA', 'ON'), 'CA').map((chip) => chip.label)).toEqual(['۱ خوابه', 'تا ۲٬۰۰۰', 'وان', 'مبله', 'از ۱ دسامبر'])
    expect(appliedChips(clearFilters(back), typeTiles('CA', 'ON'), 'CA')).toEqual([])
  })

  it('offers only ways out with a result, and the nearest budget that has one', () => {
    const ways = waysOut(TORONTO, view('max=500&areas=وان'), 2450)
    expect(ways[0].label).toBe('تا ۷۵۰: ۱ آگهی')
    expect(ways.every((way) => countMatches(TORONTO, way.view) > 0)).toBe(true)
    expect(waysOut(TORONTO, view('types=house'), 2450).map((way) => way.label)).toEqual(['همه‌ی نوع‌ها: ۶ آگهی'])
  })
})
