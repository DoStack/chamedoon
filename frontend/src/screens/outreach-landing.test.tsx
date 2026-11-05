import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OwnerStatsCard } from '@/components/owner-stats'
import { clearMeToken, setLinkSource } from '@/lib/api'
import { resetOutreachLanding } from '@/lib/outreach'
import { answer } from '@/mocks/api'
import { OUTREACH_FORWARDED, OUTREACH_LANDING, OWNER_STATS } from '@/mocks/fixtures'
import type { WebApp } from '@/lib/telegram'
import { OutreachLandingLayer } from './outreach-landing'

const SRC = 'o-0123456789abcdef'
const fetchMock = vi.fn<typeof fetch>()
const requestWriteAccess = vi.fn((callback?: (granted: boolean) => void) => callback?.(true))
const openTelegramLink = vi.fn()

// Answers by the shared mock, except where a test sets the landing.
function serve(landing: unknown = OUTREACH_LANDING) {
  fetchMock.mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://x').pathname
    const method = init?.method ?? 'GET'
    const { status, body } = path === '/api/v1/me/outreach/landing/' ? { status: 200, body: landing } : answer(method, path)
    return new Response(status === 204 ? null : JSON.stringify(body), { status })
  })
}

const calls = (path: string) => fetchMock.mock.calls.filter(([input, init]) => String(input).startsWith(path) && init?.method === 'POST')

beforeEach(() => {
  fetchMock.mockReset()
  requestWriteAccess.mockClear()
  openTelegramLink.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  clearMeToken()
  resetOutreachLanding()
  setLinkSource('')
  window.Telegram = {
    WebApp: { platform: 'ios', version: '8.0', initData: 'signed', isVersionAtLeast: () => true, requestWriteAccess, openTelegramLink } as WebApp,
  }
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('outreach landing', () => {
  it('a recipient with an alert: shows it, turns it on, then offers the bot', async () => {
    serve()
    setLinkSource(SRC)
    render(<OutreachLandingLayer />)

    expect(await screen.findByText('گوش‌به‌زنگ آماده است')).toBeTruthy()
    expect(screen.getByTestId('outreach-alert-facts').textContent).toContain('تورنتو')
    expect(screen.getByTestId('outreach-alert-facts').textContent).toContain('۱٬۶۰۰')
    expect(screen.getByTestId('outreach-match-count').textContent).toContain('۴')
    const landing = calls('/api/v1/me/outreach/landing/')
    expect(landing).toHaveLength(1)
    expect(JSON.parse(String(landing[0][1]?.body))).toEqual({ src: SRC })

    // Asked once, and the yes is reported, without holding the screen.
    expect(requestWriteAccess).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(calls('/api/v1/me/outreach/write-access/')).toHaveLength(1))

    await act(async () => fireEvent.click(screen.getByTestId('outreach-confirm')))
    expect(await screen.findByTestId('outreach-done')).toBeTruthy()
    expect(calls('/api/v1/me/outreach/alerts/9101/confirm/')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('outreach-bot'))
    expect(openTelegramLink).toHaveBeenCalledWith(OUTREACH_LANDING.bot_url)
  })

  it('a forwarded link: the public page alone', async () => {
    serve(OUTREACH_FORWARDED)
    setLinkSource(SRC)
    render(<OutreachLandingLayer />)

    await waitFor(() => expect(calls('/api/v1/me/outreach/landing/')).toHaveLength(1))
    await act(async () => {})
    expect(screen.queryByTestId('outreach-alert')).toBeNull()
    expect(requestWriteAccess).not.toHaveBeenCalled()
    expect(calls('/api/v1/me/outreach/write-access/')).toHaveLength(0)
  })

  it('a link that is not from outreach: no call at all', async () => {
    serve()
    setLinkSource('share')
    render(<OutreachLandingLayer />)
    await act(async () => {})
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId('outreach-alert')).toBeNull()
  })

  it('an alert already on: nothing to show', async () => {
    serve({ ...OUTREACH_LANDING, alert: { ...OUTREACH_LANDING.alert!, active: true }, ask_write_access: false })
    setLinkSource(SRC)
    render(<OutreachLandingLayer />)
    await waitFor(() => expect(calls('/api/v1/me/outreach/landing/')).toHaveLength(1))
    await act(async () => {})
    expect(screen.queryByTestId('outreach-alert')).toBeNull()
  })
})

describe('owner stats card', () => {
  it('shows the three counts, this week and in total', () => {
    render(<OwnerStatsCard stats={OWNER_STATS} />)
    const row = (key: string) => screen.getByTestId(`stat-${key}`).textContent
    expect(row('views')).toBe('بازدید۱۲۴۸')
    expect(row('saves')).toBe('نشان‌کردن۲۷')
    expect(row('contacts')).toBe('اطلاعات تماس رو گرفتن۱۵')
    expect(screen.getByTestId('owner-stats').textContent).not.toContain('تماس گرفتن')
  })
})
