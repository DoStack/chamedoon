import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fa, formatDate, relativeTime, roomsLabel } from './format'
import { getLocale, setLocale } from './locale'
import { adoptServerUi, applyStoredUi, chooseUi, fromTelegram } from './ui-prefs'

const patchPrefs = vi.hoisted(() => vi.fn(async () => ({ ok: true })))
vi.mock('./account', () => ({ patchPrefs }))

beforeEach(() => {
  localStorage.clear()
  patchPrefs.mockClear()
  document.documentElement.className = ''
})
afterEach(() => setLocale('fa'))

describe('fromTelegram', () => {
  it('takes en and de, and everything else is Persian', () => {
    expect(fromTelegram('en-US')).toBe('en')
    expect(fromTelegram('de')).toBe('de')
    expect(fromTelegram('fr')).toBe('fa')
    expect(fromTelegram(undefined)).toBe('fa')
  })
})

describe('applyStoredUi', () => {
  it('uses what this device chose', async () => {
    localStorage.setItem('chamedoon.ui', JSON.stringify({ locale: 'de', theme: 'dark' }))
    await applyStoredUi()
    expect(getLocale()).toBe('de')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('ignores a bad stored value', async () => {
    localStorage.setItem('chamedoon.ui', '{"locale":"xx"}')
    await applyStoredUi()
    expect(getLocale()).toBe('fa')
  })
})

describe('adoptServerUi', () => {
  it('takes another device’s choice when this one has none', async () => {
    await adoptServerUi({ locale: 'en', theme: 'auto' })
    expect(getLocale()).toBe('en')
  })

  it('never overrides what this device chose', async () => {
    localStorage.setItem('chamedoon.ui', JSON.stringify({ locale: 'de', theme: 'auto' }))
    await adoptServerUi({ locale: 'en', theme: 'auto' })
    expect(getLocale()).toBe('fa')
  })
})

describe('chooseUi', () => {
  it('applies at once and stores, then tells the server only what changed', async () => {
    await chooseUi({ locale: 'en' })
    expect(getLocale()).toBe('en')
    expect(JSON.parse(localStorage.getItem('chamedoon.ui')!).locale).toBe('en')
    expect(patchPrefs).toHaveBeenCalledWith({ locale: 'en' })
  })
})

describe('format follows the language', () => {
  const apartment = { property_type: 'apartment', rooms: 2, country: 'DE' } as const

  it('stays as it was in Persian', () => {
    expect(fa(1850)).toBe('۱٬۸۵۰')
    expect(roomsLabel(apartment)).toBe('آپارتمان ۲ اتاقه')
    expect(formatDate('2026-11-09')).toBe('۹ نوامبر')
  })

  it('reads English', async () => {
    await setLocale('en')
    expect(fa(1850)).toBe('1,850')
    expect(roomsLabel({ ...apartment, rooms: 1, country: 'CA' })).toBe('1-bedroom apartment')
    expect(relativeTime(new Date(Date.now() - 3 * 3_600_000).toISOString())).toBe('3 h ago')
  })

  it('reads German', async () => {
    await setLocale('de')
    expect(fa(1150)).toBe('1.150')
    expect(roomsLabel(apartment)).toBe('2-Zimmer-Wohnung')
  })
})
