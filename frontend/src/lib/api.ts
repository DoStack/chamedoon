import { report } from './report'
import { getWebApp } from './telegram'

const ME_PREFIX = '/api/v1/me/'
const AUTH_URL = '/api/auth/telegram/'
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
  | 'no_telegram_session' // outside Telegram, or initData is empty

// `string & {}` until the generated contract lists every API code. Screens switch on this, never on text.
export type ApiCode = ClientCode | (string & {})

export type ApiFailure = { ok: false; code: ApiCode; status?: number; retryAfterSeconds?: number }
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
  report('api_failure', { ...failure, detail })
  return { ok: false, ...failure }
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
      return isJson ? { ok: true, data: body as T, status } : fail({ code: 'bad_response', status })
    }
    const { code, detail } = (isJson && body ? body : {}) as { code?: unknown; detail?: unknown }
    return fail(
      {
        code: typeof code === 'string' && API_CODE.test(code) ? code : codeFromStatus(status),
        status,
        retryAfterSeconds: readRetryAfter(response),
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
  return request<T>(path, { ...init, headers })
}

// The JWT lives in this variable only and is exchanged again on each launch.
let token: string | null = null
let exchanging: Promise<ApiResult<string>> | null = null

function exchange(): Promise<ApiResult<string>> {
  exchanging ??= (async (): Promise<ApiResult<string>> => {
    const init_data = getWebApp()?.initData
    if (!init_data) return fail({ code: 'no_telegram_session' })
    const result = await request<{ token?: string }>(AUTH_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data }),
    })
    if (!result.ok) return result
    if (!result.data?.token) return fail({ code: 'bad_response', status: result.status })
    token = result.data.token
    return { ok: true, data: token, status: result.status }
  })().finally(() => {
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
    const exchanged = await exchange()
    if (!exchanged.ok) return exchanged
    jwt = exchanged.data
  }
  const result = await send(jwt)
  if (result.ok || result.status !== 401) return result

  // Expired during the session. A 401 means the server did not act, so sending again is safe.
  token = null
  const exchanged = await exchange()
  return exchanged.ok ? send(exchanged.data) : exchanged
}

export function clearMeToken() {
  token = null
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

const RETRYABLE: ReadonlySet<ApiCode> = new Set<ClientCode>(['offline', 'network_error', 'timeout', 'server_error'])

export function isRetryable(error: unknown): boolean {
  return error instanceof ApiFailureError && RETRYABLE.has(error.failure.code)
}
