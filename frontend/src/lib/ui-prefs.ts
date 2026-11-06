import { useSyncExternalStore } from 'react'
import type { Prefs } from '@/api/types'
import { patchPrefs } from './account'
import { setLocale, type Locale } from './locale'
import { getWebApp, isVersionAtLeast, setThemeMode, type ThemeMode } from './telegram'

// The app is Persian only for now: the en/de translations stay in code but no device, Telegram language or account choice can switch to them.
export const ACTIVE_LOCALE: Locale = 'fa'

export type UiPrefs = { locale: Locale; theme: ThemeMode }

const KEY = 'chamedoon.ui'
const LOCALES: readonly string[] = ['fa', 'en', 'de']
const THEMES: readonly string[] = ['light', 'dark', 'auto']

let current: UiPrefs = { locale: 'fa', theme: 'auto' }
const listeners = new Set<() => void>()

function parse(raw: string | null | undefined): UiPrefs | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<UiPrefs>
    if (typeof value?.locale === 'string' && LOCALES.includes(value.locale) && typeof value.theme === 'string' && THEMES.includes(value.theme)) return { locale: value.locale, theme: value.theme }
  } catch {
    // A bad stored value is the same as none.
  }
  return null
}

function readLocal() {
  try {
    return parse(localStorage.getItem(KEY))
  } catch {
    return null
  }
}

const cloud = () => (isVersionAtLeast('6.9') ? getWebApp()?.CloudStorage : undefined)

async function readCloud() {
  const storage = cloud()
  if (!storage) return null
  const raw = await new Promise<string>((resolve) => {
    const timer = setTimeout(() => resolve(''), 800)
    try {
      storage.getItem(KEY, (error, stored) => {
        clearTimeout(timer)
        resolve(error ? '' : (stored ?? ''))
      })
    } catch {
      clearTimeout(timer)
      resolve('')
    }
  })
  return parse(raw)
}

async function apply(chosen: UiPrefs) {
  const next: UiPrefs = { ...chosen, locale: ACTIVE_LOCALE }
  current = next
  setThemeMode(next.theme)
  await setLocale(next.locale)
  listeners.forEach((listener) => listener())
}

function store(chosen: UiPrefs) {
  const value = JSON.stringify({ ...chosen, locale: ACTIVE_LOCALE })
  try {
    localStorage.setItem(KEY, value)
  } catch {
    // Private mode: the choice lasts for this session.
  }
  try {
    cloud()?.setItem(KEY, value)
  } catch {
    // Same.
  }
}

// Runs at boot, only when `features.account` is on. Local first, per ADR-3: browsing does not start a session for this.
export async function applyStoredUi() {
  const stored = readLocal() ?? (await readCloud())
  await apply(stored ?? { locale: ACTIVE_LOCALE, theme: 'auto' })
}

// The account answered: another device's theme counts only when this one has not chosen. Its language is ignored.
export async function adoptServerUi(prefs: Pick<Prefs, 'locale' | 'theme'>) {
  // A language kept on the account from before also sets the language of post text the server writes; put it back to Persian.
  if (prefs.locale && prefs.locale !== ACTIVE_LOCALE) void patchPrefs({ locale: ACTIVE_LOCALE })
  if (readLocal() || !prefs.theme || prefs.theme === 'auto') return
  const next: UiPrefs = { locale: ACTIVE_LOCALE, theme: prefs.theme }
  store(next)
  await apply(next)
}

// Applies at once. Resolves false when only the server copy failed (F22: CloudStorage, then API).
export async function chooseUi(requested: Partial<UiPrefs>) {
  const { locale: _ignored, ...change } = requested
  const next = { ...current, ...change }
  store(next)
  await apply(next)
  const result = await patchPrefs(change)
  return result.ok
}

export function useUiPrefs() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => current,
  )
}
