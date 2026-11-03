import { useSyncExternalStore } from 'react'
import { API_VERSION, type Meta } from '@/api/types'
import { type ApiCode, exchangeSession, getSession, onApiFailure, publicFetch } from './api'
import { report } from './report'
import { resolveStart } from './start'
import { getWebApp } from './telegram'

export type Access =
  | { kind: 'checking' }
  | { kind: 'open' }
  | { kind: 'legacy' } // the flag is off for this person: the old pages
  | { kind: 'outside' } // F02#6
  | { kind: 'session_over' } // F02#7
  | { kind: 'blocked' } // F02#9
  | { kind: 'maintenance'; until: string | null } // F25#3, F25#9
  | { kind: 'offline' } // F01#11

// Where a person without the flag goes: the same place on the old pages. Search and hash carry Telegram's launch data
// and the start param.
export function legacyUrl() {
  const listing = location.pathname.match(/^\/app\/v2\/listing\/(\d+)/)
  return `/app/home/${listing ? `${listing[1]}/` : ''}${location.search}${location.hash}`
}

type Update = 'new_version' | 'flag_off'
type State = { access: Access; update: Update | null; meta: Meta | null }
let state: State = { access: { kind: 'checking' }, update: null, meta: null }
const listeners = new Set<() => void>()

function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  listeners.forEach((listener) => listener())
}

// Fails closed: only a yes from meta or the exchange opens the new app. Any other answer leaves the old pages in charge,
// except where an old page could not load either (offline) or would be wrong (signed out, blocked, maintenance).
export function accessFromFailure(code: ApiCode): Access {
  switch (code) {
    case 'maintenance':
      return { kind: 'maintenance', until: state.meta?.maintenance.until ?? null }
    case 'auth_outside':
      return { kind: 'outside' }
    case 'session_expired':
    case 'auth_invalid':
    case 'auth_required':
      return { kind: 'session_over' }
    case 'user_blocked':
      return { kind: 'blocked' }
    case 'offline':
    case 'network_error':
    case 'timeout':
      return { kind: 'offline' }
    default:
      return { kind: 'legacy' }
  }
}

// flag_off wins: its way out goes to the old pages, where a reload could land on a 404.
export function setUpdateNeeded(update: Update = 'new_version') {
  if (state.update !== update && state.update !== 'flag_off') set({ update })
}

function outdated(meta: Meta) {
  if (meta.min_api_version > API_VERSION) return true
  // A deploy changes build_id. Once per session, since a CDN can hold the old shell for a moment after a reload.
  if (__BUILD_ID__ !== 'dev' && meta.build_id !== 'dev' && meta.build_id !== __BUILD_ID__) {
    const seen = `chamedoon.update.${meta.build_id}`
    try {
      if (sessionStorage.getItem(seen)) return false
      sessionStorage.setItem(seen, '1')
    } catch {
      return false
    }
    return true
  }
  return false
}

export async function resolveAccess(): Promise<Access> {
  if (__TG_MOCK__ && location.pathname.includes('/__harness/')) return { kind: 'open' }
  if (!getWebApp()?.initData) return { kind: 'outside' }
  const meta = await publicFetch<Meta>('/api/v1/public/meta/')
  if (!meta.ok) return accessFromFailure(meta.code)
  set({ meta: meta.data })
  if (outdated(meta.data)) setUpdateNeeded()
  if (meta.data.maintenance.on) return { kind: 'maintenance', until: meta.data.maintenance.until }
  if (meta.data.miniapp_v2) return { kind: 'open' }
  await resolveStart() // First, so the exchange can store the link's src.
  const exchanged = await exchangeSession()
  if (!exchanged.ok) return accessFromFailure(exchanged.code) // miniapp_off lands on the old pages.
  return exchanged.data.miniapp_v2 ? { kind: 'open' } : { kind: 'legacy' }
}

let booted = false

export async function boot() {
  set({ access: { kind: 'checking' } })
  const access = await resolveAccess()
  set({ access })
  if (access.kind === 'legacy') {
    report('access_legacy')
    location.replace(legacyUrl())
  }
  if (booted) return
  booted = true
  onApiFailure(({ code }) => {
    if (code === 'maintenance' || code === 'user_blocked' || code === 'session_expired' || code === 'auth_invalid' || code === 'auth_outside') {
      set({ access: accessFromFailure(code) })
    }
    // The flag went off for this person during the session (F25#5).
    if (code === 'miniapp_off' && state.access.kind === 'open') setUpdateNeeded('flag_off')
  })
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void recheck()
  })
}

// A failed recheck changes nothing: an open session keeps going until a real answer says otherwise.
export async function recheck() {
  if (state.access.kind !== 'open' && state.access.kind !== 'maintenance') return
  const meta = await publicFetch<Meta>('/api/v1/public/meta/')
  if (!meta.ok) return
  set({ meta: meta.data })
  if (meta.data.maintenance.on) {
    set({ access: { kind: 'maintenance', until: meta.data.maintenance.until } })
    return
  }
  if (state.access.kind === 'maintenance') set({ access: { kind: 'open' } })
  if (outdated(meta.data)) setUpdateNeeded()
  // Off for everyone: this person may still be on the allowlist, and the exchange's miniapp_off lands in the listener.
  if (!meta.data.miniapp_v2) await exchangeSession()
}

export function useAccess() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => state,
  )
}

export function getMeta() {
  return state.meta
}

export function hasSession() {
  return getSession() !== null
}
