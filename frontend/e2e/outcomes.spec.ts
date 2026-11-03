import { expect, test } from '@playwright/test'
import { type Forced, forceOutcome, goOfflineIfForced } from './force-outcome'

const API = '/api/v1/public/listings/1'
const HARNESS = `/app/v2/__harness/public?tgmock=ios&path=${encodeURIComponent(API)}`
const CRASH_TITLE = 'مشکلی از طرف ما پیش آمد'

test.beforeEach(async ({ page }) => {
  await page.route('https://telegram.org/**', (route) => route.abort())
})

const CASES: Array<[string, Forced, Record<string, unknown>]> = [
  ['success', { kind: 'ok', body: { id: 1 } }, { ok: true, data: { id: 1 } }],
  ['network failure', { kind: 'network_error' }, { ok: false, code: 'network_error' }],
  ['offline', { kind: 'offline' }, { ok: false, code: 'offline' }],
  ['Vercel 502 page', { kind: 'html_error', status: 502 }, { ok: false, code: 'server_error', status: 502 }],
  ['200 that is not JSON', { kind: 'not_json' }, { ok: false, code: 'bad_response' }],
  ['API code', { kind: 'code', status: 409, code: 'listing_closed' }, { ok: false, code: 'listing_closed', status: 409 }],
  ['429', { kind: 'code', status: 429, code: 'rate_limited', retryAfter: 30 }, { ok: false, code: 'rate_limited', retryAfterSeconds: 30 }],
]

for (const [name, forced, expected] of CASES) {
  test(`fetch layer outcome: ${name}`, async ({ page }) => {
    await forceOutcome(page, `**${API}`, forced)
    await page.goto(HARNESS)
    await goOfflineIfForced(page, forced)
    await page.getByTestId('run').click()
    await expect(page.getByTestId('outcome')).not.toBeEmpty()
    const text = (await page.getByTestId('outcome').textContent())!
    expect(JSON.parse(text)).toMatchObject(expected)
    expect(text).not.toContain('log only')
    expect(text).not.toContain('FUNCTION_INVOCATION')
  })
}

test('fetch layer outcome: timeout', async ({ page }) => {
  await page.clock.install()
  await forceOutcome(page, `**${API}`, { kind: 'timeout' })
  await page.goto(HARNESS)
  await page.getByTestId('run').click()
  await page.clock.fastForward(16_000)
  await expect(page.getByTestId('outcome')).toHaveText(JSON.stringify({ ok: false, code: 'timeout' }))
})

test('fetch layer outcome: no Telegram session on /me', async ({ page }) => {
  await page.goto('/app/v2/__harness/me?tgmock=ios&path=/api/v1/me/prefs')
  await page.getByTestId('run').click()
  await expect(page.getByTestId('outside-screen')).toBeVisible()
})

test('fetch layer outcome: /me exchanges initData and sends the JWT', async ({ page }) => {
  const auth: Record<string, string | undefined> = {}
  await page.route('**/api/v1/auth/exchange/', (route) => route.fulfill({ json: { access_token: 'jwt', miniapp_v2: true } }))
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/me/'),
    (route) => {
      auth[new URL(route.request().url()).pathname] = route.request().headers().authorization
      return route.fulfill({ json: {} })
    },
  )
  await page.goto('/app/v2/__harness/me?tgmock=ios&tginit=signed&path=/api/v1/me/prefs')
  await page.getByTestId('run').click()
  await expect(page.getByTestId('outcome')).not.toBeEmpty()
  expect(auth['/api/v1/me/prefs']).toBe('Bearer jwt')
})

test('route boundary keeps the shell and hides the raw error', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/app/v2/__harness/throw?tgmock=ios')
  await expect(page.getByText(CRASH_TITLE)).toBeVisible()
  await expect(page.locator('.app-shell .app-top')).toHaveCSS('padding-top', '93px')
  await expect(page.locator('body')).not.toContainText('harness raw error text')
  const retry = page.getByRole('button', { name: 'تلاش مجدد' })
  await expect(retry).toHaveCSS('height', '44px')
  await retry.click()
  await expect(page.getByText(CRASH_TITLE)).toBeVisible()
})

test('app boundary catches a crash outside the router', async ({ page }) => {
  await page.goto('/app/v2/?tgmock=ios&tginit=signed&tgcrash=1')
  await expect(page.getByText(CRASH_TITLE)).toBeVisible()
  await expect(page.locator('body')).not.toContainText('forced app crash')
  await expect(page.getByRole('button', { name: 'تلاش مجدد' })).toBeVisible()
})
