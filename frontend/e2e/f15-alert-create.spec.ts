import { expect, test, type Page } from '@playwright/test'
import { ALERTS, ALERT_SAVE } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const LIST = '/api/v1/me/alerts/'
const FILTERED = '/app/v2/?types=bed1&areas=نورث یورک&max=2000&furnished=1'
const chip = (page: Page) => page.getByTestId('alert-chip')
const sheet = (page: Page) => page.getByTestId('alert-sheet')
const toast = (page: Page) => page.getByTestId('toast')
const saved = (patch: object) => ({ status: 200, body: { ...ALERT_SAVE, ...patch } })
const none = { status: 200, body: { ...ALERTS, results: [], active_count: 0 } }

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = FILTERED) {
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), [`GET ${LIST}`]: none, ...api })
  await page.goto(APP(path))
  await expect(page.getByTestId('count-bar')).toBeVisible()
}

async function saveFromSheet(page: Page) {
  await chip(page).click()
  await sheet(page).getByRole('button', { name: 'ذخیره' }).click()
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('F15#1: the sheet shows the filters, read-only, and what it sends', async ({ page }) => {
      await open(page)
      const sent: unknown[] = []
      await page.route(`**${LIST}`, async (route) => {
        if (route.request().method() !== 'POST') return route.fallback()
        sent.push(route.request().postDataJSON())
        return route.fulfill({ status: 201, json: ALERT_SAVE })
      })
      await chip(page).click()
      await expect(sheet(page).getByLabel('نام')).toHaveValue('۱ خوابه · نورث یورک · تا ۲٬۰۰۰')
      await expect(sheet(page).getByLabel('نام')).toHaveAttribute('readonly', '')
      const tags = sheet(page).getByTestId('alert-tags')
      for (const label of ['تورنتو', '۱ خوابه', 'تا ۲٬۰۰۰', 'نورث یورک', 'مبله', 'بدون قیمت هم بیاید']) await expect(tags).toContainText(label)
      await expect(sheet(page).getByRole('tab', { name: 'به‌محض پیدا شدن' })).toHaveAttribute('data-state', 'active')
      await shot(page, 'F15-01-create', size)
      await sheet(page).getByRole('tab', { name: 'روزانه، ساعت ۹' }).click()
      await sheet(page).getByRole('button', { name: 'ذخیره' }).click()
      await expect(sheet(page)).toHaveCount(0)
      expect(sent).toEqual([{ country: 'CA', city: 'Toronto', delivery: 'daily', filters: { types: ['bed1'], max: 2000, unpriced: true, areas: ['نورث یورک'], furnished: true } }])
    })

    test('F15#2: saved, the chip turns on, and «دیدن» opens F16', async ({ page }) => {
      await open(page)
      await saveFromSheet(page)
      await expect(toast(page)).toContainText('گوش به زنگ ذخیره شد')
      await expect(chip(page)).toHaveAttribute('aria-label', 'گوش به زنگ روشن')
      await shot(page, 'F15-02-saved', size)
      await toast(page).getByRole('button', { name: 'دیدن' }).click()
      await expect(page.getByTestId('alerts-screen')).toBeVisible()
    })

    test('F15#3: the bot was never opened', async ({ page }) => {
      await open(page, { [`POST ${LIST}`]: { status: 201, body: { ...ALERT_SAVE, bot_open: false } } })
      await saveFromSheet(page)
      const bot = page.getByTestId('alert-bot')
      await expect(bot).toContainText('برای دریافت پیام، ربات را باز کنید')
      await shot(page, 'F15-03-bot', size)
      await bot.getByRole('button', { name: 'باز کردن ربات' }).click()
      const opened = await page.evaluate(() => (window.Telegram!.WebApp as unknown as { __opened: string[] }).__opened)
      expect(opened).toContain(ALERT_SAVE.bot_url)
      await expect(bot).toHaveCount(0)
      await expect(chip(page)).toHaveAttribute('aria-label', 'گوش به زنگ روشن')
    })

    test('F15#4: at the cap', async ({ page }) => {
      await open(page, { [`POST ${LIST}`]: coded(409, 'alert_cap') })
      await saveFromSheet(page)
      const cap = page.getByTestId('alert-cap')
      await expect(cap).toContainText('سقف ۵ گوش به زنگ پر شده است')
      await shot(page, 'F15-04-cap', size)
      await cap.getByRole('button', { name: 'مدیریت گوش به زنگ‌ها' }).click()
      await expect(page.getByTestId('alerts-screen')).toBeVisible()
    })

    test('F15#5: the same alert already exists', async ({ page }) => {
      const existing = ALERTS.results[0]
      await open(page, { [`POST ${LIST}`]: saved({ save_outcome: 'duplicate', alert: existing }) })
      await saveFromSheet(page)
      const duplicate = page.getByTestId('alert-duplicate')
      await expect(duplicate).toContainText('این گوش به زنگ را از قبل دارید')
      await expect(duplicate).toContainText('۱ خوابه · نورث یورک · تا ۲٬۰۰۰')
      await expect(duplicate).toContainText('تورنتو · مبله · به‌محض پیدا شدن')
      await shot(page, 'F15-05-duplicate', size)
      await duplicate.getByRole('button', { name: 'بستن' }).click()
      await expect(duplicate).toHaveCount(0)
    })

    test('F15#6: saved from an empty list, with the ways out kept', async ({ page }) => {
      await open(page, {}, '/app/v2/?types=studio&max=1000&furnished=1')
      await expect(page.getByTestId('filter-empty')).toBeVisible()
      await saveFromSheet(page)
      await expect(toast(page)).toContainText('گوش به زنگ ذخیره شد')
      await expect(page.getByTestId('count-bar')).toContainText('۰ آگهی')
      await expect(chip(page)).toHaveAttribute('aria-label', 'گوش به زنگ روشن')
      await shot(page, 'F15-06-zero', size)
    })

    test('F15#7: sending is off, so there is no chip', async ({ page }) => {
      await open(page, { '/api/v1/public/meta/': p3Meta({ alerts_on: false }) })
      await expect(chip(page)).toHaveCount(0)
      await shot(page, 'F15-07-sending-off', size)
    })

    test('F15#8: the same alert was off and is back on', async ({ page }) => {
      await open(page, { [`POST ${LIST}`]: saved({ save_outcome: 'reactivated' }) })
      await saveFromSheet(page)
      await expect(toast(page)).toContainText('گوش به زنگ دوباره روشن شد')
      await expect(chip(page)).toHaveAttribute('aria-label', 'گوش به زنگ روشن')
      await shot(page, 'F15-08-reactivated', size)
    })

    test('F15#10: only a city has a default name', async ({ page }) => {
      await open(page, {}, '/app/v2/')
      await chip(page).click()
      await expect(sheet(page).getByLabel('نام')).toHaveValue('همه‌ی آگهی‌های تورنتو')
      await expect(sheet(page).getByTestId('alert-tags')).toHaveText('تورنتو')
      await shot(page, 'F15-10-city-only', size)
    })

    test('a failed save keeps the sheet and offers a retry', async ({ page }) => {
      await open(page, { [`POST ${LIST}`]: coded(500, 'server_error') })
      await saveFromSheet(page)
      await expect(toast(page)).toContainText('ذخیره نشد')
      await expect(sheet(page)).toBeVisible()
      await shot(page, 'F15-save-failed', size)
    })

    test('the wanted tab has no chip', async ({ page }) => {
      await open(page, {}, '/app/v2/?kind=wanted')
      await expect(chip(page)).toHaveCount(0)
    })

    test('browsing alone does not start a session: the list of alerts waits', async ({ page }) => {
      const calls: string[] = []
      page.on('request', (request) => calls.push(new URL(request.url()).pathname))
      await open(page, {}, '/app/v2/')
      await expect(chip(page)).toHaveAttribute('aria-label', 'گوش به زنگ')
      expect(calls).not.toContain(LIST)
      expect(calls).not.toContain('/api/v1/auth/exchange/')
    })
  })
}
