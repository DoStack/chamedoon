import { describe, expect, it } from 'vitest'
import { normalize, parseCourier, type ParseCategory, type ParseCountry } from './smart-parse'

const COUNTRIES: ParseCountry[] = [
  { code: 'CA', name_fa: 'کانادا', name_en: 'Canada', cities: [{ slug: 'toronto', name_fa: 'تورنتو', name_en: 'Toronto' }, { slug: 'montreal', name_fa: 'مونترال', name_en: 'Montreal' }] },
  { code: 'IR', name_fa: 'ایران', name_en: 'Iran', cities: [{ slug: 'tehran', name_fa: 'تهران', name_en: 'Tehran' }, { slug: 'shiraz', name_fa: 'شیراز', name_en: 'Shiraz' }] },
  { code: 'DE', name_fa: 'آلمان', name_en: 'Germany', cities: [{ slug: 'berlin', name_fa: 'برلین', name_en: 'Berlin' }] },
]
const CATEGORIES: ParseCategory[] = [
  { code: 'DOCUMENTS', name_fa: 'مدرک', name_en: 'Documents' },
  { code: 'CLOTHES', name_fa: 'لباس', name_en: 'Clothes' },
  { code: 'MEDICINE', name_fa: 'دارو', name_en: 'Medicine' },
]
const NOW = new Date('2026-11-10T12:00:00Z')
const parse = (text: string) => parseCourier(text, COUNTRIES, CATEGORIES, NOW)

describe('parseCourier', () => {
  it('reads the design example', () => {
    expect(parse('از تورنتو به تهران تا آخر نوامبر، مدارک')).toEqual({ from: 'CA:toronto', to: 'IR:tehran', month: '2026-11', cat: 'DOCUMENTS', size: '', side: '' })
  })
  it('takes «به» before «از» and English names', () => {
    expect(parse('به Tehran از Montreal')).toMatchObject({ from: 'CA:montreal', to: 'IR:tehran' })
  })
  it('without «از» and «به», the first place named is the origin', () => {
    expect(parse('برلین تهران دسامبر')).toMatchObject({ from: 'DE:berlin', to: 'IR:tehran', month: '2026-12' })
  })
  it('a Jalali month and a past month roll to the right year', () => {
    expect(parse('تهران تورنتو دی').month).toBe('2027-01')
    expect(parse('مارس').month).toBe('2027-03')
  })
  it('reads size and side with Persian digits', () => {
    expect(parse('مسافر تورنتو تا ۱ کیلو')).toMatchObject({ from: 'CA:toronto', size: '1', side: 'DEMAND' })
    expect(parse('فقط مدرک')).toMatchObject({ size: 'doc' })
  })
  it('category by its Persian or English name', () => {
    expect(parse('دارو به شیراز')).toMatchObject({ cat: 'MEDICINE', to: 'IR:shiraz' })
    expect(parse('clothes')).toMatchObject({ cat: 'CLOTHES' })
  })
  it('nothing known is empty', () => {
    expect(parse('سلام')).toEqual({ from: '', to: '', month: '', cat: '', size: '', side: '' })
    expect(normalize('  ي ك۱۲ ')).toBe('ی ک12')
  })
})
