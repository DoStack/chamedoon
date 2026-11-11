import { expect, test, type Page } from '@playwright/test'
import { ACCOUNT, EXCHANGE } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const URL_ = '/api/v1/me/account/'
const SUPPORT_URL = 'https://t.me/chamedoon_home_bot?start=support'
const nav = (page: Page) => page.getByTestId('main-nav')
const opened = (page: Page) => page.evaluate(() => (window.Telegram!.WebApp as unknown as { __opened: string[] }).__opened)
const MONTREAL = { country: 'CA', city: 'Montreal', slug: 'montreal', name_fa: 'مونترال' }
const BERLIN = { country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' }

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = '/app/home/account', city = TORONTO) {
  await savedCity(page, city)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), '/api/v1/auth/exchange/': { status: 200, body: EXCHANGE }, ...api })
  await page.goto(APP(path))
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('the tab opens it, instead of the old page', async ({ page }) => {
      await open(page, {}, '/app/home/')
      await nav(page).getByRole('tab', { name: 'حساب کاربری' }).click()
      await expect(page.getByTestId('account-screen')).toBeVisible()
      await expect(page).toHaveURL(/\/app\/home\/account/)
      await expect(nav(page).getByRole('tab', { name: 'حساب کاربری' })).toHaveAttribute('data-state', 'active')
    })

    test('F21#1: the account', async ({ page }) => {
      await open(page)
      await expect(page.getByTestId('account-who')).toContainText('مهسا')
      await expect(page.getByTestId('account-handle')).toHaveText('@mahsa_home')
      await expect(page.getByTestId('row-city')).toContainText('تورنتو')
      await expect(page.getByTestId('row-mine')).toContainText('۱ آگهی')
      await expect(page.getByTestId('row-saved')).toContainText('۲ گوش به زنگ · ۲ آگهی')
      await expect(page.getByTestId('row-safety')).toContainText('راهنمای ایمنی تورنتو')
      await expect(page.getByTestId('open-channel')).toContainText('کانال تورنتو')
      await expect(page.getByTestId('open-bot')).toContainText('ربات خانه')
      await shot(page, 'F21-01-account', size)
    })

    test('alerts off: the saved row counts bookmarks only', async ({ page }) => {
      await open(page, { '/api/v1/public/meta/': p3Meta({ features: { alerts: false, account: true, posting: false, smart_posting: false } }) })
      await expect(page.getByTestId('row-saved')).toContainText('۲ آگهی')
      await expect(page.getByTestId('row-saved')).not.toContainText('گوش به زنگ')
    })

    test('«آگهی‌های من» stays on the old page until P4', async ({ page }) => {
      await open(page)
      await page.getByTestId('row-mine').click()
      await page.waitForURL('**/app/home/mine/?*')
    })

    test('F21#2: the city sheet changes the city and saves it', async ({ page }) => {
      await open(page)
      const sent: unknown[] = []
      await page.route('**/api/v1/me/prefs/', async (route) => {
        if (route.request().method() === 'PATCH') sent.push(route.request().postDataJSON())
        return route.fulfill({ json: ACCOUNT.prefs })
      })
      await page.getByTestId('row-city').click()
      await expect(page.getByText('مونترال')).toBeVisible()
      await shot(page, 'F21-02-city', size)
      await page.getByText('مونترال').click()
      await expect(page.getByTestId('row-city')).toContainText('مونترال')
      await expect(page.getByTestId('row-safety')).toContainText('راهنمای ایمنی مونترال')
      expect(sent).toEqual([{ country: 'CA', city: 'Montreal' }])
    })

    test('F21#3: support opens in the bot', async ({ page }) => {
      await open(page)
      await page.getByTestId('row-support').click()
      expect(await opened(page)).toContain(SUPPORT_URL)
    })

    test('F21#4: Toronto safety, with the official site and no law stated', async ({ page }) => {
      await open(page, {}, '/app/home/safety')
      await expect(page.getByRole('heading', { name: 'راهنمای ایمنی تورنتو' })).toBeVisible()
      await expect(page.getByTestId('safety-tips')).not.toContainText('در انتاریو')
      await expect(page.getByTestId('safety-site')).toContainText('سایت LTB انتاریو')
      await expect(page.getByTestId('main-nav')).toHaveCount(0)
      await shot(page, 'F21-04-safety-toronto', size)
    })

    test('F21#5: the channel opens in Telegram', async ({ page }) => {
      await open(page)
      await page.getByTestId('open-channel').click()
      expect(await opened(page)).toContain(`https://t.me/${ACCOUNT.channel!.username}`)
    })

    test('F21#6: Montreal safety, with the official site and no law stated', async ({ page }) => {
      await open(page, {}, '/app/home/safety', MONTREAL)
      await expect(page.getByRole('heading', { name: 'راهنمای ایمنی مونترال' })).toBeVisible()
      await expect(page.getByTestId('safety-tips')).not.toContainText('ودیعه')
      await expect(page.getByTestId('safety-tips')).not.toContainText('انتاریو')
      await expect(page.getByTestId('safety-site')).toContainText('سایت TAL کبک')
      await shot(page, 'F21-06-safety-montreal', size)
    })

    test('F21#7: a city with no separate rule', async ({ page }) => {
      await open(page, {}, '/app/home/safety', BERLIN)
      await expect(page.getByRole('heading', { name: 'راهنمای ایمنی برلین' })).toBeVisible()
      await expect(page.getByTestId('safety-site')).toHaveCount(0)
      await expect(page.getByTestId('safety-tips')).toContainText('پیش از دیدن خانه پول نفرستید')
      await shot(page, 'F21-07-safety-plain', size)
    })

    test('F21#8: no username', async ({ page }) => {
      await open(page, { [URL_]: { status: 200, body: { ...ACCOUNT, user: { ...ACCOUNT.user, telegram_username: null } } } })
      await expect(page.getByTestId('account-who')).toContainText('مهسا')
      await expect(page.getByTestId('account-handle')).toHaveCount(0)
      await shot(page, 'F21-08-no-username', size)
    })

    test('F21#9: the account did not load, then retry works', async ({ page }) => {
      await open(page, { [URL_]: coded(500, 'server_error') })
      await expect(page.getByTestId('account-failed')).toContainText('اطلاعات حساب بارگذاری نشد')
      await expect(page.getByTestId('main-nav')).toBeVisible()
      await shot(page, 'F21-09-failed', size)
      await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(page.getByTestId('account-who')).toBeVisible()
    })

    test('no channel: only the bot button', async ({ page }) => {
      await open(page, { [URL_]: { status: 200, body: { ...ACCOUNT, channel: null } } })
      await expect(page.getByTestId('open-bot')).toBeVisible()
      await expect(page.getByTestId('open-channel')).toHaveCount(0)
    })

    test('the city fails to save: it says so and offers a retry', async ({ page }) => {
      await open(page, { 'PATCH /api/v1/me/prefs/': coded(500, 'server_error') })
      await page.getByTestId('row-city').click()
      await page.getByText('مونترال').click()
      await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
    })

    test('flag off mid-session: back to the list', async ({ page }) => {
      await open(page, { [URL_]: coded(403, 'feature_off') })
      await expect(page.getByTestId('count-bar')).toBeVisible()
    })

    test('slow: skeletons with the tabs in place', async ({ page }) => {
      await open(page, { [URL_]: 'hang' })
      await expect(page.getByTestId('account-loading')).toBeVisible()
      await expect(page.getByTestId('main-nav')).toBeVisible()
    })
  })
}
