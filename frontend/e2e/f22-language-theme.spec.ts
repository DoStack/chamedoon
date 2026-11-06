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

    test('F22#1: Persian, right to left, with auto as the default and no language picker', async ({ page }) => {
      await open(page)
      await expect(html(page)).toHaveAttribute('dir', 'rtl')
      await expect(html(page)).toHaveAttribute('lang', 'fa')
      await expect(theme(page).getByRole('tab', { name: 'خودکار' })).toHaveAttribute('data-state', 'active')
      await expect(language(page)).toHaveCount(0)
      await shot(page, 'F22-01-fa', size)
    })

    test('a language kept on the device from before is ignored', async ({ page }) => {
      await stored(page, { locale: 'de', theme: 'auto' })
      await open(page, {}, '/app/v2/account', BERLIN)
      await expect(html(page)).toHaveAttribute('dir', 'rtl')
      await expect(html(page)).toHaveAttribute('lang', 'fa')
      await expect(page.getByTestId('row-safety')).toContainText('راهنمای ایمنی')
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

    test('F22#5: the list stays Persian with English kept on the device', async ({ page }) => {
      await stored(page, { locale: 'en', theme: 'auto' })
      await open(page, {}, '/app/v2/')
      await expect(page.getByTestId('count-bar')).toBeVisible()
      await expect(html(page)).toHaveAttribute('dir', 'rtl')
      await expect(page.getByTestId('filter-button')).not.toContainText('Filters')
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

    for (const code of ['en-GB', 'de', 'fr']) {
      test(`a Telegram language of ${code} stays Persian`, async ({ page }) => {
        await open(page, {}, '/app/v2/', TORONTO, `&tglang=${code}`)
        await expect(html(page)).toHaveAttribute('dir', 'rtl')
        await expect(html(page)).toHaveAttribute('lang', 'fa')
      })
    }

    test('a failed save keeps the choice and offers a retry', async ({ page }) => {
      await open(page, { 'PATCH /api/v1/me/prefs/': coded(500, 'server_error') })
      await theme(page).getByRole('tab', { name: 'تاریک' }).click()
      await expect(html(page)).toHaveClass(/(^|\s)dark(\s|$)/)
      await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
    })

    test('another device’s language never comes with the account, and is put back to Persian', async ({ page }) => {
      await open(page, { '/api/v1/me/account/': { status: 200, body: { ...ACCOUNT, prefs: { ...ACCOUNT.prefs, locale: 'de' } } } })
      const sent = page.waitForRequest((request) => request.url().includes('/api/v1/me/prefs/') && request.method() === 'PATCH')
      await page.reload()
      expect((await sent).postDataJSON()).toEqual({ locale: 'fa' })
      await expect(page.getByTestId('row-city')).toBeVisible()
      await expect(html(page)).toHaveAttribute('lang', 'fa')
    })
  })
}
