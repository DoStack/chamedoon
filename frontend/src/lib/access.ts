import { useSyncExternalStore } from 'react'
import { API_VERSION, type Meta } from '@/api/types'
import { type ApiCode, exchangeSession, getSession, onApiFailure, publicFetch, subscribeSession } from './api'
import { report } from './report'
import { getSavedCity } from './city'
import { readSnapshot } from './snapshot-cache'
import { resolveStart } from './start'
import { getStartParam, getWebApp } from './telegram'

export type Access =
  | { kind: 'checking' }
  | { kind: 'open' }
  | { kind: 'legacy' } // the flag is off for this person: the old pages
  | { kind: 'outside' } // F02#6
  | { kind: 'session_over' } // F02#7
  | { kind: 'blocked' } // F02#9
  | { kind: 'maintenance'; until: string | null } // F25#3, F25#9
  | { kind: 'offline' } // F01#11
  | { kind: 'failed' } // F25#2: no answer about the flag, so neither the app nor a redirect

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

// Someone on `qa_user_ids` tries switched-off features on production. Only their own exchange says so; /public/meta/ does
// not, so everyone else never hears of it. Browsing does not exchange (ADR-3), so `?startapp=qa` asks for it once and this
// device remembers until an exchange says otherwise.
const QA_KEY = 'chamedoon.qa'

function asksForQa() {
  if (getStartParam() === 'qa') return true
  try {
    return localStorage.getItem(QA_KEY) === '1'
  } catch {
    return false
  }
}

function withQa(meta: Meta): Meta {
  const qa = getSession()?.qa
  return qa ? { ...meta, features: { ...meta.features, ...qa.features }, text_search_on: qa.text_search_on, alerts_on: qa.alerts_on } : meta
}

subscribeSession(() => {
  const qa = Boolean(getSession()?.qa)
  try {
    if (qa) localStorage.setItem(QA_KEY, '1')
    else localStorage.removeItem(QA_KEY)
  } catch {
    // No storage: the link asks again next time.
  }
  if (qa && state.meta) set({ meta: withQa(state.meta) })
})

// Fails closed: only a yes from meta or the exchange opens the new app, and only a no from the exchange goes to the old
// pages. A failure says nothing about the flag: sending that person to /app/home/ would bounce them straight back here.
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
    case 'miniapp_off':
      return { kind: 'legacy' }
    default:
      return { kind: 'failed' }
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

function validMeta(meta: Meta) {
  return typeof meta?.maintenance?.on === 'boolean'
}

async function hasSavedList() {
  const city = getSavedCity()
  return city ? (await readSnapshot(city.slug)) !== null : false
}

export async function resolveAccess(): Promise<Access> {
  if (__TG_MOCK__ && location.pathname.includes('/__harness/')) return { kind: 'open' }
  if (!getWebApp()?.initData) return { kind: 'outside' }
  const meta = await publicFetch<Meta>('/api/v1/public/meta/')
  if (!meta.ok) {
    const access = accessFromFailure(meta.code)
    // F25#1: no connection, and this person's list is on the device. It is public data, and `recheck` corrects this on
    // the first answer from the server.
    return access.kind === 'offline' && (await hasSavedList()) ? { kind: 'open' } : access
  }
  if (!validMeta(meta.data)) return { kind: 'failed' }
  set({ meta: withQa(meta.data) })
  if (outdated(meta.data)) setUpdateNeeded()
  if (meta.data.maintenance.on) return { kind: 'maintenance', until: meta.data.maintenance.until }
  if (meta.data.miniapp_v2) {
    if (asksForQa()) await exchangeSession()
    return { kind: 'open' }
  }
  await resolveStart() // First, so the exchange can store the link's src.
  const exchanged = await exchangeSession()
  if (!exchanged.ok) return accessFromFailure(exchanged.code) // miniapp_off lands on the old pages.
  return exchanged.data.miniapp_v2 ? { kind: 'open' } : { kind: 'legacy' }
}

const LEGACY_AT = 'chamedoon.legacy_at'
const BOUNCE_MS = 30_000

function bouncedFromLegacy() {
  try {
    const last = Number(sessionStorage.getItem(LEGACY_AT))
    sessionStorage.setItem(LEGACY_AT, String(Date.now()))
    return Date.now() - last < BOUNCE_MS
  } catch {
    return false
  }
}

let booted = false

export async function boot() {
  set({ access: { kind: 'checking' } })
  const resolved = await resolveAccess()
  // F22. Only with the account open: otherwise the app stays Persian and follows Telegram's theme, as it always has.
  if (state.meta?.features?.account === true) await (await import('./ui-prefs')).applyStoredUi()
  // The server and the exchange disagreeing would bounce between the shell and the old pages, so a second try stops here.
  const access: Access = resolved.kind === 'legacy' && bouncedFromLegacy() ? { kind: 'failed' } : resolved
  set({ access })
  if (access.kind === 'legacy') {
    report('access_legacy')
    location.replace(legacyUrl())
  }
  if (resolved.kind === 'legacy' && access.kind === 'failed') report('access_legacy_loop')
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
  window.addEventListener('online', () => void recheck())
}

// A failed recheck changes nothing: an open session keeps going until a real answer says otherwise.
export async function recheck() {
  if (state.access.kind !== 'open' && state.access.kind !== 'maintenance') return
  const meta = await publicFetch<Meta>('/api/v1/public/meta/')
  if (!meta.ok || !validMeta(meta.data)) return
  set({ meta: withQa(meta.data) })
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
