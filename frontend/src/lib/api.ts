import type { ServerCode } from '@/api/codes.gen'
import type { Exchange, GateChannel } from '@/api/types'
import { identifyTelemetry, report } from './report'
import { getWebApp } from './telegram'

const ME_PREFIX = '/api/v1/me/'
const EXCHANGE_URL = '/api/v1/auth/exchange/'
const DEFAULT_TIMEOUT_MS = 15_000

// Codes the client produces itself, for failures that carry no API `code`.
export type ClientCode =
  | 'offline'
  | 'network_error'
  | 'timeout'
  | 'aborted'
  | 'server_error' // 5xx or a Vercel platform error
  | 'rate_limited'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'bad_request'
  | 'bad_response' // 2xx that is not JSON, or lacks what we need

// Screens switch on this, never on text. ServerCode comes from the contract, so a typo fails typecheck. At runtime a
// newer API can still send an unknown code, so every switch needs a default.
export type ApiCode = ClientCode | ServerCode

export type ApiFailure = {
  ok: false
  code: ApiCode
  status?: number
  retryAfterSeconds?: number
  joinUrl?: string // not_member
  channel?: GateChannel // not_member
  fields?: Record<string, string[]> // validation_failed
  duplicateId?: number // alert_duplicate
  duplicateOf?: number // duplicate_listing
  duplicateOwner?: 'self' | 'other' // duplicate_listing
}
export type ApiResult<T> = { ok: true; data: T; status: number } | ApiFailure

export type ApiInit = RequestInit & { timeoutMs?: number }

const API_CODE = /^[a-z][a-z0-9_]*$/

function codeFromStatus(status: number): ClientCode {
  if (status >= 500) return 'server_error'
  if (status === 429) return 'rate_limited'
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'forbidden'
  if (status === 404) return 'not_found'
  return 'bad_request'
}

function fail(failure: Omit<ApiFailure, 'ok'>, detail?: unknown): ApiFailure {
  report('api_failure', { code: failure.code, status: failure.status, detail })
  return { ok: false, ...failure }
}

type FailureListener = (failure: ApiFailure) => void
const listeners = new Set<FailureListener>()

// For failures that change the whole app (maintenance, session over, blocked), whichever call hit them first.
export function onApiFailure(listener: FailureListener) {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}

function settled<T>(result: ApiResult<T>): ApiResult<T> {
  if (!result.ok) listeners.forEach((listener) => listener(result))
  return result
}

function readRetryAfter(response: Response) {
  const seconds = Number(response.headers.get('Retry-After'))
  return Number.isFinite(seconds) && seconds > 0 ? seconds : undefined
}

// Never rejects: every outcome of the call comes back as a value. `detail` goes to the log only.
async function request<T>(path: string, { timeoutMs = DEFAULT_TIMEOUT_MS, signal, ...init }: ApiInit): Promise<ApiResult<T>> {
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const abort = () => controller.abort()
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort)

  try {
    const response = await fetch(path, { ...init, signal: controller.signal })
    const { status } = response
    const text = await response.text()
    let body: unknown
    let isJson = true
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      isJson = false
    }

    if (response.ok) {
      // A 200 with no object behind it would crash the screen reading it. 204 is the one answer with no body.
      return isJson && (body !== null || status === 204) ? { ok: true, data: body as T, status } : fail({ code: 'bad_response', status })
    }
    const { code, detail, join_url, channel, fields, duplicate_id, duplicate_of, duplicate_owner } = (isJson && body ? body : {}) as {
      code?: unknown
      detail?: unknown
      join_url?: string
      channel?: GateChannel
      fields?: Record<string, string[]>
      duplicate_id?: number
      duplicate_of?: number
      duplicate_owner?: 'self' | 'other'
    }
    return fail(
      {
        code: typeof code === 'string' && API_CODE.test(code) ? (code as ApiCode) : codeFromStatus(status),
        status,
        retryAfterSeconds: readRetryAfter(response),
        ...(join_url ? { joinUrl: join_url } : {}),
        ...(channel ? { channel } : {}),
        ...(fields ? { fields } : {}),
        ...(duplicate_id ? { duplicateId: duplicate_id } : {}),
        ...(duplicate_of ? { duplicateOf: duplicate_of } : {}),
        ...(duplicate_owner ? { duplicateOwner: duplicate_owner } : {}),
      },
      detail,
    )
  } catch {
    if (timedOut) return fail({ code: 'timeout' })
    if (signal?.aborted) return { ok: false, code: 'aborted' }
    return fail({ code: navigator.onLine === false ? 'offline' : 'network_error' })
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}

