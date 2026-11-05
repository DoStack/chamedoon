import { expect, test, type Page } from '@playwright/test'
import { ALERTS, ALERT_CITY_GONE, ALERT_PAUSED, ALERT_WANTED } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const LIST = '/api/v1/me/alerts/'
const [NOW, DAILY, OFF] = ALERTS.results
const list = (patch: object = {}) => ({ status: 200, body: { ...ALERTS, ...patch } })
const cards = (page: Page) => page.getByTestId('alert-card')
const toast = (page: Page) => page.getByTestId('toast')
const menuItems = (page: Page) => page.getByRole('menuitem')

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = '/app/v2/saved') {
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), ...api })
  await page.goto(APP(path))
}

function patches(page: Page, id: number, answer: (body: Record<string, unknown>) => { status: number; json: unknown }) {
  const sent: Array<Record<string, unknown>> = []
  void page.route(`**${LIST}${id}/`, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    sent.push(body)
    const { status, json } = answer(body)
    return route.fulfill({ status, json })
  })
  return sent
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('F16#1: the list, with the count against the cap', async ({ page }) => {
      await open(page)
      await expect(cards(page)).toHaveCount(3)
      await expect(page.getByTestId('saved-tabs').getByRole('tab', { name: 'گوش به زنگ (۲ از ۵)' })).toHaveAttribute('data-state', 'active')
      await expect(cards(page).nth(0)).toContainText('۱ خوابه · نورث یورک · تا ۲٬۰۰۰')
      await expect(cards(page).nth(0)).toContainText('تورنتو · مبله')
      await expect(cards(page).nth(0).getByTestId('alert-new')).toContainText('۳ جدید')
      await expect(cards(page).nth(1).getByRole('button', { name: 'روزانه' })).toHaveAttribute('aria-pressed', 'true')
      await expect(cards(page).nth(2)).toContainText('خاموش · پیامی نمی‌آید')
      await expect(cards(page).nth(2).getByTestId('alert-delivery')).toHaveCount(0)
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await shot(page, 'F16-01-list', size)
    })

    test('F16#2: the menu of an active alert', async ({ page }) => {
      await open(page)
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await expect(menuItems(page)).toHaveText(['ویرایش', 'مکث ۱ هفته', 'خاموش', 'حذف'])
      await shot(page, 'F16-02-menu', size)
    })

    test('F16#3: edit saves the filters and delivery', async ({ page }) => {
      await open(page)
      const sent = patches(page, NOW.id, () => ({ status: 200, json: { ...NOW, delivery: 'daily' } }))
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'ویرایش' }).click()
      const sheet = page.getByTestId('alert-edit')
      await expect(sheet.getByTestId('edit-chips').getByRole('button')).toHaveText(['۱ خوابه', 'تا ۲٬۰۰۰', 'نورث یورک', 'مبله', 'فیلتر'])
      await shot(page, 'F16-03-edit', size)
      await sheet.getByTestId('edit-chips').getByRole('button', { name: 'مبله' }).click()
      await sheet.getByRole('tab', { name: 'روزانه، ساعت ۹' }).click()
      await sheet.getByRole('button', { name: 'ذخیره' }).click()
      await expect(sheet).toHaveCount(0)
      expect(sent[0].delivery).toBe('daily')
      expect(sent[0].filters).toEqual({ types: ['bed1'], max: 2000, unpriced: true, areas: ['نورث یورک'] })
    })

    test('F16#3: «فیلتر» opens F06 and its choice comes back to the sheet', async ({ page }) => {
      await open(page)
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'ویرایش' }).click()
      await page.getByTestId('alert-edit').getByRole('button', { name: 'فیلتر' }).click()
      await expect(page.getByTestId('filter-sheet')).toBeVisible()
      await page.getByTestId('filter-sheet').getByRole('button', { name: /نمایش/ }).click()
      await expect(page.getByTestId('alert-edit')).toBeVisible()
    })

    test('F16#3 to F15#5: filters that match another alert show it', async ({ page }) => {
      await open(page)
      patches(page, NOW.id, () => ({ status: 409, json: { code: 'alert_duplicate', detail: 'log only', duplicate_id: DAILY.id } }))
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'ویرایش' }).click()
      await page.getByTestId('alert-edit').getByRole('button', { name: 'ذخیره' }).click()
      const sheet = page.getByTestId('alert-duplicate')
      await expect(sheet).toContainText('این گوش به زنگ را از قبل دارید')
      await expect(sheet).toContainText('اتاق · ریچموند هیل · تا ۱٬۰۰۰')
    })

    test('F16#4: pause for a week, and undo', async ({ page }) => {
      await open(page)
      const sent = patches(page, NOW.id, (body) => ({ status: 200, json: body.alert_state === 'paused' ? { ...NOW, alert_state: 'paused', paused_until: '2026-11-09T12:00:00Z', new_count: 0 } : NOW }))
      await page.route(`**${LIST}`, (route) => route.fulfill({ json: sent.at(-1)?.alert_state === 'paused' ? { ...ALERTS, results: [{ ...NOW, alert_state: 'paused', paused_until: '2026-11-09T12:00:00Z', new_count: 0 }, DAILY, OFF] } : ALERTS }))
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'مکث ۱ هفته' }).click()
      await expect(toast(page)).toContainText('گوش به زنگ تا ۹ نوامبر مکث شد')
      await expect(cards(page).nth(0)).toContainText('مکث تا ۹ نوامبر')
      await expect(cards(page).nth(0).getByTestId('alert-delivery')).toHaveCount(0)
      await shot(page, 'F16-04-paused', size)
      await toast(page).getByRole('button', { name: 'برگردان' }).click()
      await expect.poll(() => sent.at(-1)?.alert_state).toBe('active')
      await expect(cards(page).nth(0).getByTestId('alert-delivery')).toBeVisible()
    })

    test('F16#5: delete asks first', async ({ page }) => {
      const gone: string[] = []
      await open(page)
      await page.route(`**${LIST}`, (route) => route.fulfill({ json: gone.length ? { ...ALERTS, results: [NOW, DAILY] } : ALERTS }))
      await page.route(`**${LIST}${OFF.id}/`, (route) => {
        if (route.request().method() === 'DELETE') gone.push(route.request().url())
        return route.fulfill({ status: 204 })
      })
      await cards(page).nth(2).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'حذف' }).click()
      const confirm = page.getByTestId('alert-delete')
      await expect(confirm).toContainText('این گوش به زنگ حذف شود؟')
      await expect(confirm).toContainText('استودیو · داون‌تاون · سابلت')
      await shot(page, 'F16-05-delete', size)
      await confirm.getByRole('button', { name: 'انصراف' }).click()
      expect(gone).toEqual([])
      await expect(cards(page)).toHaveCount(3)
      await cards(page).nth(2).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'حذف' }).click()
      await confirm.getByRole('button', { name: 'حذف' }).click()
      await expect(cards(page)).toHaveCount(2)
      expect(gone).toHaveLength(1)
    })

    test('F16#6: new listings of an alert, under its filters, and the count clears', async ({ page }) => {
      await open(page)
      const viewed: string[] = []
      await page.route(`**${LIST}${NOW.id}/viewed/`, (route) => {
        viewed.push(route.request().method())
        return route.fulfill({ status: 204 })
      })
      await cards(page).nth(0).getByTestId('alert-new').click()
      await expect(page.getByTestId('alert-count')).toContainText('۳ آگهی تازه')
      await expect(page.getByTestId('alert-filters')).toContainText('نورث یورک')
      await expect(page.getByTestId('listing-card')).toHaveCount(3)
      await expect.poll(() => viewed).toEqual(['POST'])
      await shot(page, 'F16-06-new', size)
    })

    test('F16#7: no alerts yet', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: list({ results: [], active_count: 0 }) })
      // D2: with none it opens on the bookmarks, and this tab is one tap away.
      await expect(page.getByTestId('saved-screen')).toBeVisible()
      await page.getByTestId('saved-tabs').getByRole('tab').first().click()
      await expect(page.getByTestId('alerts-empty')).toContainText('هنوز گوش به زنگی ندارید')
      await expect(page.getByTestId('saved-tabs').getByRole('tab', { name: 'گوش به زنگ (۰ از ۵)' })).toBeVisible()
      await shot(page, 'F16-07-empty', size)
      await page.getByTestId('alert-new-button').click()
      await expect(page.getByTestId('count-bar')).toBeVisible()
    })

    test('F16#8: the menu of a paused alert offers «ادامه»', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: list({ results: [ALERT_PAUSED, DAILY, OFF] }) })
      await expect(cards(page).nth(0)).toContainText('مکث تا ۹ نوامبر')
      await cards(page).nth(0).getByTestId('alert-menu').click()
      await expect(menuItems(page)).toHaveText(['ویرایش', 'ادامه', 'خاموش', 'حذف'])
      await shot(page, 'F16-08-menu-paused', size)
    })

    test('F16#9: the menu of an alert that is off offers «روشن کردن»', async ({ page }) => {
      await open(page)
      await cards(page).nth(2).getByTestId('alert-menu').click()
      await expect(menuItems(page)).toHaveText(['ویرایش', 'روشن کردن', 'حذف'])
      await shot(page, 'F16-09-menu-off', size)
    })

    test('F16#10: turning on at the cap rolls back and says so', async ({ page }) => {
      await open(page)
      patches(page, OFF.id, () => ({ status: 409, json: { code: 'alert_cap', detail: 'log only' } }))
      await cards(page).nth(2).getByTestId('alert-menu').click()
      await menuItems(page).filter({ hasText: 'روشن کردن' }).click()
      const sheet = page.getByTestId('alert-cap')
      await expect(sheet).toContainText('سقف ۵ گوش به زنگ پر شده است')
      await shot(page, 'F16-10-cap', size)
      await sheet.getByRole('button', { name: 'بستن' }).click()
      await expect(sheet).toHaveCount(0)
      await expect(cards(page).nth(2)).toContainText('خاموش · پیامی نمی‌آید')
    })

    test('F16#11: from a wanted post, and a city that left', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: list({ results: [NOW, ALERT_WANTED, ALERT_CITY_GONE], active_count: 2 }) })
      await expect(cards(page).nth(1)).toContainText('از آگهی دنبال خانه‌ی شما')
      await expect(cards(page).nth(1)).toContainText('اتاق یا استودیو · نورث یورک، ریچموند هیل · تا ۱٬۶۰۰')
      await expect(cards(page).nth(1).getByTestId('alert-delivery')).toHaveCount(0)
      await expect(cards(page).nth(2)).toContainText('این شهر دیگر در چمدون نیست')
      await expect(cards(page).nth(2)).toContainText('Calgary')
      await shot(page, 'F16-11-wanted-gone', size)
      await cards(page).nth(1).getByTestId('alert-menu').click()
      await expect(menuItems(page)).toHaveText(['خاموش'])
      await page.keyboard.press('Escape')
      await cards(page).nth(2).getByTestId('alert-menu').click()
      await expect(menuItems(page)).toHaveText(['حذف'])
    })

    test('F16#12: an alert that is gone', async ({ page }) => {
      await open(page, {}, '/app/v2/alerts/999')
      await expect(page.getByTestId('alert-gone')).toContainText('این گوش به زنگ دیگر نیست')
      await shot(page, 'F16-12-gone', size)
      await page.getByRole('button', { name: 'برگشت' }).click()
      await expect(page.getByTestId('alerts-screen')).toBeVisible()
    })

    test('F16#13: the bot is closed', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: list({ bot_blocked: true }) })
      const note = page.getByTestId('bot-blocked')
      await expect(note).toContainText('ربات بسته است و پیامی نمی‌رسد.')
      await shot(page, 'F16-13-bot', size)
      await note.getByRole('button', { name: 'باز کردن ربات' }).click()
      const opened = await page.evaluate(() => (window.Telegram!.WebApp as unknown as { __opened: string[] }).__opened)
      expect(opened).toContain(ALERTS.bot_url)
    })

    test('F23#3: notifications are off, and it can turn them on', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: list({ notify_enabled: false }) })
      const note = page.getByTestId('notify-off')
      await expect(note).toContainText('اعلان‌ها خاموش است')
      await shot(page, 'F23-03-saved-off', size)
      const sent: unknown[] = []
      await page.route('**/api/v1/me/prefs/', async (route) => {
        if (route.request().method() === 'PATCH') sent.push(route.request().postDataJSON())
        return route.fulfill({ json: { notify_enabled: true } })
      })
      await note.getByRole('button', { name: 'روشن کردن' }).click()
      await expect(note).toHaveCount(0)
      expect(sent).toEqual([{ notify_enabled: true }])
    })

    test('delivery changes on the card, and a failure puts it back', async ({ page }) => {
      await open(page)
      const sent = patches(page, NOW.id, () => ({ status: 500, json: { code: 'server_error', detail: 'log only' } }))
      const daily = cards(page).nth(0).getByRole('button', { name: 'روزانه' })
      await daily.click()
      await expect(toast(page)).toContainText('ذخیره نشد')
      await expect(daily).toHaveAttribute('aria-pressed', 'false')
      expect(sent).toEqual([{ delivery: 'daily' }])
      await shot(page, 'F16-write-failed', size)
    })

    test('load fails, then retry works', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: coded(500, 'server_error') })
      await expect(page.getByTestId('alerts-failed')).toContainText('بارگذاری نشد')
      await shot(page, 'F16-load-failed', size)
      await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(cards(page)).toHaveCount(3)
    })

    test('a slow load shows skeletons with the bar and tabs in place', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: 'hang' })
      await expect(page.getByTestId('alerts-loading')).toBeVisible()
      await expect(page.getByTestId('saved-tabs')).toBeVisible()
      await expect(page.getByTestId('main-nav')).toBeVisible()
    })

    test('«آگهی‌ها» is F14, under the same tabs', async ({ page }) => {
      await open(page)
      await page.getByTestId('saved-tabs').getByRole('tab', { name: /آگهی‌ها/ }).click()
      await expect(page.getByTestId('saved-empty')).toBeVisible()
      await expect(page.getByTestId('saved-tabs')).toBeVisible()
      await expect(page).toHaveURL(/tab=ads/)
      await shot(page, 'F16-ads-tab', size)
    })

    test('sending off: alerts can be managed but no new one offered', async ({ page }) => {
      await open(page, { '/api/v1/public/meta/': p3Meta({ alerts_on: false }) })
      await expect(cards(page)).toHaveCount(3)
      await expect(page.getByTestId('alert-new-button')).toHaveCount(0)
    })

    test('the saved tab shows the sum of new listings once there is a session', async ({ page }) => {
      await open(page)
      await expect(cards(page)).toHaveCount(3)
      await expect(page.getByTestId('main-nav').getByTestId('tab-badge')).toHaveText('۴')
    })

    test('flag off mid-session: the server says so and the tabs leave', async ({ page }) => {
      await open(page, { [`GET ${LIST}`]: coded(403, 'feature_off') })
      await expect(page.getByTestId('saved-screen')).toBeVisible()
      await expect(page.getByTestId('saved-tabs')).toHaveCount(0)
    })
  })
}
