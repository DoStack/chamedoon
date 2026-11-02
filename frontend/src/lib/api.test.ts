import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, clearMeToken, meFetch, publicFetch } from './api'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const fetchMock = vi.fn<typeof fetch>()
const authHeader = (call: number) => new Headers(fetchMock.mock.calls[call][1]?.headers).get('Authorization')

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  clearMeToken()
  window.Telegram = { WebApp: { initData: 'signed' } }
})
afterEach(() => vi.unstubAllGlobals())

describe('publicFetch', () => {
  it('never sends Authorization, even when the caller passes one, or after login', async () => {
    fetchMock.mockImplementation(async () => json({ token: 'jwt' }))
    await meFetch('/api/v1/me/prefs')
    fetchMock.mockClear()
    await publicFetch('/api/v1/public/listings/1', { headers: { Authorization: 'Bearer x' } })
    expect(authHeader(0)).toBeNull()
  })

  it('refuses /me paths', async () => {
    await expect(publicFetch('/api/v1/me/prefs')).rejects.toThrow('cannot call')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('throws ApiError with the parsed body', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'gone' }, 404))
    const error = await publicFetch('/api/v1/public/listings/1').catch((e: ApiError) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, body: { detail: 'gone' } })
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
    await expect(meFetch('/api/v1/me/a')).resolves.toEqual({ ok: true })
    expect(authHeader(3)).toBe('Bearer new')
  })

  it('refuses paths outside /me, so the JWT cannot go elsewhere', async () => {
    await expect(meFetch('/api/v1/public/x')).rejects.toThrow('cannot call')
    await expect(meFetch('https://evil.example/api/v1/me/x')).rejects.toThrow('cannot call')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('fails without initData', async () => {
    window.Telegram = { WebApp: {} }
    await expect(meFetch('/api/v1/me/a')).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
