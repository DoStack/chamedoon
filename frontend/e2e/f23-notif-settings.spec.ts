import { expect, test, type Page } from '@playwright/test'
import { ACCOUNT } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const PREFS = '/api/v1/me/prefs/'
const toast = (page: Page) => page.getByTestId('toast')
const sw = (page: Page, name: string) => page.getByTestId(`switch-${name}`)
const withPrefs = (patch: object) => ({ status: 200, body: { ...ACCOUNT, prefs: { ...ACCOUNT.prefs, ...patch } } })

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = '/app/v2/settings/notifications') {
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), ...api })
  await page.goto(APP(path))
}

function recordPrefs(page: Page) {
  const sent: Array<Record<string, unknown>> = []
  void page.route(`**${PREFS}`, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    const body = route.request().postDataJSON() as Record<string, unknown>
    sent.push(body)
    return route.fulfill({ json: { ...ACCOUNT.prefs, ...body } })
  })
  return sent
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('the account row opens it, with the quiet hours in its value', async ({ page }) => {
      await open(page, {}, '/app/v2/account')
      await expect(page.getByTestId('row-notifications')).toContainText('بی‌صدا ۲۳ تا ۸')
      await page.getByTestId('row-notifications').click()
      await expect(page.getByTestId('notif-screen')).toBeVisible()
      await expect(page.getByTestId('main-nav')).toHaveCount(0)
    })

    test('F23#1: all on, and a change of quiet hours saves at once', async ({ page }) => {
      await open(page)
      const sent = recordPrefs(page)
      for (const name of ['all', 'alerts', 'news', 'saved', 'quiet']) await expect(sw(page, name)).toBeChecked()
      await expect(page.getByTestId('quiet-from')).toHaveValue('23:00')
      await expect(page.getByTestId('quiet-to')).toHaveValue('08:00')
      await page.getByTestId('quiet-to').selectOption('07:00')
      await expect(toast(page)).toContainText('ساعت بی‌صدا ذخیره شد')
      expect(sent).toEqual([{ quiet_from: '23:00', quiet_to: '07:00' }])
      await shot(page, 'F23-01-all-on', size)
    })

    test('a single switch saves at once, with no save button', async ({ page }) => {
      await open(page)
      const sent = recordPrefs(page)
      await sw(page, 'saved').click()
      await expect(sw(page, 'saved')).not.toBeChecked()
      await expect.poll(() => sent).toEqual([{ notify_saved: false }])
      await expect(page.getByRole('button', { name: 'ذخیره' })).toHaveCount(0)
    })

    test('F23#2: the master switch turns all off, keeps their values, and undo brings it back', async ({ page }) => {
      await open(page)
      const sent = recordPrefs(page)
      await sw(page, 'all').click()
      await expect(toast(page)).toContainText('همه‌ی اعلان‌ها خاموش شد')
      for (const name of ['alerts', 'news', 'saved', 'quiet']) {
        await expect(sw(page, name)).toBeDisabled()
        await expect(sw(page, name)).toBeChecked()
      }
      await expect(page.getByTestId('quiet-from')).toBeDisabled()
      await shot(page, 'F23-02-all-off', size)
      await toast(page).getByRole('button', { name: 'برگردان' }).click()
      await expect(sw(page, 'all')).toBeChecked()
      await expect(sw(page, 'alerts')).toBeEnabled()
      expect(sent).toEqual([{ notify_enabled: false }, { notify_enabled: true }])
    })

    test('F23#4: the bot is closed', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': { status: 200, body: { ...ACCOUNT, bot_blocked: true } } })
      const note = page.getByTestId('bot-blocked')
      await expect(note).toContainText('ربات بسته است و پیامی نمی‌رسد.')
      await shot(page, 'F23-04-bot', size)
      await note.getByRole('button', { name: 'باز کردن ربات' }).click()
      const opened = await page.evaluate(() => (window.Telegram!.WebApp as unknown as { __opened: string[] }).__opened)
      expect(opened).toContain(ACCOUNT.bot_url)
    })

    test('F23#5: start and end the same stay here, with the error, and nothing is sent', async ({ page }) => {
      await open(page)
      const sent = recordPrefs(page)
      await page.getByTestId('quiet-to').selectOption('23:00')
      await expect(page.getByTestId('quiet-error')).toHaveText('ساعت شروع و پایان یکی است')
      expect(sent).toEqual([])
      await shot(page, 'F23-05-same', size)
      await page.getByTestId('quiet-to').selectOption('06:00')
      await expect(page.getByTestId('quiet-error')).toHaveCount(0)
      await expect.poll(() => sent).toEqual([{ quiet_from: '23:00', quiet_to: '06:00' }])
    })

    test('quiet off sends both ends empty, and on brings them back', async ({ page }) => {
      await open(page)
      const sent = recordPrefs(page)
      await sw(page, 'quiet').click()
      await expect(page.getByTestId('quiet-from')).toBeDisabled()
      await sw(page, 'quiet').click()
      await expect.poll(() => sent).toEqual([
        { quiet_from: null, quiet_to: null },
        { quiet_from: '23:00', quiet_to: '08:00' },
      ])
    })

    test('a failed save puts the last value back and offers a retry', async ({ page }) => {
      await open(page, { [`PATCH ${PREFS}`]: coded(500, 'server_error') })
      await sw(page, 'alerts').click()
      await expect(toast(page)).toContainText('ذخیره نشد')
      await expect(sw(page, 'alerts')).toBeChecked()
      await shot(page, 'F23-save-failed', size)
    })

    test('quiet hours not set: the switch is off and the times wait', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': withPrefs({ quiet_from: null, quiet_to: null }) })
      await expect(sw(page, 'quiet')).not.toBeChecked()
      await expect(page.getByTestId('quiet-from')).toBeDisabled()
      await expect(page.getByTestId('quiet-from')).toHaveValue('23:00')
    })

    test('notifications off: the account row says so', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': withPrefs({ notify_enabled: false }) }, '/app/v2/account')
      await expect(page.getByTestId('row-notifications')).toContainText('خاموش')
    })

    test('load fails, then retry works', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': coded(500, 'server_error') })
      await expect(page.getByTestId('notif-failed')).toContainText('بارگذاری نشد')
      await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(sw(page, 'all')).toBeVisible()
    })

    test('Back returns to the account', async ({ page }) => {
      await open(page, {}, '/app/v2/account')
      await page.getByTestId('row-notifications').click()
      await expect(page.getByTestId('notif-screen')).toBeVisible()
      await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('account-screen')).toBeVisible()
    })
  })
}
