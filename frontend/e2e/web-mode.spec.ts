import { expect, test, type Page } from '@playwright/test'
import { mockApi, savedCity, TORONTO } from './api-mock'
import { mockCourier } from './courier/mock'
import { answer } from '../src/mocks/courier'

// Web mode: the same app in a normal browser (no Telegram initData), when the server turns it on.
const SESSION = '/api/v1/auth/web/session/'
const session = (patch: object = {}) => ({ status: 200, body: { web_login: true, email_login: false, bot_username: 'chamedoon_home_bot', user: null, prefs: null, ...patch } })
const START = { status: 200, body: { code: 'A'.repeat(32), match: 42, poll_token: 'p'.repeat(43), bot_url: `https://t.me/chamedoon_home_bot?start=weblogin_${'A'.repeat(32)}`, expires_in: 300 } }
const PENDING = { status: 200, body: { status: 'pending', opened: false } }

async function open(page: Page, path: string, api: Parameters<typeof mockApi>[1] = {}, publicReads?: string[]) {
  await mockApi(page, { [SESSION]: session(), 'POST /api/v1/auth/web/start/': START, '/api/v1/auth/web/poll/': PENDING, ...api })
  if (!path.startsWith('/app/home')) await mockCourier(page) // registered last, so it answers /api/v1/courier/
  if (publicReads) await mockPublicCourier(page, publicReads)
  await page.goto(path)
}

// The public board answers like the signed-in API minus everything about a person (backend courier_api/public.py).
const PUBLIC_FIELDS = ['id', 'type', 'origin', 'stops', 'date', 'date_kind', 'capacity_kg', 'weight_kg', 'categories', 'created_at']
const onlyPublic = (card: Record<string, unknown>) => Object.fromEntries(PUBLIC_FIELDS.map((key) => [key, card[key]]))

async function mockPublicCourier(page: Page, reads: string[]) {
  await page.route(
    (url) => url.pathname.startsWith('/api/v1/public/courier/'),
    (route) => {
      const { pathname, search } = new URL(route.request().url())
      const path = pathname.replace('/api/v1/public/courier/', '')
      reads.push(`${path}${search}`)
      expect(route.request().method()).toBe('GET')
      const { status, body } = answer('GET', `/api/v1/courier/${path}`, search)
      let shaped = body as Record<string, unknown>
      if (status === 200 && /^requests\/\d+\/$/.test(path)) shaped = onlyPublic(shaped)
      if (status === 200 && path === 'requests/') shaped = { ...shaped, items: (shaped.items as Record<string, unknown>[]).map(onlyPublic) }
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(shaped) })
    },
  )
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

    test('courier: anonymous board, detail without contact, intro opens the login sheet', async ({ page }) => {
      const reads: string[] = []
      await open(page, '/app/', {}, reads)
      const cards = page.getByTestId('request-card')
      await expect(cards.first()).toBeVisible()
      await expect(page.getByTestId('web-signin')).toHaveCount(0)
      await expect(page.getByTestId('web-login-sheet')).toHaveCount(0)
      expect(reads.some((path) => path.startsWith('requests/?'))).toBe(true)
      expect(await noSideScroll(page)).toBe(true)
      await cards.first().click()
      await expect(page).toHaveURL(/\/app\/request\/\d+/)
      await expect(page.getByTestId('published-contact')).toHaveCount(0)
      await expect(page.getByTestId('intro-connected')).toHaveCount(0)
      await page.getByTestId('intro-ask').locator('visible=true').first().click()
      await expect(page.getByTestId('web-login-sheet')).toBeVisible()
      await expect(page.getByTestId('web-login-match')).toHaveText('42')
      expect(await noSideScroll(page)).toBe(true)
    })

    test('courier: browser Back closes the login sheet before leaving the page', async ({ page }) => {
      await open(page, '/app/', {}, [])
      await page.getByTestId('request-card').first().click()
      await expect(page).toHaveURL(/\/app\/request\/\d+/)
      await page.getByTestId('intro-ask').locator('visible=true').first().click()
      await expect(page.getByTestId('web-login-sheet')).toBeVisible()
      await page.goBack()
      await expect(page.getByTestId('web-login-sheet')).toHaveCount(0)
      await expect(page).toHaveURL(/\/app\/request\/\d+/)
      await page.goBack()
      await expect(page).toHaveURL(/\/app\/(\?.*)?$/)
    })

    test('housing: browser Back closes the login sheet before leaving the page', async ({ page }) => {
      await open(page, '/app/home/')
      await page.getByTestId('listing-card').first().click()
      await expect(page).toHaveURL(/\/app\/home\/listing\/\d+/)
      await page.getByTestId('bookmark').locator('visible=true').first().click()
      await expect(page.getByTestId('web-login-sheet')).toBeVisible()
      await page.goBack()
      await expect(page.getByTestId('web-login-sheet')).toHaveCount(0)
      await expect(page).toHaveURL(/\/app\/home\/listing\/\d+/)
    })

    test('courier: closing the sheet with its button leaves history as it was', async ({ page }) => {
      await open(page, '/app/', {}, [])
      await page.getByTestId('request-card').first().click()
      await page.getByTestId('intro-ask').locator('visible=true').first().click()
      const sheet = page.getByTestId('web-login-sheet')
      await expect(sheet).toBeVisible()
      await sheet.getByRole('button', { name: 'بستن' }).click()
      await expect(sheet).toHaveCount(0)
      await page.goBack()
      await expect(page).toHaveURL(/\/app\/(\?.*)?$/)
    })
  })
}
