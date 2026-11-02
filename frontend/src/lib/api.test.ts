import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiFailureError, clearMeToken, isRetryable, meFetch, publicFetch, unwrap } from './api'

const json = (body: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify(body), { status, headers })
const fetchMock = vi.fn<typeof fetch>()
const authHeader = (call: number) => new Headers(fetchMock.mock.calls[call][1]?.headers).get('Authorization')
const URL_PUBLIC = '/api/v1/public/listings/1'

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  clearMeToken()
  window.Telegram = { WebApp: { initData: 'signed' } }
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('outcomes', () => {
  it('returns data on success', async () => {
    fetchMock.mockResolvedValue(json({ id: 1 }))
    await expect(publicFetch(URL_PUBLIC)).resolves.toEqual({ ok: true, data: { id: 1 }, status: 200 })
  })

  it('returns the API code and keeps detail out of the result', async () => {
    fetchMock.mockResolvedValue(json({ code: 'listing_closed', detail: 'secret text' }, 410))
    const result = await publicFetch(URL_PUBLIC)
    expect(result).toMatchObject({ ok: false, code: 'listing_closed', status: 410 })
    expect(JSON.stringify(result)).not.toContain('secret text')
  })

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [400, 'bad_request'],
    [429, 'rate_limited'],
    [500, 'server_error'],
    [504, 'server_error'],
  ])('maps status %i without a code to %s', async (status, code) => {
    fetchMock.mockResolvedValue(json({ detail: 'raw' }, status))
    await expect(publicFetch(URL_PUBLIC)).resolves.toMatchObject({ ok: false, code, status })
  })

  it('treats a Vercel HTML error page as server_error', async () => {
    fetchMock.mockResolvedValue(new Response('<html>FUNCTION_INVOCATION_FAILED</html>', { status: 502 }))
    await expect(publicFetch(URL_PUBLIC)).resolves.toMatchObject({ code: 'server_error', status: 502 })
  })

  it('ignores a code that is not a stable snake_case value', async () => {
    fetchMock.mockResolvedValue(json({ code: 'Something broke at line 3' }, 500))
    await expect(publicFetch(URL_PUBLIC)).resolves.toMatchObject({ code: 'server_error' })
  })

  it('reads Retry-After', async () => {
    fetchMock.mockResolvedValue(json({ code: 'rate_limited' }, 429, { 'Retry-After': '30' }))
    await expect(publicFetch(URL_PUBLIC)).resolves.toMatchObject({ code: 'rate_limited', retryAfterSeconds: 30 })
  })

  it('returns bad_response for a 2xx that is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>login</html>', { status: 200 }))
    await expect(publicFetch(URL_PUBLIC)).resolves.toMatchObject({ code: 'bad_response' })
  })

  it('accepts an empty success body', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(publicFetch(URL_PUBLIC)).resolves.toEqual({ ok: true, data: null, status: 204 })
  })

  it('returns network_error when fetch fails online', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(publicFetch(URL_PUBLIC)).resolves.toEqual({ ok: false, code: 'network_error' })
  })

  it('returns offline when the browser is offline', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(publicFetch(URL_PUBLIC)).resolves.toEqual({ ok: false, code: 'offline' })
  })

  const hang: typeof fetch = (_url, init) =>
    new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('', 'AbortError'))))

  it('returns timeout when the response never comes', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(hang)
    const pending = publicFetch(URL_PUBLIC, { timeoutMs: 1000 })
    await vi.advanceTimersByTimeAsync(1000)
    await expect(pending).resolves.toEqual({ ok: false, code: 'timeout' })
  })

  it('returns aborted when the caller cancels, without logging', async () => {
    fetchMock.mockImplementation(hang)
    const controller = new AbortController()
    const pending = publicFetch(URL_PUBLIC, { signal: controller.signal })
    controller.abort()
    await expect(pending).resolves.toEqual({ ok: false, code: 'aborted' })
    expect(console.warn).not.toHaveBeenCalled()
  })

  it('logs detail for failures', async () => {
    fetchMock.mockResolvedValue(json({ code: 'listing_closed', detail: 'why' }, 410))
    await publicFetch(URL_PUBLIC)
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining('api_failure'), expect.objectContaining({ code: 'listing_closed', detail: 'why' }))
  })
})