// Public reads are cached by the CDN, and any Authorization header bypasses that cache. Never send one here.
export function publicFetch<T>(path: string, init: ApiInit = {}): Promise<ApiResult<T>> {
  if (path.startsWith(ME_PREFIX)) throw new Error(`publicFetch cannot call ${path}`)
  const headers = new Headers(init.headers)
  headers.delete('Authorization')
  return request<T>(path, { ...init, headers }).then(settled)
}

// The JWT lives in this variable only and is exchanged again on each launch.
let token: string | null = null
let session: Exchange | null = null
let linkSource = ''
let exchanging: Promise<ApiResult<Exchange>> | null = null
const sessionListeners = new Set<() => void>()

// The link tag from the deep link. The server stores the first one per person.
export function setLinkSource(src: string) {
  linkSource = src
}

export function getSession() {
  return session
}

export function subscribeSession(listener: () => void) {
  sessionListeners.add(listener)
  return () => void sessionListeners.delete(listener)
}

export function exchangeSession(): Promise<ApiResult<Exchange>> {
  exchanging ??= (async (): Promise<ApiResult<Exchange>> => {
    const init_data = getWebApp()?.initData
    if (!init_data) return { ok: false, code: 'auth_outside' }
    const result = await request<Exchange>(EXCHANGE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(linkSource ? { init_data, src: linkSource } : { init_data }),
    })
    if (!result.ok) return result
    if (!result.data?.access_token) return fail({ code: 'bad_response', status: result.status })
    token = result.data.access_token
    session = result.data
    if (result.data.user?.telegram_user_id) identifyTelemetry(result.data.user.telegram_user_id)
    sessionListeners.forEach((listener) => listener())
    return result
  })()
    .then(settled)
    .finally(() => {
      exchanging = null
    })
  return exchanging
}

export async function meFetch<T>(path: string, init: ApiInit = {}): Promise<ApiResult<T>> {
  if (!path.startsWith(ME_PREFIX)) throw new Error(`meFetch cannot call ${path}`)
  const send = (jwt: string) => {
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${jwt}`)
    return request<T>(path, { ...init, headers })
  }

  let jwt = token
  if (!jwt) {
    const exchanged = await exchangeSession()
    if (!exchanged.ok) return exchanged
    jwt = token!
  }
  const result = await send(jwt)
  if (result.ok || result.status !== 401) return settled(result)

  // Expired during the session. A 401 means the server did not act, so sending again is safe.
  token = null
  const exchanged = await exchangeSession()
  return exchanged.ok ? send(token!).then(settled) : exchanged
}

export function clearMeToken() {
  token = null
  session = null
}

// For TanStack Query, which needs a throw to enter its error state. The message holds the code and nothing else.
export class ApiFailureError extends Error {
  readonly failure: ApiFailure

  constructor(failure: ApiFailure) {
    super(failure.code)
    this.name = 'ApiFailureError'
    this.failure = failure
  }
}

export async function unwrap<T>(call: Promise<ApiResult<T>>): Promise<T> {
  const result = await call
  if (!result.ok) throw new ApiFailureError(result)
  return result.data
}

const RETRYABLE: ReadonlySet<ApiCode> = new Set<ApiCode>(['offline', 'network_error', 'timeout', 'server_error', 'auth_unavailable', 'feed_failed'])

export function isRetryable(error: unknown): boolean {
  return error instanceof ApiFailureError && RETRYABLE.has(error.failure.code)
}
