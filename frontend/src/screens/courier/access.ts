import { useSyncExternalStore } from 'react'
import { getWebApp } from '@/lib/telegram'
import { exchangeSession, metaFetch, onCourierFailure, type CourierFailure } from './api'
import { API_VERSION, type Meta } from './types'

// Nothing of the app renders until the flag says yes. The courier API has its own flag: anyone it refuses goes to the old pages.
export type Access = 'checking' | 'open' | 'outside' | 'session_over' | 'blocked' | 'maintenance' | 'offline' | 'failed' | 'legacy'
export type Update = 'new_version' | 'flag_off' | null

let state: { access: Access; update: Update } = { access: 'checking', update: null }
const subscribers = new Set<() => void>()

function publish(next: Partial<typeof state>) {
  state = { ...state, ...next }
  subscribers.forEach((listener) => listener())
}

export const legacyUrl = () => `/app/courier/${location.search}${location.hash}`

function offlineOrFailed(failure: CourierFailure): Access {
  return failure.code === 'offline' || failure.code === 'network_error' || failure.code === 'timeout' ? 'offline' : 'failed'
}

export async function boot() {
  publish({ access: 'checking' })
  if (!getWebApp()?.initData) return publish({ access: 'outside' })
  const meta = await metaFetch<Meta>('meta/')
  if (!meta.ok) return publish({ access: offlineOrFailed(meta) })
  if (meta.data.maintenance) return publish({ access: 'maintenance' })
  if (meta.data.min_api_version > API_VERSION) publish({ update: 'new_version' })
  const exchanged = await exchangeSession()
  if (!exchanged.ok) {
    switch (exchanged.code) {
      case 'auth_required':
      case 'auth_invalid':
        return publish({ access: 'outside' })
      case 'session_expired':
      case 'token_expired':
        return publish({ access: 'session_over' })
      case 'user_blocked':
        return publish({ access: 'blocked' })
      case 'maintenance':
        return publish({ access: 'maintenance' })
      default:
        return publish({ access: offlineOrFailed(exchanged) })
    }
  }
  if (!exchanged.data.courier_v2) {
    publish({ access: 'legacy' })
    location.replace(legacyUrl())
    return
  }
  publish({ access: 'open' })
}

// Whichever call hits one of these first changes the whole app, while the screen behind a sheet stays where it is.
onCourierFailure((failure) => {
  switch (failure.code) {
    case 'maintenance':
      return publish({ access: 'maintenance' })
    case 'user_blocked':
      return publish({ access: 'blocked' })
    case 'session_expired':
    case 'token_expired':
    case 'auth_invalid':
    case 'auth_required':
      if (state.access === 'open') publish({ access: 'session_over' })
      return
    case 'flag_off':
      return publish({ update: 'flag_off' })
    case 'version_mismatch':
      return publish({ update: 'new_version' })
  }
})

export function useAccess() {
  return useSyncExternalStore(
    (listener) => {
      subscribers.add(listener)
      return () => void subscribers.delete(listener)
    },
    () => state,
  )
}
