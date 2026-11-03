import type { Page, Route } from '@playwright/test'
import { answer, coded, fx, type MockResponse } from '../../src/mocks/courier'

export { coded, fx }

export const COURIER = (path = '', extra = '') => `/app/v2/courier/${path}${path.includes('?') ? '&' : '?'}tgmock=ios&tginit=signed${extra}`

// Keys: "METHOD path/" or "path/", relative to /api/v1/courier/. A function answers by the request, for flows that change state.
type Forced = MockResponse | 'abort' | 'hang' | ((route: Route) => MockResponse | 'abort' | 'hang')
export type CourierOverrides = Record<string, Forced>

export async function mockCourier(page: Page, api: CourierOverrides = {}) {
  await page.route('https://telegram.org/**', (route) => route.abort())
  const calls: Array<{ method: string; path: string; query: string; body: unknown; headers: Record<string, string> }> = []
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/courier/'),
    async (route: Route) => {
      const { pathname, search } = new URL(route.request().url())
      const path = pathname.replace('/api/v1/courier/', '')
      const method = route.request().method()
      calls.push({ method, path, query: search, body: route.request().postDataJSON(), headers: route.request().headers() })
      let forced = api[`${method} ${path}`] ?? api[path]
      if (typeof forced === 'function') forced = forced(route)
      if (forced === 'hang') return
      if (forced === 'abort') return route.abort('failed')
      const { status, body, headers, html } = (forced as MockResponse | undefined) ?? answer(method, pathname, search)
      if (html) return route.fulfill({ status, contentType: 'text/html', body: String(body) })
      return route.fulfill({ status, contentType: 'application/json', headers, body: status === 204 ? '' : JSON.stringify(body) })
    },
  )
  await page.route(
    (url) => url.pathname.startsWith('/app/courier/'),
    (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>legacy</title>legacy page' }),
  )
  return calls
}

export const PHONE = { width: 390, height: 844 }
export const DESKTOP = { width: 1024, height: 844 }
export const VIEWPORTS = [
  ['phone', PHONE],
  ['desktop', DESKTOP],
] as const

export function noSidewaysScroll(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
}
