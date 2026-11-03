import { expect, test } from '@playwright/test'
import { META, EXCHANGE } from '../src/mocks/fixtures'
import { APP, coded, mockApi, PHONE } from './api-mock'

const FLAG_OFF = { '/api/v1/public/meta/': { status: 200, body: { ...META, miniapp_v2: false } } }
const person = (miniapp_v2: boolean) => ({ 'POST /api/v1/auth/exchange/': { status: 200, body: { ...EXCHANGE, miniapp_v2 } } })

test.beforeEach(async ({ page }) => page.setViewportSize(PHONE))

test.describe('the miniapp_v2 gate fails closed', () => {
  test('flag on for everyone opens the app without an exchange', async ({ page }) => {
    let exchanged = false
    await mockApi(page)
    page.on('request', (r) => {
      if (r.url().includes('/auth/exchange/')) exchanged = true
    })
    await page.goto(APP())
    await expect(page.getByTestId('app-ready')).toBeVisible()
    expect(exchanged).toBe(false)
  })

  test('allowlisted person opens the app', async ({ page }) => {
    await mockApi(page, { ...FLAG_OFF, ...person(true) })
    await page.goto(APP())
    await expect(page.getByTestId('app-ready')).toBeVisible()
  })

  test('person not on the allowlist goes to the old pages', async ({ page }) => {
    await mockApi(page, { ...FLAG_OFF, ...person(false) })
    await page.goto(APP())
    await page.waitForURL('**/app/home/**')
    expect(new URL(page.url()).searchParams.get('tginit')).toBe('signed')
  })

  for (const [name, api] of [
    ['exchange 5xx', { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': coded(500, 'server_error') }],
    ['exchange platform error page', { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': { status: 502, body: '<html>x</html>', html: true } }],
    ['auth_unavailable', { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': coded(503, 'auth_unavailable') }],
    ['meta 5xx', { '/api/v1/public/meta/': coded(500, 'server_error') }],
    ['meta not JSON', { '/api/v1/public/meta/': { status: 200, body: '<html>sign in</html>', html: true } }],
    ['exchange without a token', { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': { status: 200, body: {} } }],
  ] as const) {
    test(`${name} goes to the old pages, never the new app`, async ({ page }) => {
      await mockApi(page, api)
      await page.goto(APP())
      await page.waitForURL('**/app/home/**')
    })
  }

  test('F01#11: no connection shows retry, and retry recovers', async ({ page }) => {
    let fail = true
    await mockApi(page)
    await page.route('**/api/v1/public/meta/', (route) => (fail ? route.abort('failed') : route.fallback()))
    await page.goto(APP())
    await expect(page.getByTestId('offline-screen')).toContainText('اتصال برقرار نشد')
    fail = false
    await page.getByRole('button', { name: 'تلاش مجدد' }).click()
    await expect(page.getByTestId('app-ready')).toBeVisible()
  })
})

test('F02#6: outside Telegram', async ({ page }) => {
  await mockApi(page)
  await page.goto('/app/v2/?tgmock=ios')
  await expect(page.getByTestId('outside-screen')).toContainText('در تلگرام باز کنید')
  await expect(page.getByRole('link', { name: 'باز کردن در تلگرام' })).toHaveAttribute('href', /t\.me/)
})

for (const code of ['session_expired', 'auth_invalid'] as const) {
  test(`F02#7: exchange answers ${code}`, async ({ page }) => {
    await mockApi(page, { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': coded(401, code) })
    await page.goto(APP())
    await expect(page.getByTestId('session-over')).toContainText('نشست شما تمام شده است')
    await page.getByRole('button', { name: 'باز کردن دوباره' }).click()
    const opened = await page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)
    expect(opened[0]).toContain('t.me/')
  })
}

test('F02#9: blocked account', async ({ page }) => {
  await mockApi(page, { ...FLAG_OFF, 'POST /api/v1/auth/exchange/': coded(403, 'user_blocked') })
  await page.goto(APP())
  await expect(page.getByTestId('blocked-screen')).toContainText('حساب شما غیرفعال است')
  await expect(page.getByRole('button', { name: 'پشتیبانی' })).toBeVisible()
})

test('F25#3: maintenance with a time, and retry reopens', async ({ page }) => {
  const until = new Date(Date.now() + 30 * 60_000).toISOString()
  let on = true
  await mockApi(page)
  await page.route('**/api/v1/public/meta/', (route) => route.fulfill({ json: { ...META, maintenance: { on, until: on ? until : null } } }))
  await page.goto(APP())
  await expect(page.getByTestId('maintenance-screen')).toContainText('در حال به‌روزرسانی هستیم')
  await expect(page.getByTestId('maintenance-screen')).toContainText('دقیقه دیگر برمی‌گردیم')
  on = false
  await page.getByRole('button', { name: 'تلاش مجدد' }).click()
  await expect(page.getByTestId('app-ready')).toBeVisible()
})

test('F25#9: maintenance with no time has no time line and no channel', async ({ page }) => {
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META, maintenance: { on: true, until: null } } } })
  await page.goto(APP())
  await expect(page.getByTestId('maintenance-screen')).not.toContainText('برمی‌گردیم')
  await expect(page.getByTestId('maintenance-screen').getByRole('button')).toHaveCount(1)
})

test('F25#5: older bundle gets the update sheet, with no way to close it', async ({ page }) => {
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META, min_api_version: 99 } } })
  await page.goto(APP())
  const sheet = page.getByTestId('update-sheet')
  await expect(sheet).toContainText('نسخه‌ی تازه آماده است')
  await expect(sheet.getByRole('button', { name: 'بستن' })).toHaveCount(0)
  await expect(sheet.getByRole('button', { name: 'بارگذاری مجدد' })).toBeVisible()
})

test('F25#5: flag off mid-session shows the update sheet on return', async ({ page }) => {
  let flag = true
  await mockApi(page, person(false))
  await page.route('**/api/v1/public/meta/', (route) => route.fulfill({ json: { ...META, miniapp_v2: flag } }))
  await page.goto(APP())
  await expect(page.getByTestId('app-ready')).toBeVisible()
  flag = false
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await expect(page.getByTestId('update-sheet')).toBeVisible()
})

test('maintenance mid-session replaces the app', async ({ page }) => {
  let on = false
  await mockApi(page)
  await page.route('**/api/v1/public/meta/', (route) => route.fulfill({ json: { ...META, maintenance: { on, until: null } } }))
  await page.goto(APP())
  await expect(page.getByTestId('app-ready')).toBeVisible()
  on = true
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await expect(page.getByTestId('maintenance-screen')).toBeVisible()
})

test('dark Telegram theme applies', async ({ page }) => {
  await mockApi(page)
  await page.goto(APP('/app/v2/', '&tgtheme=dark'))
  await expect(page.getByTestId('app-ready')).toBeVisible()
  await expect(page.locator('html')).toHaveClass(/dark/)
})
