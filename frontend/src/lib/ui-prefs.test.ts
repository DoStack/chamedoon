import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fa, formatDate, relativeTime, roomsLabel } from './format'
import { getLocale, setLocale } from './locale'
import { adoptServerUi, applyStoredUi, chooseUi } from './ui-prefs'

const patchPrefs = vi.hoisted(() => vi.fn(async () => ({ ok: true })))
vi.mock('./account', () => ({ patchPrefs }))

beforeEach(() => {
  localStorage.clear()
  patchPrefs.mockClear()
  document.documentElement.className = ''
})
afterEach(() => setLocale('fa'))

describe('applyStoredUi', () => {
  it('keeps the theme this device chose but stays Persian', async () => {
    localStorage.setItem('chamedoon.ui', JSON.stringify({ locale: 'de', theme: 'dark' }))
    await applyStoredUi()
    expect(getLocale()).toBe('fa')
    expect(document.documentElement.dir).toBe('rtl')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem("chamedoon.ui")).toContain("dark")
  })

  it('ignores a bad stored value', async () => {
    localStorage.setItem('chamedoon.ui', '{"locale":"xx"}')
    await applyStoredUi()
    expect(getLocale()).toBe('fa')
  })
})

describe('adoptServerUi', () => {
  it('takes another device’s theme but never its language', async () => {
    await adoptServerUi({ locale: 'en', theme: 'dark' })
    expect(getLocale()).toBe('fa')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(JSON.parse(localStorage.getItem('chamedoon.ui')!)).toEqual({ locale: 'fa', theme: 'dark' })
    expect(patchPrefs).toHaveBeenCalledWith({ locale: 'fa' })
  })

  it('leaves a Persian account alone', async () => {
    await adoptServerUi({ locale: 'fa', theme: 'auto' })
    expect(patchPrefs).not.toHaveBeenCalled()
  })

  it('a language alone from the server changes nothing', async () => {
    await adoptServerUi({ locale: 'de', theme: 'auto' })
    expect(getLocale()).toBe('fa')
    expect(localStorage.getItem('chamedoon.ui')).toBeNull()
  })
})

describe('chooseUi', () => {
  it('applies a theme at once and stores it, then tells the server only what changed', async () => {
    await chooseUi({ theme: 'dark' })
    expect(JSON.parse(localStorage.getItem('chamedoon.ui')!)).toEqual({ locale: 'fa', theme: 'dark' })
    expect(patchPrefs).toHaveBeenCalledWith({ theme: 'dark' })
  })

  it('ignores a language choice', async () => {
    await chooseUi({ locale: 'en' })
    expect(getLocale()).toBe('fa')
    expect(patchPrefs).toHaveBeenCalledWith({})
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
