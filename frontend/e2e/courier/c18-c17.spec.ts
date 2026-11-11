import { expect, test } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C18 and C17 on ${size}`, () => {
    test.use({ viewport })

    test('C17.1 guide, RTL, no sideways scroll, four tabs', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('guide'))
      await expect(page.getByRole('heading', { name: 'راهنما' })).toBeVisible()
      await expect(page.getByTestId('courier-nav').getByRole('tab')).toHaveCount(4)
      expect(await page.evaluate(() => getComputedStyle(document.querySelector('[data-testid="guide"]')!).direction)).toBe('rtl')
      expect(await noSidewaysScroll(page)).toBe(true)
      await expect(page.getByTestId('pending-badge')).toHaveText('۱')
    })

    test('C17.2 prohibited sheet shows the server list', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('guide'))
      await page.getByTestId('prohibited-open').click()
      await expect(page.getByTestId('prohibited-sheet')).toContainText(fx('guide').prohibited[0])
      await expect(page.getByTestId('prohibited-sheet')).toContainText(fx('guide').prohibited_footer)
    })

    test('C17.2 prohibited list fails with a retry, and the guide stays', async ({ page }) => {
      await mockCourier(page, { 'guide/': coded(500, 'server_error') })
      await page.goto(COURIER('guide'))
      await page.getByTestId('prohibited-open').click()
      await expect(page.getByTestId('prohibited-failed')).toBeVisible({ timeout: 10_000 })
      await expect(page.getByRole('heading', { name: 'راهنما', includeHidden: true })).toBeAttached()
    })

    test('C18.3 maintenance from meta', async ({ page }) => {
      await mockCourier(page, { 'meta/': { status: 200, body: { ...fx('meta'), maintenance: true } } })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('maintenance')).toContainText('در حال به‌روزرسانی هستیم')
    })

    test('C18.3 maintenance mid-session', async ({ page }) => {
      await mockCourier(page, { 'me/': coded(503, 'maintenance') })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('maintenance')).toBeVisible()
    })

    test('C18.2 server error at the start, with no status or platform text on screen', async ({ page }) => {
      await mockCourier(page, { 'meta/': { status: 502, body: '<html>FUNCTION_INVOCATION_FAILED</html>', html: true } })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('server-error')).toContainText('مشکلی از طرف ما پیش آمد')
      await expect(page.locator('body')).not.toContainText('502')
      await expect(page.locator('body')).not.toContainText('FUNCTION')
    })

    test('C18.1 offline at the start, retry recovers', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'meta/': () => (fail ? 'abort' : { status: 200, body: fx('meta') }) })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('offline-screen')).toBeVisible()
      fail = false
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(page.getByRole('heading', { name: 'راهنما' })).toBeVisible()
    })

    test('C18.7 outside Telegram', async ({ page }) => {
      await mockCourier(page)
      await page.goto('/app/guide')
      await expect(page.getByTestId('outside')).toContainText('باربری در تلگرام باز می‌شود')
    })

    test('C18.7 invalid initData', async ({ page }) => {
      await mockCourier(page, { 'auth/exchange/': coded(401, 'auth_invalid') })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('outside')).toBeVisible()
    })

    test('C18.10 session expired at exchange', async ({ page }) => {
      await mockCourier(page, { 'auth/exchange/': coded(401, 'session_expired') })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('session-over')).toContainText('نشست شما تمام شد')
    })

    test('C18.10 token expired: one silent refresh, the screen never changes', async ({ page }) => {
      let first = true
      const calls = await mockCourier(page, {
        'me/': () => {
          if (first) {
            first = false
            return coded(401, 'token_expired')
          }
          return { status: 200, body: fx('me') }
        },
      })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('pending-badge')).toBeVisible()
      await expect(page.getByTestId('session-over')).toHaveCount(0)
      expect(calls.filter((call) => call.path === 'auth/exchange/').length).toBeGreaterThanOrEqual(2)
    })

    test('C18.10 refresh fails too', async ({ page }) => {
      let exchanges = 0
      await mockCourier(page, {
        'auth/exchange/': () => (++exchanges === 1 ? { status: 200, body: fx('exchange') } : coded(401, 'session_expired')),
        'me/': coded(401, 'token_expired'),
      })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('session-over')).toBeVisible()
    })

    test('C18.11 blocked', async ({ page }) => {
      await mockCourier(page, { 'auth/exchange/': coded(403, 'user_blocked') })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('blocked')).toContainText('دسترسی این حساب محدود شده است')
    })

    test('flag off at the start goes to the old pages and keeps the launch data', async ({ page }) => {
      await mockCourier(page, { 'auth/exchange/': { status: 200, body: { ...fx('exchange'), courier_v2: false } } })
      await page.goto(COURIER('guide'))
      await page.waitForURL(/\/app\/courier\//)
      expect(page.url()).toContain('tginit=signed')
    })

    test('C18.14 flag off mid-session: sheet with no close, the button loads the old screens', async ({ page }) => {
      await mockCourier(page, { 'me/': coded(403, 'flag_off') })
      await page.goto(COURIER('guide'))
      const sheet = page.getByTestId('update-sheet')
      await expect(sheet).toContainText('باربری به‌روز شد')
      await expect(sheet.getByRole('button', { name: 'بستن' })).toHaveCount(0)
      await sheet.getByRole('button', { name: 'بارگذاری مجدد' }).click()
      await page.waitForURL(/\/app\/courier\//)
    })

    test('C18.5 old bundle against a newer API', async ({ page }) => {
      await mockCourier(page, { 'meta/': { status: 200, body: { ...fx('meta'), min_api_version: 99 } } })
      await page.goto(COURIER('guide'))
      const sheet = page.getByTestId('update-sheet')
      await expect(sheet).toContainText('نسخه‌ی تازه آماده است')
      await expect(sheet.getByRole('button', { name: 'بستن' })).toHaveCount(0)
    })

    test('C18.5 version_mismatch from any call', async ({ page }) => {
      await mockCourier(page, { 'me/': coded(426, 'version_mismatch') })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('update-sheet')).toContainText('نسخه‌ی تازه آماده است')
    })

    test('C18.6 unknown path', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('nothing/here'))
      await expect(page.getByTestId('not-found')).toContainText('این صفحه پیدا نشد')
    })

    test('C18.4 slow answer: skeleton at once, the note after 5 s', async ({ page }) => {
      await mockCourier(page, { 'meta/': 'hang' })
      await page.goto(COURIER('guide'))
      await expect(page.getByTestId('checking')).toBeVisible()
      await expect(page.getByTestId('slow-note')).toHaveCount(0)
      await expect(page.getByTestId('slow-note')).toBeVisible({ timeout: 7_000 })
    })

    test('C18.12 old client: in-page back replaces Telegram back', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('guide', '&tgversion=6.0'))
      await expect(page.getByTestId('page-back')).toBeVisible()
    })

    test('C18.12 current client has no in-page back', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('guide'))
      await expect(page.getByRole('heading', { name: 'راهنما' })).toBeVisible()
      await expect(page.getByTestId('page-back')).toHaveCount(0)
    })

    test('C18.15 dark theme comes from Telegram', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('guide', '&tgtheme=dark'))
      await expect(page.getByRole('heading', { name: 'راهنما' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
    })
  })
}
