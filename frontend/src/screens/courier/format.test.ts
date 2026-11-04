import { describe, expect, it } from 'vitest'
import { kg, packageSize, sizeLabel, tripSpace } from './format'

describe('sizes', () => {
  it('a size chosen in the form reads as that size, not the weight stored for it', () => {
    expect(tripSpace('10.00')).toBe('۳ کیلو +')
    expect(tripSpace('3.00')).toBe('۱ تا ۳ کیلو')
    expect(tripSpace('0.50')).toBe('فقط مدرک')
    expect(packageSize('5.00')).toBe('۳ کیلو +')
    expect(packageSize('3.00')).toBe('۱ تا ۳ کیلو')
    expect(packageSize('0.50')).toBe('مدرک')
    expect(sizeLabel({ type: 'SUPPLY', capacity_kg: '10.00', weight_kg: null })).toBe('جا: ۳ کیلو +')
  })

  it('an exact weight from the old form or an import stays exact', () => {
    expect(tripSpace('0.10')).toBe(kg('0.10'))
    expect(tripSpace('20.00')).toBe(kg('20.00'))
    expect(packageSize('7.50')).toBe(kg('7.50'))
  })
})
