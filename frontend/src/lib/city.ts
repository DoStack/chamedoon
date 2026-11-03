import { useSyncExternalStore } from 'react'
import type { Cities, CityRow, CountryRow, Prefs } from '@/api/types'
import { getSession, meFetch } from './api'
import { getWebApp, isVersionAtLeast } from './telegram'

export type SavedCity = { country: string; city: string; slug: string; name_fa: string; channel?: string }

const KEY = 'chamedoon.city'

function readLocal(): SavedCity | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as SavedCity) : null
  } catch {
    return null
  }
}

let saved = readLocal()
const listeners = new Set<() => void>()

function publish(next: SavedCity | null) {
  saved = next
  listeners.forEach((listener) => listener())
}

export function getSavedCity() {
  return saved
}

export function useSavedCity() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => saved,
  )
}

function cloudStorage() {
  return isVersionAtLeast('6.9') ? getWebApp()?.CloudStorage : undefined
}

// The device copy answers at once. CloudStorage follows the person to another device, and the exchange's prefs follow
// them across a reinstall, so a first open with neither is the only one that asks.
export async function restoreSavedCity() {
  if (saved) return
  const cloud = cloudStorage()
  if (!cloud) return
  const value = await new Promise<string>((resolve) => {
    const timer = setTimeout(() => resolve(''), 800)
    try {
      cloud.getItem(KEY, (error, stored) => {
        clearTimeout(timer)
        resolve(error ? '' : (stored ?? ''))
      })
    } catch {
      clearTimeout(timer)
      resolve('')
    }
  })
  if (!value) return
  try {
    publish(JSON.parse(value) as SavedCity)
  } catch {
    // A bad stored value is the same as none.
  }
}

export function cityFromPrefs(prefs: Prefs | undefined, cities: Cities): SavedCity | null {
  if (!prefs?.city) return null
  for (const country of cities.countries) {
    const row = country.cities.find((city) => city.name === prefs.city)
    if (row) return toSaved(country, row)
  }
  return null
}

export function toSaved(country: CountryRow, row: CityRow): SavedCity {
  return { country: country.code, city: row.name, slug: row.slug, name_fa: row.name_fa, channel: row.channel.username }
}

export function findCity(cities: Cities, slug: string) {
  for (const country of cities.countries) {
    const row = country.cities.find((city) => city.slug === slug)
    if (row) return { country, row }
  }
  return null
}

// Resolves to false only when the server copy failed. The device copy never blocks the choice.
export async function saveCity(next: SavedCity): Promise<boolean> {
  publish(next)
  const value = JSON.stringify(next)
  try {
    localStorage.setItem(KEY, value)
  } catch {
    // Private mode: the choice lasts for this session.
  }
  try {
    cloudStorage()?.setItem(KEY, value)
  } catch {
    // Same.
  }
  if (!getSession()) return true
  const result = await meFetch<Prefs>('/api/v1/me/prefs/', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ country: next.country, city: next.city }),
  })
  return result.ok
}