describe('publicFetch', () => {
  it('never sends Authorization, even when the caller passes one, or after login', async () => {
    fetchMock.mockImplementation(async () => json({ token: 'jwt' }))
    await meFetch('/api/v1/me/prefs')
    fetchMock.mockClear()
    await publicFetch(URL_PUBLIC, { headers: { Authorization: 'Bearer x' } })
    expect(authHeader(0)).toBeNull()
  })

  it('refuses /me paths', () => {
    expect(() => publicFetch('/api/v1/me/prefs')).toThrow('cannot call')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('meFetch', () => {
  it('exchanges initData once and reuses the JWT', async () => {
    fetchMock.mockResolvedValueOnce(json({ token: 'jwt' })).mockImplementation(async () => json({ ok: true }))
    await Promise.all([meFetch('/api/v1/me/a'), meFetch('/api/v1/me/b')])
    await meFetch('/api/v1/me/c')
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/telegram/')).toHaveLength(1)
    expect(fetchMock.mock.calls[0][1]?.body).toBe(JSON.stringify({ init_data: 'signed' }))
    expect(authHeader(3)).toBe('Bearer jwt')
  })

  it('exchanges again once on 401', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ token: 'old' }))
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ token: 'new' }))
      .mockResolvedValueOnce(json({ ok: true }))
    await expect(meFetch('/api/v1/me/a')).resolves.toMatchObject({ ok: true, data: { ok: true } })
    expect(authHeader(3)).toBe('Bearer new')
  })

  it('stops after a second 401', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ token: 'a' }))
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({ token: 'b' }))
      .mockResolvedValueOnce(json({ code: 'token_expired' }, 401))
    await expect(meFetch('/api/v1/me/a')).resolves.toMatchObject({ code: 'token_expired' })
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('returns the exchange failure, with no call to /me', async () => {
    fetchMock.mockResolvedValueOnce(json({ code: 'init_data_expired' }, 401))
    await expect(meFetch('/api/v1/me/a')).resolves.toMatchObject({ code: 'init_data_expired' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('returns bad_response when the exchange has no token', async () => {
    fetchMock.mockResolvedValueOnce(json({}))
    await expect(meFetch('/api/v1/me/a')).resolves.toMatchObject({ code: 'bad_response' })
  })

  it('refuses paths outside /me, so the JWT cannot go elsewhere', async () => {
    await expect(meFetch('/api/v1/public/x')).rejects.toThrow('cannot call')
    await expect(meFetch('https://evil.example/api/v1/me/x')).rejects.toThrow('cannot call')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('returns no_telegram_session without initData', async () => {
    window.Telegram = { WebApp: {} }
    await expect(meFetch('/api/v1/me/a')).resolves.toEqual({ ok: false, code: 'no_telegram_session' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('query helpers', () => {
  it('unwrap throws a typed error whose message is only the code', async () => {
    fetchMock.mockResolvedValue(json({ code: 'listing_closed', detail: 'raw' }, 410))
    const error = await unwrap(publicFetch(URL_PUBLIC)).catch((e: ApiFailureError) => e)
    expect(error).toBeInstanceOf(ApiFailureError)
    expect((error as ApiFailureError).message).toBe('listing_closed')
    expect((error as ApiFailureError).failure.status).toBe(410)
  })

  it('retries only transport and server failures', () => {
    const error = (code: string) => new ApiFailureError({ ok: false, code })
    for (const code of ['offline', 'network_error', 'timeout', 'server_error']) expect(isRetryable(error(code))).toBe(true)
    for (const code of ['listing_closed', 'rate_limited', 'not_found']) expect(isRetryable(error(code))).toBe(false)
    expect(isRetryable(new Error('boom'))).toBe(false)
  })
})
