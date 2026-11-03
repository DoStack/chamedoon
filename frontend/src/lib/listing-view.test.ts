import { describe, expect, it } from 'vitest'
import { PRICE_KINDS, TORONTO, WANTED } from '@/mocks/fixtures'
import type { Card } from '@/api/types'
import { readFileSync, existsSync } from 'node:fs'
import { appliedChips, applyView, clearFilters, countMatches, facets, matches, readView, sortCards, typeTiles, waysOut, writeView, type FilterCard, type View } from './listing-view'

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

  it('keeps a listing with no price inside a budget unless switched off', () => {
    expect(applyView([noPrice], view('max=1000'))).toHaveLength(1)
    expect(applyView([noPrice], view('max=1000&unpriced=0'))).toHaveLength(0)
    expect(applyView([noPrice], view('unpriced=0'))).toHaveLength(1)
  })

  it('filters by type, area, part and text, in Persian or Latin digits', () => {
    expect(applyView(TORONTO, view('types=room')).every((card) => card.property_type === 'room')).toBe(true)
    expect(applyView(TORONTO, view('areas=وان'))).toHaveLength(2)
    expect(applyView(TORONTO, view('types=bed2'))).toHaveLength(1)
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
      const sorted = sortCards([{ ...noPrice, available_from: null }, { ...noPrice, id: 1, price_monthly: 500, value_ratio: 1, available_from: '2026-12-01' }, { ...noPrice, id: 2, available_from: null }], sort, '2026-12-01')
      expect(sorted[0].id).toBe(1)
    }
  })

  it('sorts by the monthly value and by recency', () => {
    expect(sortCards(TORONTO, 'cheap')[0].price_monthly).toBe(750)
    expect(sortCards(TORONTO, 'dear')[0].price_monthly).toBe(2450)
    const newest = sortCards(TORONTO, 'new')[0]
    expect(TORONTO.every((card) => new Date(card.last_seen) <= new Date(newest.last_seen))).toBe(true)
  })

  it('value sort reads the ratio from the snapshot', () => {
    expect(ids(sortCards([{ ...noPrice, id: 2, value_ratio: 1.2 }, { ...noPrice, id: 1, value_ratio: 0.8 }], 'value'))).toEqual([1, 2])
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
    expect(applyView(one({ available_from: '2026-12-05' }), view('move=2026-12-01'))).toHaveLength(1)
    expect(applyView(one({ available_from: '2026-12-20' }), view('move=2026-12-01'))).toHaveLength(0)
    expect(applyView(one({ available_from: '2026-12-20' }), view('move=2026-12-01&window=30'))).toHaveLength(1)
    expect(applyView(one({ available_from: null }), view('move=2026-12-01'))).toHaveLength(0)
  })

  it('bed3 means three or more, and no rooms never matches a bed token', () => {
    expect(applyView(one({ rooms: 4 }), view('types=bed3'))).toHaveLength(1)
    expect(applyView(one({ rooms: null }), view('types=bed1'))).toHaveLength(0)
  })

  it('offers each market its own type tiles', () => {
    expect(typeTiles('DE', '').map((tile) => tile.label)).toContain('۲ اتاقه')
    expect(typeTiles('CA', 'QC').map((tile) => tile.label)).toContain('آپارتمان')
    expect(typeTiles('CA', 'ON').map((tile) => tile.label)).toContain('۲ خوابه')
  })

  it('round-trips through the URL and leaves sort and city alone', () => {
    const params = new URLSearchParams('city=montreal&sort=cheap&view=compact')
    const next = writeView(params, { ...readView(params), types: ['bed1'], max: 2000, furnished: true, unpriced: false, areas: ['وان'], move: '2026-12-01', window: 14 })
    expect(next.get('city')).toBe('montreal')
    expect(next.get('sort')).toBe('cheap')
    expect(next.get('view')).toBe('compact')
    const back = readView(next)
    expect(back).toMatchObject({ types: ['bed1'], max: 2000, furnished: true, unpriced: false, areas: ['وان'], move: '2026-12-01', window: 14 })
    expect(appliedChips(back, typeTiles('CA', 'ON'), 'CA').map((chip) => chip.label)).toEqual(['۱ خوابه', 'تا ۲٬۰۰۰', 'وان', 'مبله', 'از ۱ دسامبر'])
    expect(appliedChips(clearFilters(back), typeTiles('CA', 'ON'), 'CA')).toEqual([])
  })

  it('holds the sort on the URL, drops the default, and clearing ends a date sort', () => {
    const on = writeView(new URLSearchParams('sort=new'), { ...view('sort=date&move=2026-12-01') })
    expect(on.get('sort')).toBe('date')
    expect(writeView(on, view()).has('sort')).toBe(false)
    const cleared = clearFilters(view('sort=date&move=2026-12-01'))
    expect(writeView(on, cleared).has('sort')).toBe(false)
    expect(writeView(new URLSearchParams(), clearFilters(view('sort=cheap'))).get('sort')).toBe('cheap')
  })

  it('counts a section from the other sections', () => {
    const counted = facets(TORONTO, view('types=room&term=lease'))
    expect(counted.types.bed1).toBe(2)
    expect(counted.term.sublet).toBe(1)
  })

  it('offers only ways out with a result, and the nearest budget that has one', () => {
    const ways = waysOut(TORONTO, view('max=500&areas=وان'), 2450)
    expect(ways[0].label).toBe('تا ۷۵۰: ۱ آگهی')
    expect(ways.every((way) => countMatches(TORONTO, way.view) > 0)).toBe(true)
    expect(waysOut(TORONTO, view('types=house'), 2450).map((way) => way.label)).toEqual(['همه‌ی نوع‌ها: ۶ آگهی'])
  })
})

// From the backend's snapshot work. The same file runs in pytest, so the two implementations cannot drift.
const VECTORS = `${__dirname}/../../../backend/home_api/testdata/filter_vectors.json`

describe.skipIf(!existsSync(VECTORS))('filter_vectors.json', () => {
  const data = existsSync(VECTORS) ? JSON.parse(readFileSync(VECTORS, 'utf8')) : { cards: [], cases: [], facet_cases: [] }
  const cards = data.cards as FilterCard[]
  const toView = (filters: Partial<View>): View => ({ ...view(), unpriced: false, ...filters })

  for (const item of data.cases) {
    it(item.name, () => {
      // A case's top-level `move` only orders; one inside `filters` also filters.
      const shown = cards.filter((card) => matches(card, toView(item.filters)))
      expect(ids(sortCards(shown, item.sort ?? 'new', item.move ?? item.filters.move))).toEqual(item.expected_ids)
    })
  }

  for (const item of data.facet_cases) {
    it(`facets: ${item.name}`, () => {
      expect(facets(cards, toView(item.filters))).toMatchObject(item.facets)
      const counted = facets(cards, toView(item.filters))
      for (const section of ['types', 'term', 'gender', 'areas', 'area_ids'] as const) expect(counted[section]).toEqual(item.facets[section])
    })
  }
})
