import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const ROW = { country: 'CA', city: 'Toronto', slug: 'toronto', name_fa: 'تورنتو' }

// Telegram takes only letters, digits, _ and - in a CloudStorage key.
const VALID_CLOUD_KEY = /^[A-Za-z0-9_-]{1,128}$/

function stubTelegram(stored: Record<string, string> = {}) {
  const setItem = vi.fn((key: string, value: string) => {
    stored[key] = value
  })
  const getItem = vi.fn((key: string, done: (error: unknown, value?: string) => void) => done(null, stored[key] ?? ''))
  window.Telegram = { WebApp: { isVersionAtLeast: () => true, CloudStorage: { getItem, setItem } } }
  return { setItem, getItem }
}

describe('the saved city in CloudStorage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
  })
  afterEach(() => {
    delete window.Telegram
  })

  it('writes under a key Telegram takes, and keeps the device key', async () => {
    const { setItem } = stubTelegram()
    const { saveCity } = await import('./city')
    await saveCity(ROW)
    expect(setItem.mock.calls[0][0]).toBe('chamedoon_city')
    expect(setItem.mock.calls[0][0]).toMatch(VALID_CLOUD_KEY)
    expect(JSON.parse(localStorage.getItem('chamedoon.city')!)).toEqual(ROW)
  })

  it('reads from the same key on another device', async () => {
    const { getItem } = stubTelegram({ chamedoon_city: JSON.stringify(ROW) })
    const { getSavedCity, restoreSavedCity } = await import('./city')
    expect(getSavedCity()).toBeNull()
    await restoreSavedCity()
    expect(getItem.mock.calls[0][0]).toBe('chamedoon_city')
    expect(getSavedCity()).toEqual(ROW)
  })

  it('the device copy answers first, with no cloud call', async () => {
    localStorage.setItem('chamedoon.city', JSON.stringify(ROW))
    const { getItem } = stubTelegram()
    const { getSavedCity, restoreSavedCity } = await import('./city')
    await restoreSavedCity()
    expect(getItem).not.toHaveBeenCalled()
    expect(getSavedCity()).toEqual(ROW)
  })
})
