import { describe, expect, it } from 'vitest'
import { PRICE_KINDS, TORONTO } from '@/mocks/fixtures'
import { cardTitle, currency, modeBadge, placeRuns, priceTail, relativeTime } from './format'

describe('placeRuns', () => {
  const ltr = (text: string) => placeRuns(text).filter((run) => run.ltr).map((run) => run.text)

  it('isolates a street that opens with its number, with or without an area', () => {
    expect(ltr('5000 Yonge St')).toEqual(['5000 Yonge St'])
    expect(ltr('نورث یورک · 5000 Yonge St')).toEqual(['5000 Yonge St'])
    expect(ltr('North York · 5000 Yonge St')).toEqual(['North York', '5000 Yonge St'])
    expect(ltr('خیابان ۵۰۰۰ Yonge St')).toEqual(['۵۰۰۰ Yonge St'])
  })

  it('takes German letters as Latin, so a name is not cut at its umlaut', () => {
    expect(ltr('Neukölln, Spandau, Köpenick')).toEqual(['Neukölln, Spandau, Köpenick'])
    expect(ltr('ÖPNV')).toEqual(['ÖPNV'])
  })

  it('keeps brackets inside the run, so they do not swap', () => {
    expect(ltr('North York (Yonge between Steeles and Finch, opposite Iranian Plaza)')).toEqual(['North York (Yonge between Steeles and Finch, opposite Iranian Plaza)'])
    expect(ltr('(near lower Port Moody) Port Moody')).toEqual(['(near lower Port Moody) Port Moody'])
    expect(ltr('پلاک (12)')).toEqual([])
  })

  it('leaves Persian text and bare numbers alone, and loses nothing', () => {
    expect(ltr('نورث یورک')).toEqual([])
    expect(ltr('پلاک 12')).toEqual([])
    for (const text of ['نورث یورک · 5000 Yonge St', 'North York', 'پلاک 12 · Bloor', '']) {
      expect(placeRuns(text).map((run) => run.text).join('')).toBe(text)
    }
  })
})

describe('format', () => {
  it('prints the period and only a stated basis', () => {
    expect(priceTail(TORONTO[0])).toBe('در ماه')
    expect(priceTail({ price_period: 'night', price_basis: 'unknown', country: 'CA' })).toBe('هر شب')
    expect(priceTail({ price_period: 'week', price_basis: 'unknown', country: 'CA' })).toBe('در هفته')
    expect(priceTail({ price_period: 'month', price_basis: 'warm', country: 'DE' })).toBe('در ماه · گرم')
    expect(priceTail({ price_period: 'month', price_basis: 'cold', country: 'DE' })).toBe('در ماه · سرد')
  })

  it('never says warm or cold on a Canadian card, whatever the model read', () => {
    expect(priceTail({ price_period: 'month', price_basis: 'warm', country: 'CA' })).toBe('در ماه')
    expect(priceTail({ price_period: 'month', price_basis: 'cold', country: 'CA' })).toBe('در ماه')
  })

  it('states half rooms and never rounds them', () => {
    expect(cardTitle({ property_type: 'apartment', rooms: 2.5, country: 'DE', size_m2: null })).toBe('آپارتمان ۲٫۵ اتاقه')
    expect(cardTitle({ property_type: 'apartment', rooms: 1.5, country: 'CA', size_m2: 60 })).toBe('آپارتمان ۱٫۵ خوابه · ۶۰ متر')
    expect(cardTitle({ property_type: 'house', rooms: 3, country: 'CA', size_m2: null })).toBe('خانه ۳ خوابه')
  })

  it('reads German rooms as Zimmer, and leaves rooms out when unknown', () => {
    expect(cardTitle({ property_type: 'apartment', rooms: 2, country: 'DE', size_m2: 52 })).toBe('آپارتمان ۲ اتاقه · ۵۲ متر')
    expect(cardTitle({ property_type: 'apartment', rooms: 2, country: 'CA', size_m2: null })).toBe('آپارتمان ۲ خوابه')
    expect(cardTitle({ property_type: 'apartment', rooms: null, country: 'CA', size_m2: 70 })).toBe('آپارتمان · ۷۰ متر')
    expect(cardTitle({ property_type: 'room', rooms: null, country: 'CA', size_m2: 14 })).toBe('اتاق · ۱۴ متر')
  })

  it('names the rental mode per country', () => {
    expect(modeBadge(PRICE_KINDS[0])).toBe('کوتاه‌مدت')
    expect(modeBadge(TORONTO[0])).toBe('')
    expect(modeBadge({ rental_mode: 'sublet', country: 'CA', available_from: '2026-12-01', available_to: '2027-02-28' })).toBe('سابلت · ۱ دسامبر تا ۲۸ فوریه')
    expect(modeBadge({ rental_mode: 'lease_transfer', country: 'DE', available_from: null, available_to: null })).toBe('Nachmieter')
  })

  it('prints relative times and currencies', () => {
    const now = new Date('2026-11-10T12:00:00Z').getTime()
    expect(relativeTime('2026-11-10T09:00:00Z', now)).toBe('۳ ساعت پیش')
    expect(relativeTime('2026-11-09T09:00:00Z', now)).toBe('دیروز')
    expect(relativeTime('2026-11-07T09:00:00Z', now)).toBe('۳ روز پیش')
    expect(currency('EUR')).toBe('€')
    expect(currency('CAD')).toBe('CA$')
  })
})
