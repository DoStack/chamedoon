import { describe, expect, it } from 'vitest'
import PARSE from '../../../backend/aisearch/testdata/parse_fixtures.json' with { type: 'json' }
import { readView, typeTiles, writeView } from './listing-view'
import { fitsSize, fromSpec, saidChips, toListParams } from './text-search'

const parsed = () => fromSpec(PARSE.parsed.response.spec, PARSE.parsed.response.area_labels)
const tiles = typeTiles('CA', 'ON')

describe('fromSpec', () => {
  it('gives the list a view it reads back the same', () => {
    for (const row of Object.values(PARSE)) {
      const { view } = fromSpec(row.response.spec, row.response.area_labels)
      expect(readView(writeView(new URLSearchParams(), view))).toEqual(view)
    }
  })
})

describe('fitsSize', () => {
  it('keeps a card with no size', () => {
    expect(fitsSize({ size_m2: null }, { min: 70, max: 90 })).toBe(true)
  })

  it('checks both ends', () => {
    expect(fitsSize({ size_m2: 69 }, { min: 70, max: 90 })).toBe(false)
    expect(fitsSize({ size_m2: 90 }, { min: 70, max: 90 })).toBe(true)
    expect(fitsSize({ size_m2: 91 }, { min: 70, max: 90 })).toBe(false)
  })
})

describe('saidChips', () => {
  it('has one chip per filter, with the area as typed', () => {
    const labels = saidChips(parsed(), tiles, 'CA').map((chip) => chip.label)
    expect(labels).toHaveLength(4)
    expect(labels).toContain('۱ خوابه')
    expect(labels).toContain('فینچ')
    expect(labels).toContain('۵۵ متر')
  })

  it('removes only its own filter', () => {
    const search = parsed()
    const area = saidChips(search, tiles, 'CA').find((chip) => chip.label === 'فینچ')!
    const next = area.clear(search)
    expect(next.view.area_ids).toEqual([])
    expect(next.view.types).toEqual(search.view.types)
    expect(next.size).toEqual(search.size)
  })
})

describe('toListParams', () => {
  it('names the areas, since the list has no chip for an id', () => {
    const params = toListParams(parsed())
    expect(params.getAll('areas')).toEqual(['نورث یورک'])
    expect(params.getAll('area_ids')).toEqual([])
    expect(params.getAll('types')).toEqual(['bed1'])
  })
})
