import { useSyncExternalStore } from 'react'
import { report } from './report'

export type Locale = 'fa' | 'en' | 'de'

type Messages = Record<string, Partial<Record<'en' | 'de', string>>>

// Persian is the source text and the key. A message with no translation stays Persian and is logged once (F22 rules).
let locale: Locale = 'fa'
let messages: Messages = {}
const missing = new Set<string>()
const listeners = new Set<() => void>()

export function getLocale() {
  return locale
}

export function appDir(): 'rtl' | 'ltr' {
  return locale === 'fa' ? 'rtl' : 'ltr'
}

export function t(text: string, values?: Record<string, string | number>) {
  let out = text
  if (locale !== 'fa') {
    const found = messages[text]?.[locale]
    if (found) out = found
    else if (!missing.has(`${locale}:${text}`)) {
      missing.add(`${locale}:${text}`)
      report('locale_missing_key', { locale, text })
    }
  }
  if (!values) return out
  return out.replace(/\{(\w+)\}/g, (whole, name: string) => (name in values ? String(values[name]) : whole))
}

export async function setLocale(next: Locale) {
  if (next !== 'fa' && !Object.keys(messages).length) messages = (await import('./messages')).MESSAGES
  locale = next
  document.documentElement.lang = next
  document.documentElement.dir = appDir()
  listeners.forEach((listener) => listener())
}

export function useLocale() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => locale,
  )
}
