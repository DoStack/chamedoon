import { afterEach, describe, expect, it, vi } from 'vitest'
import { appDir, getLocale, setLocale, t } from './locale'

afterEach(() => setLocale('fa'))

describe('locale', () => {
  it('starts in Persian, right to left, and changes no text', () => {
    expect(getLocale()).toBe('fa')
    expect(appDir()).toBe('rtl')
    expect(t('جستجو')).toBe('جستجو')
    expect(t('{n} آگهی', { n: '۳' })).toBe('۳ آگهی')
  })

  it('translates and turns the page around', async () => {
    await setLocale('de')
    expect(t('جستجو')).toBe('Suche')
    expect(appDir()).toBe('ltr')
    expect(document.documentElement.dir).toBe('ltr')
    expect(document.documentElement.lang).toBe('de')
  })

  it('keeps Persian for a message with no translation and logs it once', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await setLocale('en')
    expect(t('متنی که ترجمه ندارد')).toBe('متنی که ترجمه ندارد')
    expect(t('متنی که ترجمه ندارد')).toBe('متنی که ترجمه ندارد')
    expect(warn).toHaveBeenCalledTimes(1)
    warn.mockRestore()
  })
})
