import { expect, test, type Page } from '@playwright/test'
import { ACCOUNT, EXCHANGE, TORONTO as CARDS, listing } from '../src/mocks/fixtures'
import { APP, coded, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const BERLIN = { country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' }
const stored = (page: Page, value: object) => page.addInitScript((ui) => localStorage.setItem('chamedoon.ui', JSON.stringify(ui)), value)
const html = (page: Page) => page.locator('html')
const language = (page: Page) => page.getByTestId('control-language')
const theme = (page: Page) => page.getByTestId('control-theme')

async function open(page: Page, api: Parameters<typeof mockApi>[1] = {}, path = '/app/v2/account', city = TORONTO, extra = '') {
  await savedCity(page, city)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta(), '/api/v1/auth/exchange/': { status: 200, body: EXCHANGE }, ...api })
  await page.goto(APP(path, extra))
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('F22#1: Persian, right to left, with auto as the default', async ({ page }) => {
      await open(page)
      await expect(html(page)).toHaveAttribute('dir', 'rtl')
      await expect(language(page).getByRole('tab', { name: 'FA' })).toHaveAttribute('data-state', 'active')
      await expect(theme(page).getByRole('tab', { name: 'خودکار' })).toHaveAttribute('data-state', 'active')
      await shot(page, 'F22-01-fa', size)
    })

    test('F22#2: English turns the page around at once and is saved', async ({ page }) => {
      await open(page)
      const sent: unknown[] = []
      await page.route('**/api/v1/me/prefs/', async (route) => {
        if (route.request().method() === 'PATCH') sent.push(route.request().postDataJSON())
        return route.fulfill({ json: ACCOUNT.prefs })
      })
      await language(page).getByRole('tab', { name: 'EN' }).click()
      await expect(html(page)).toHaveAttribute('dir', 'ltr')
      await expect(html(page)).toHaveAttribute('lang', 'en')
      await expect(page.getByTestId('row-city')).toContainText('My city')
      await expect(page.getByTestId('row-city')).toContainText('Toronto')
      await expect(page.getByTestId('main-nav').getByRole('tab', { name: 'Account' })).toBeVisible()
      expect(sent).toEqual([{ locale: 'en' }])
      await shot(page, 'F22-02-en', size)
      await page.reload()
      await expect(html(page)).toHaveAttribute('dir', 'ltr')
    })

    test('F22#3: German, with Berlin', async ({ page }) => {
      await stored(page, { locale: 'de', theme: 'auto' })
      await open(page, {}, '/app/v2/account', BERLIN)
      await expect(html(page)).toHaveAttribute('lang', 'de')
      await expect(page.getByTestId('row-safety')).toContainText('Sicherheitstipps Berlin')
      await expect(page.getByTestId('row-city')).toContainText('Berlin')
      await shot(page, 'F22-03-de', size)
    })

    test('F22#4: dark on the list', async ({ page }) => {
      await stored(page, { locale: 'fa', theme: 'dark' })
      await open(page, {}, '/app/v2/')
      await expect(page.getByTestId('count-bar')).toBeVisible()
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
      await shot(page, 'F22-04-dark', size)
    })

    test('the theme applies at once, and auto follows Telegram', async ({ page }) => {
      await open(page, {}, '/app/v2/account', TORONTO, '&tgtheme=dark')
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
      await theme(page).getByRole('tab', { name: 'روشن' }).click()
      await expect(html(page)).not.toHaveClass(/(^|\s)dark(\s|$)/)
      await theme(page).getByRole('tab', { name: 'خودکار' }).click()
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
    })

    test('F22#5: the list in English', async ({ page }) => {
      await stored(page, { locale: 'en', theme: 'auto' })
      await open(page, {}, '/app/v2/')
      const bar = page.getByTestId('count-bar')
      await expect(bar).toContainText('listings')
      await expect(bar).toContainText('Newest')
      await expect(page.getByTestId('alert-chip')).toHaveAttribute('aria-label', 'Alert')
      await expect(page.getByTestId('filter-button')).toContainText('Filters')
      await expect(page.getByTestId('listing-card').first()).toContainText('1-bedroom apartment · 55 m²')
      await expect(page.getByTestId('listing-card').first()).toContainText('per month')
      await expect(page.getByTestId('city-button')).toContainText('Toronto')
      await shot(page, 'F22-05-en-list', size)
    })

    test('F22#6: the list in German, with the listing text left in Persian', async ({ page }) => {
      await stored(page, { locale: 'de', theme: 'auto' })
      await open(page, {}, '/app/v2/', BERLIN)
      const bar = page.getByTestId('count-bar')
      await expect(bar).toContainText('Inserate')
      await expect(page.getByTestId('alert-chip')).toHaveAttribute('aria-label', 'Suchauftrag')
      await expect(page.getByTestId('listing-card').first()).toContainText('نویکلن')
      await shot(page, 'F22-06-de-list', size)
    })

    test('F22#7: dark on the membership gate', async ({ page }) => {
      await stored(page, { locale: 'fa', theme: 'dark' })
      const id = CARDS[0].id
      await open(page, { [`/api/v1/public/listings/${id}/`]: { status: 200, body: listing(CARDS[0]) }, [`POST /api/v1/me/listings/${id}/contact/`]: coded(403, 'not_member', { join_url: 'https://t.me/+join', channel: { username: 'x', title: 'کانال چمدون - خانه · تورنتو', city: 'Toronto' } }) }, `/app/v2/listing/${id}`)
      await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
      await expect(page.getByTestId('gate-sheet')).toBeVisible()
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
      await shot(page, 'F22-07-dark-gate', size)
    })

    test('the first language comes from Telegram when it is en or de', async ({ page }) => {
      await open(page, {}, '/app/v2/', TORONTO, '&tglang=en-GB')
      await expect(html(page)).toHaveAttribute('lang', 'en')
    })

    test('any other Telegram language is Persian', async ({ page }) => {
      await open(page, {}, '/app/v2/', TORONTO, '&tglang=fr')
      await expect(html(page)).toHaveAttribute('dir', 'rtl')
    })

    test('a failed save keeps the choice and offers a retry', async ({ page }) => {
      await open(page, { 'PATCH /api/v1/me/prefs/': coded(500, 'server_error') })
      await theme(page).getByRole('tab', { name: 'تاریک' }).click()
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
      await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
    })

    test('another device’s language comes with the account', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': { status: 200, body: { ...ACCOUNT, prefs: { ...ACCOUNT.prefs, locale: 'de' } } } })
      await expect(html(page)).toHaveAttribute('lang', 'de')
    })
  })
}
