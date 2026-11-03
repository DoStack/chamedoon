import type { ServerCode } from '@/api/courier-codes.gen'
import { report } from '@/lib/report'
import { getWebApp } from '@/lib/telegram'
import { API_VERSION, type Exchange } from './types'

const BASE = '/api/v1/courier/'
const TIMEOUT_MS = 15_000

// Failures that carry no API `code`.
export type ClientCode = 'offline' | 'network_error' | 'timeout' | 'aborted' | 'bad_response' | 'unknown_code'
// Screens switch on this, never on text. Every switch needs a default: a newer API can send a code this bundle lacks.
export type CourierCode = ClientCode | ServerCode

export type RouteKeys = { side: 'DEMAND' | 'SUPPLY'; origin: string; destination: string }

export type CourierFailure = {
  ok: false
  code: CourierCode
  status?: number
  retryAfterSeconds?: number
  fields?: Record<string, string[]>
  route?: RouteKeys
  requestId?: number
  introId?: number | null
}
export type CourierResult<T> = { ok: true; data: T; status: number } | CourierFailure
export type CourierInit = RequestInit & { timeoutMs?: number; key?: string }

const API_CODE = /^[a-z][a-z0-9_]*$/

function codeFromStatus(status: number): CourierCode {
  if (status >= 500) return 'server_error'
  if (status === 429) return 'rate_limited'
  if (status === 401) return 'auth_required'
  if (status === 404) return 'not_found'
  return 'unknown_code'
}

function fail(failure: Omit<CourierFailure, 'ok'>, detail?: unknown): CourierFailure {
  report('courier_api_failure', { code: failure.code, status: failure.status, detail })
  return { ok: false, ...failure }
}

type Listener = (failure: CourierFailure) => void
const listeners = new Set<Listener>()

// For failures that change the whole app (maintenance, session over, blocked, flag off), whichever call hit them first.
export function onCourierFailure(listener: Listener) {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}

function settled<T>(result: CourierResult<T>): CourierResult<T> {
  if (!result.ok) listeners.forEach((listener) => listener(result))
  return result
}

// Never rejects: every outcome of the call comes back as a value. `detail` goes to the log only.
async function send<T>(path: string, { timeoutMs = TIMEOUT_MS, signal, key, ...init }: CourierInit, jwt?: string): Promise<CourierResult<T>> {
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const abort = () => controller.abort()
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort)
  const headers = new Headers(init.headers)
  headers.set('X-Courier-Api-Version', String(API_VERSION))
  if (init.body) headers.set('Content-Type', 'application/json')
  if (jwt) headers.set('Authorization', `Bearer ${jwt}`)
  if (key) headers.set('Idempotency-Key', key)
  try {
    const response = await fetch(`${BASE}${path}`, { ...init, headers, signal: controller.signal })
    const { status } = response
    const text = await response.text()
    let body: unknown
    let isJson = true
    try {
      body = text ? JSON.parse(text) : null
    } catch {
      isJson = false
    }
    if (response.ok) return isJson && (body !== null || status === 204) ? { ok: true, data: body as T, status } : fail({ code: 'bad_response', status })
    const shaped = (isJson && body ? body : {}) as { code?: unknown; detail?: unknown; fields?: Record<string, string[]>; route?: RouteKeys; request_id?: number; intro_id?: number | null }
    const retry = Number(response.headers.get('Retry-After'))
    return fail(
      {
        code: typeof shaped.code === 'string' && API_CODE.test(shaped.code) ? (shaped.code as CourierCode) : codeFromStatus(status),
        status,
        ...(Number.isFinite(retry) && retry > 0 ? { retryAfterSeconds: retry } : {}),
        ...(shaped.fields ? { fields: shaped.fields } : {}),
        ...(shaped.route ? { route: shaped.route } : {}),
        ...(shaped.request_id ? { requestId: shaped.request_id } : {}),
        ...('intro_id' in shaped ? { introId: shaped.intro_id } : {}),
      },
      shaped.detail,
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

export function metaFetch<T>(path: string) {
  return send<T>(path, {}).then(settled)
}

// The JWT lives in this variable only and is exchanged again on each launch.
let token: string | null = null
let exchanging: Promise<CourierResult<Exchange>> | null = null

export function exchangeSession(): Promise<CourierResult<Exchange>> {
  exchanging ??= (async (): Promise<CourierResult<Exchange>> => {
    const init_data = getWebApp()?.initData
    if (!init_data) return { ok: false, code: 'auth_required' }
    const result = await send<Exchange>('auth/exchange/', { method: 'POST', body: JSON.stringify({ init_data }) })
    if (!result.ok) return result
    if (!result.data?.access_token) return fail({ code: 'bad_response', status: result.status })
    token = result.data.access_token
    return result
  })()
    .then(settled)
    .finally(() => {
      exchanging = null
    })
  return exchanging
}

export async function courierFetch<T>(path: string, init: CourierInit = {}): Promise<CourierResult<T>> {
  if (!token) {
    const exchanged = await exchangeSession()
    if (!exchanged.ok) return exchanged
  }
  const result = await send<T>(path, init, token!)
  if (result.ok || result.status !== 401) return settled(result)
  // Expired during the session. A 401 means the server did not act, so sending again is safe.
  token = null
  const exchanged = await exchangeSession()
  return exchanged.ok ? send<T>(path, init, token!).then(settled) : exchanged
}

export const postJson = <T>(path: string, body?: unknown, init: CourierInit = {}) => courierFetch<T>(path, { method: 'POST', ...(body === undefined ? {} : { body: JSON.stringify(body) }), ...init })

export const patchJson = <T>(path: string, body: unknown) => courierFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body) })

// For TanStack Query, which needs a throw to enter its error state. The message holds the code and nothing else.
export class CourierError extends Error {
  readonly failure: CourierFailure

  constructor(failure: CourierFailure) {
    super(failure.code)
    this.name = 'CourierError'
    this.failure = failure
  }
}

export async function unwrap<T>(call: Promise<CourierResult<T>>): Promise<T> {
  const result = await call
  if (!result.ok) throw new CourierError(result)
  return result.data
}

export function errorCode(error: unknown): CourierCode {
  return error instanceof CourierError ? error.failure.code : 'unknown_code'
}

const RETRYABLE: ReadonlySet<CourierCode> = new Set<CourierCode>(['offline', 'network_error', 'server_error', 'auth_unavailable'])

export const isRetryable = (error: unknown) => RETRYABLE.has(errorCode(error))

// The same key on a retry, so a lost response never posts twice. New per form, not per tap.
export function newIdempotencyKey() {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`
}
