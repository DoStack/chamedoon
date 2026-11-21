import { expect, test, type Page } from '@playwright/test'
import { mockApi, savedCity, TORONTO } from './api-mock'
import { mockCourier } from './courier/mock'

// Web mode: the same app in a normal browser (no Telegram initData), when the server turns it on.
const SESSION = '/api/v1/auth/web/session/'
const session = (patch: object = {}) => ({ status: 200, body: { web_login: true, email_login: false, bot_username: 'chamedoon_home_bot', user: null, prefs: null, ...patch } })
const START = { status: 200, body: { code: 'A'.repeat(32), match: 42, poll_token: 'p'.repeat(43), bot_url: `https://t.me/chamedoon_home_bot?start=weblogin_${'A'.repeat(32)}`, expires_in: 300 } }
const PENDING = { status: 200, body: { status: 'pending', opened: false } }

async function open(page: Page, path: string, api: Parameters<typeof mockApi>[1] = {}) {
  await mockApi(page, { [SESSION]: session(), 'POST /api/v1/auth/web/start/': START, '/api/v1/auth/web/poll/': PENDING, ...api })
  if (!path.startsWith('/app/home')) await mockCourier(page) // registered last, so it answers /api/v1/courier/
  await page.goto(path)
}

const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)

for (const [size, viewport] of [
  ['phone', { width: 390, height: 844 }],
  ['desktop', { width: 1440, height: 900 }],
] as const) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('anonymous list, detail, and the login sheet on save', async ({ page }) => {
      await open(page, '/app/home/')
      const cards = page.getByTestId('listing-card')
      await expect(cards.first()).toBeVisible()
      await expect(page.getByTestId('outside-screen')).toHaveCount(0)
      expect(await noSideScroll(page)).toBe(true)
      await cards.first().click()
      await expect(page).toHaveURL(/\/app\/home\/listing\/\d+/)
      await expect(page.getByTestId('outside-screen')).toHaveCount(0)
      await page.goBack()
      await page.getByTestId('bookmark').first().click()
      const sheet = page.getByTestId('web-login-sheet')
      await expect(sheet).toBeVisible()
      await expect(page.getByTestId('web-login-match')).toHaveText('42')
      await expect(page.getByTestId('toast')).toHaveCount(0) // nothing is "marked" before sign-in
      await expect(sheet.getByRole('link', { name: /ربات چمدون/ })).toHaveAttribute('href', START.body.bot_url)
      if (size === 'desktop') await expect(page.getByTestId('web-login-qr')).toBeVisible()
      expect(await noSideScroll(page)).toBe(true)
    })

    test('poll success signs in and the action goes on', async ({ page }) => {
      let polls = 0
      const user = { id: 9, telegram_user_id: 7000001, first_name: 'Ali', telegram_username: 'ali', has_telegram: true, email: null }
      await mockApi(page, { [SESSION]: session(), 'POST /api/v1/auth/web/start/': START })
      await page.route('**/api/v1/auth/web/poll/', (route) => {
        expect(route.request().headers()['x-poll-token']).toBe(START.body.poll_token)
        polls += 1
        return route.fulfill({ json: polls < 2 ? PENDING.body : { status: 'ok', user, prefs: {} } })
      })
      const writes: string[] = []
      await page.route(/\/api\/v1\/me\/saved\/\d+\/$/, (route) => {
        writes.push(`${route.request().method()} csrf=${route.request().headers()['x-csrftoken'] !== undefined} auth=${route.request().headers()['authorization'] !== undefined}`)
        return route.fulfill({ json: { listing_id: 1, state: 'active', card: null, closed_reason: null, note: '', saved_at: new Date().toISOString() } })
      })
      await page.goto('/app/home/')
      await page.getByTestId('bookmark').first().click()
      await expect(page.getByTestId('web-login-sheet')).toBeVisible()
      await expect(page.getByTestId('web-login-sheet')).toHaveCount(0, { timeout: 10_000 })
      await expect.poll(() => writes.length).toBeGreaterThan(0)
      expect(writes[0]).toBe('PUT csrf=true auth=false')
    })

    test('flag off: the usual open-in-Telegram screen', async ({ page }) => {
      await open(page, '/app/home/', { [SESSION]: session({ web_login: false }) })
      await expect(page.getByTestId('outside-screen')).toBeVisible()
    })

    test('courier: sign-in first, then the app', async ({ page }) => {
      await open(page, '/app/')
      await expect(page.getByTestId('web-signin')).toBeVisible()
      await page.getByTestId('web-signin').getByRole('button').click()
      await expect(page.getByTestId('web-login-match')).toHaveText('42')
      expect(await noSideScroll(page)).toBe(true)
    })
  })
}
