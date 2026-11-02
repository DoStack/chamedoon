import { getWebApp } from './telegram'

const ME_PREFIX = '/api/v1/me/'
const AUTH_URL = '/api/auth/telegram/'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(`API error ${status}`)
    this.status = status
    this.body = body
  }
}

async function parse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let body: unknown = text
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    // Not JSON: keep the text for the error.
  }
  if (!response.ok) throw new ApiError(response.status, body)
  return body as T
}

// Public reads are cached by the CDN, and any Authorization header bypasses that cache. Never send one here.
export async function publicFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (path.startsWith(ME_PREFIX)) throw new Error(`publicFetch cannot call ${path}`)
  const headers = new Headers(init.headers)
  headers.delete('Authorization')
  return parse<T>(await fetch(path, { ...init, headers }))
}

// The JWT lives in this variable only and is exchanged again on each launch.
let token: string | null = null
let exchanging: Promise<string> | null = null

function exchange(): Promise<string> {
  exchanging ??= (async () => {
    const init_data = getWebApp()?.initData
    if (!init_data) throw new ApiError(401, { detail: 'no Telegram initData' })
    const response = await fetch(AUTH_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data }),
    })
    const data = await parse<{ token: string }>(response)
    token = data.token
    return data.token
  })().finally(() => {
    exchanging = null
  })
  return exchanging
}

export async function meFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (!path.startsWith(ME_PREFIX)) throw new Error(`meFetch cannot call ${path}`)
  const send = (jwt: string) => {
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${jwt}`)
    return fetch(path, { ...init, headers })
  }
  let response = await send(token ?? (await exchange()))
  if (response.status === 401) {
    token = null
    response = await send(await exchange())
  }
  return parse<T>(response)
}

export function clearMeToken() {
  token = null
}
