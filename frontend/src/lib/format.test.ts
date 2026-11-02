import { describe, expect, it } from 'vitest'
import { PRICE_KINDS, TORONTO } from '@/mocks/fixtures'
import { cardTitle, currency, modeBadge, priceTail, relativeTime } from './format'

describe('format', () => {
  it('prints the period and only a stated basis', () => {
    expect(priceTail(TORONTO[0])).toBe('در ماه')
    expect(priceTail({ price_period: 'night', price_basis: 'unknown' })).toBe('هر شب')
    expect(priceTail({ price_period: 'week', price_basis: 'unknown' })).toBe('در هفته')
    expect(priceTail({ price_period: 'month', price_basis: 'warm' })).toBe('در ماه · گرم')
    expect(priceTail({ price_period: 'month', price_basis: 'cold' })).toBe('در ماه · سرد')
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
