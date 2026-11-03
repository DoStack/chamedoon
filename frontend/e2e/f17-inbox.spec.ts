import { expect, test, type Page } from '@playwright/test'
import { ACCOUNT, NOTIFICATIONS, TORONTO as CARDS, listing } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const LIST = '/api/v1/me/notifications/'
const READ = '/api/v1/me/notifications/read/'
const rows = (page: Page) => page.getByTestId('inbox-row')
const dots = (page: Page) => page.getByTestId('unread-dot')
const toast = (page: Page) => page.getByTestId('toast')
const row = (page: Page, kind: string) => page.locator(`[data-testid="inbox-row"][data-kind="${kind}"]`).first()

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = '/app/v2/inbox') {
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), ...api })
  await page.goto(APP(path))
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('F17#1: the account bell counts every notification and opens the inbox', async ({ page }) => {
      await open(page, {}, '/app/v2/account')
      await expect(page.getByTestId('bell-count')).toHaveText('۳')
      await expect(page.getByTestId('bell')).toHaveAttribute('aria-label', 'اعلان‌ها، ۳ تازه')
      await shot(page, 'F17-01-account', size)
      await page.getByTestId('bell').click()
      await expect(page.getByTestId('inbox-screen')).toBeVisible()
    })

    test('no unread: the bell has no count', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': { status: 200, body: { ...ACCOUNT, counts: { ...ACCOUNT.counts, unread: 0 } } } }, '/app/v2/account')
      await expect(page.getByTestId('bell')).toHaveAttribute('aria-label', 'اعلان‌ها')
      await expect(page.getByTestId('bell-count')).toHaveCount(0)
    })

    test('F17#2: the list, each kind in its words, with three unread', async ({ page }) => {
      await open(page)
      await expect(rows(page)).toHaveCount(NOTIFICATIONS.results.length)
      await expect(dots(page)).toHaveCount(3)
      await expect(row(page, 'alert_match')).toContainText('۳ آگهی تازه برای «۱ خوابه · نورث یورک»')
      await expect(row(page, 'listing_live')).toContainText('آگهی شما منتشر شد: آپارتمان ۱ خوابه')
      await expect(row(page, 'listing_rejected')).toContainText('آگهی شما رد شد: اتاق در نورث یورک')
      await expect(row(page, 'listing_rejected')).toContainText('قیمت نوشته نشده است')
      await expect(row(page, 'saved_closed')).toContainText('آگهی نشان‌شده بسته شد: استودیو · داون‌تاون')
      await expect(row(page, 'listing_expired')).toContainText('آگهی شما بسته شد، چون تأیید نشد.')
      await expect(row(page, 'listing_expired')).toContainText('اتاق · وان')
      await expect(row(page, 'listing_held')).toContainText('آگهی شما در انتظار بررسی است')
      await expect(page.getByTestId('main-nav')).toHaveCount(0)
      await shot(page, 'F17-02-inbox', size)
    })

    test('F17#3: a saved listing that closed opens closed, with no contact', async ({ page }) => {
      const closed = CARDS.find((card) => card.id === 104)!
      await open(page, { [`/api/v1/public/listings/${closed.id}/`]: { status: 200, body: listing(closed, { state: 'closed', closed_reason: 'expired' }) } })
      await row(page, 'saved_closed').click()
      await expect(page.getByTestId('closed-note')).toBeVisible()
      await expect(page.getByTestId('actions')).toHaveCount(0)
      await shot(page, 'F17-03-closed', size)
    })

    test('F17#4: read all, at once', async ({ page }) => {
      await open(page)
      const sent: unknown[] = []
      await page.route(`**${READ}`, async (route) => {
        sent.push(route.request().postDataJSON())
        return route.fulfill({ json: { unread: 0 } })
      })
      await page.getByTestId('read-all').click()
      await expect(dots(page)).toHaveCount(0)
      await expect(toast(page)).toContainText('همه‌ی اعلان‌ها خوانده شد')
      await expect(page.getByTestId('read-all')).toHaveCount(0)
      expect(sent).toEqual([{}])
      await shot(page, 'F17-04-all-read', size)
    })

    test('read all fails: the dots come back and it offers a retry', async ({ page }) => {
      await open(page, { [`POST ${READ}`]: coded(500, 'server_error') })
      await page.getByTestId('read-all').click()
      await expect(toast(page).filter({ hasText: 'ذخیره نشد' })).toBeVisible()
      await expect(dots(page)).toHaveCount(3)
      await shot(page, 'F17-read-failed', size)
    })

    test('F17#5: no notifications', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: { status: 200, body: { results: [], unread: 0, next_cursor: null } } })
      await expect(page.getByTestId('inbox-empty')).toContainText('اعلانی ندارید')
      await shot(page, 'F17-05-empty', size)
      await page.getByTestId('inbox-empty').getByRole('button', { name: 'گوش به زنگ جدید' }).click()
      await expect(page.getByTestId('count-bar')).toBeVisible()
    })

    test('an alert line opens that alert, and marks itself read', async ({ page }) => {
      await open(page)
      const sent: unknown[] = []
      await page.route(`**${READ}`, async (route) => {
        sent.push(route.request().postDataJSON())
        return route.fulfill({ json: { unread: 2 } })
      })
      await row(page, 'alert_match').click()
      await expect(page).toHaveURL(/\/alerts\/11/)
      await expect(page.getByTestId('alert-listings-screen')).toBeVisible()
      expect(sent).toEqual([{ ids: [1] }])
    })

    test('a rejected listing goes to the old page until P4', async ({ page }) => {
      await open(page)
      await row(page, 'listing_rejected').click()
      await page.waitForURL('**/app/home/mine/?*')
    })

    test('a line whose target is gone shows F02#4', async ({ page }) => {
      const gone = { ...NOTIFICATIONS.results[1], target_state: 'gone', listing_id: null }
      await open(page, { [`GET ${LIST}`]: { status: 200, body: { results: [gone], unread: 1, next_cursor: null } } })
      await rows(page).first().click()
      await expect(page.getByTestId('listing-gone')).toContainText('این آگهی دیگر نیست')
    })

    test('an alert line whose alert is gone shows F16#12', async ({ page }) => {
      const gone = { ...NOTIFICATIONS.results[0], alert_id: null, target_state: 'gone' }
      await open(page, { [`GET ${LIST}`]: { status: 200, body: { results: [gone], unread: 1, next_cursor: null } } })
      await rows(page).first().click()
      await expect(page.getByTestId('alert-gone')).toBeVisible()
    })

    test('load fails, then retry works', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: coded(500, 'server_error') })
      await expect(page.getByTestId('inbox-failed')).toContainText('بارگذاری نشد')
      await shot(page, 'F17-load-failed', size)
      await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(rows(page).first()).toBeVisible()
    })

    test('many: the next page follows, and its failure offers a retry', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: { status: 200, body: { ...NOTIFICATIONS, next_cursor: '30' } } })
      await page.route(`**${LIST}?cursor=30`, (route) => route.fulfill({ status: 500, json: { code: 'server_error', detail: 'log only' } }))
      await expect(page.getByTestId('more-failed')).toBeVisible({ timeout: 10_000 })
    })

    test('Back returns to the account', async ({ page }) => {
      await open(page, {}, '/app/v2/account')
      await page.getByTestId('bell').click()
      await expect(page.getByTestId('inbox-screen')).toBeVisible()
      await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('account-screen')).toBeVisible()
    })

    test('slow: a skeleton under the title', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: 'hang' })
      await expect(page.getByTestId('inbox-loading')).toBeVisible()
      await expect(page.getByRole('heading', { name: 'اعلان‌ها' })).toBeVisible()
    })
  })
}
