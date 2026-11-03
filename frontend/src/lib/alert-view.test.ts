import { describe, expect, it } from 'vitest'
import { alertName, filtersOfView, sameSearch, viewOfFilters } from './alert-view'
import { readView } from './listing-view'

const view = (query: string) => readView(new URLSearchParams(query))

describe('filtersOfView', () => {
  it('keeps only what is set', () => {
    expect(filtersOfView(view(''))).toEqual({})
  })

  it('carries a budget with its no-price choice, as the server stores it', () => {
    expect(filtersOfView(view('max=2000'))).toEqual({ max: 2000, unpriced: true })
    expect(filtersOfView(view('max=2000&unpriced=0'))).toEqual({ max: 2000, unpriced: false })
  })

  it('never keeps text search, sort or parts', () => {
    expect(filtersOfView(view('q=مترو&sort=cheap&parts=Bonn'))).toEqual({})
  })

  it('comes back the same way', () => {
    const filters = filtersOfView(view('types=bed1&areas=نورث یورک&max=2000&furnished=1&term=sublet&gender=female'))
    expect(filtersOfView(viewOfFilters(filters))).toEqual(filters)
  })
})

describe('alertName', () => {
  it('F16#1: type, area and budget in the title, the rest under it', () => {
    expect(alertName(view('types=bed1&areas=نورث یورک&max=2000&furnished=1'), 'CA', 'تورنتو')).toEqual({ title: '۱ خوابه · نورث یورک · تا ۲٬۰۰۰', sub: 'تورنتو · مبله' })
  })

  it('F16#11: several types and areas', () => {
    expect(alertName(view('types=room&types=studio&areas=نورث یورک&areas=ریچموند هیل&max=1600'), 'CA', 'تورنتو').title).toBe('اتاق یا استودیو · نورث یورک، ریچموند هیل · تا ۱٬۶۰۰')
  })

  it('F15#10: only a city', () => {
    expect(alertName(view(''), 'CA', 'تورنتو')).toEqual({ title: 'همه‌ی آگهی‌های تورنتو', sub: 'تورنتو' })
  })

  it('a German market counts rooms', () => {
    expect(alertName(view('types=bed2'), 'DE', 'برلین').title).toBe('۲ اتاقه')
  })
})

describe('sameSearch', () => {
  it('ignores order and blanks', () => {
    expect(sameSearch({ types: ['room', 'studio'], areas: [], term: '' }, { types: ['studio', 'room'], furnished: false })).toBe(true)
  })

  it('tells two searches apart', () => {
    expect(sameSearch({ max: 1000 }, { max: 2000 })).toBe(false)
  })

  it('area ids decide when there are any, since the server adds their names', () => {
    expect(sameSearch({ area_ids: [9001] }, { area_ids: [9001], areas: ['نورث یورک'] })).toBe(true)
  })
})
