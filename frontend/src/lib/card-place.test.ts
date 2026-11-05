import { describe, expect, it } from 'vitest'
import { cardPlace, rememberCityNames } from './format'

describe('cardPlace', () => {
  it('prefers the area, then the Persian name of a launch city, then what the card says', () => {
    rememberCityNames([{ name: 'Berlin', name_fa: 'برلین' }])
    expect(cardPlace({ area: 'Spandau', city: 'Berlin' })).toBe('Spandau')
    expect(cardPlace({ area: '', city: 'Berlin' })).toBe('برلین')
    expect(cardPlace({ area: '', city: 'Essen' })).toBe('Essen')
  })

  it('takes the Persian name the card carries for a city outside the launch list', () => {
    expect(cardPlace({ area: '', city: 'Essen', city_fa: 'اسن' })).toBe('اسن')
    expect(cardPlace({ area: '', city: 'Essen', city_fa: '' })).toBe('Essen')
  })
})
