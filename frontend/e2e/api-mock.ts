import type { Page, Route } from '@playwright/test'
import { META_P3 } from '../src/mocks/fixtures'
import { answer, coded, type MockResponse, type Overrides } from '../src/mocks/api'

export { coded }

// A page opened from Telegram, against the mock backend. `api` overrides answers by "METHOD /path/".
export const APP = (path = '/app/home/', extra = '') => `${path}${path.includes('?') ? '&' : '?'}tgmock=ios&tginit=signed${extra}`

export async function mockApi(page: Page, api: Overrides = {}) {
  await page.route('https://telegram.org/**', (route) => route.abort())
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'),
    async (route: Route) => {
      const { pathname } = new URL(route.request().url())
      const method = route.request().method()
      const forced = api[`${method} ${pathname}`] ?? api[pathname]
      if (forced === 'hang') return
      if (forced === 'abort') return route.abort('failed')
      const { status, body, headers, html } = (forced as MockResponse | undefined) ?? answer(method, pathname)
      if (html) return route.fulfill({ status, contentType: 'text/html', body: String(body) })
      return route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(body) })
    },
  )
  await page.route(
    // The old template pages: a path under /app/home/ with a trailing slash. The app's own paths have none.
    (url) => /^\/app\/home\/.+\/$/.test(url.pathname),
    (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>legacy</title>legacy page' }),
  )
}

// The meta of a person with P3 on. Tests turn one part off with a patch.
export const p3Meta = (patch: object = {}): MockResponse => ({ status: 200, body: { ...META_P3, ...patch } })

export const PHONE = { width: 390, height: 844 }
export const DESKTOP = { width: 1024, height: 844 }

// Sets the saved city as the app reads it on the next load.
export async function savedCity(page: Page, value: { country: string; city: string; slug: string; name_fa: string }) {
  await page.addInitScript((saved) => localStorage.setItem('chamedoon.city', JSON.stringify(saved)), value)
}

export const TORONTO = { country: 'CA', city: 'Toronto', slug: 'toronto', name_fa: 'تورنتو' }

export const VIEWPORTS = [
  ['phone', PHONE],
  ['desktop', DESKTOP],
] as const

// `SHOTS=<dir> npm run e2e` keeps one picture per state, to compare with the design board.
export async function shot(page: Page, state: string, size: string) {
  const dir = process.env.SHOTS
  if (!dir) return
  await page.waitForTimeout(350) // Sheets and toasts finish their entrance.
  await page.screenshot({ path: `${dir}/${state}-${size === 'phone' ? 'm' : 'd'}.png` })
}
