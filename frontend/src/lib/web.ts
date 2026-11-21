// Web mode: the same app in a normal browser, outside Telegram. Small on purpose: it ships in the first load of both
// apps, while the login sheet itself (screens/web-login.tsx) loads only when someone needs it.
//
// The session is the server's HttpOnly cookie (`chm_web`): the page never holds a token. Calls that change something
// send Django's CSRF token back from the `csrftoken` cookie.
import { useSyncExternalStore } from 'react'

export type WebUser = {
  id: number
  telegram_user_id: number | null
  first_name: string
  telegram_username: string | null
  has_telegram: boolean
  email: string | null
}

export type WebSession = {
  web_login: boolean
  email_login: boolean
  bot_username: string
  user: WebUser | null
  prefs: Record<string, unknown> | null
}

// `telegram`: this action needs a Telegram account (housing contact): an email-only person is asked to connect one.
type Ask = { reason: string; telegram: boolean }
type State = { web: boolean; session: WebSession | null; ask: Ask | null }

let state: State = { web: false, session: null, ask: null }
const listeners = new Set<() => void>()
let waiting: Array<(ok: boolean) => void> = []

function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  listeners.forEach((listener) => listener())
}

export function subscribeWeb(listener: () => void) {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}

export function useWeb() {
  return useSyncExternalStore(subscribeWeb, () => state)
}

export const isWeb = () => state.web
export const webUser = () => state.session?.user ?? null
export const webSession = () => state.session

export function csrfToken() {
  const found = document.cookie.split('; ').find((part) => part.startsWith('csrftoken='))
  return found ? decodeURIComponent(found.slice('csrftoken='.length)) : ''
}

// Asked once at boot when there is no Telegram initData. Web mode is on only when the server says so: with the flag off
// the app shows its usual "open in Telegram" screen.
export async function loadWebSession(): Promise<WebSession | null> {
  try {
    const response = await fetch('/api/v1/auth/web/session/', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
    if (!response.ok) return null
    const session = (await response.json()) as WebSession
    if (!session || typeof session.web_login !== 'boolean') return null
    set({ session, web: session.web_login })
    if (session.web_login) document.documentElement.classList.add('web-mode')
    return session
  } catch {
    return null
  }
}

export function setWebUser(user: WebUser | null, prefs: Record<string, unknown> | null = null) {
  if (!state.session) return
  set({ session: { ...state.session, user, prefs: user ? prefs : null } })
}

// An action that needs an account opens the login sheet; the promise says whether the person signed in.
export function requestLogin(reason = '', { telegram = false } = {}): Promise<boolean> {
  const user = webUser()
  if (user && (!telegram || user.has_telegram)) return Promise.resolve(true)
  if (!state.web) return Promise.resolve(false)
  return new Promise((resolve) => {
    waiting.push(resolve)
    if (!state.ask) set({ ask: { reason, telegram } })
  })
}

// True in a browser when this person has no Telegram account yet (email sign-in).
export const needsTelegram = () => state.web && Boolean(webUser()) && !webUser()!.has_telegram

export function finishLogin(ok: boolean) {
  const done = waiting
  waiting = []
  set({ ask: null })
  done.forEach((resolve) => resolve(ok))
}

export async function logout() {
  try {
    await fetch('/api/v1/auth/web/logout/', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrfToken() } })
  } finally {
    setWebUser(null)
  }
}
