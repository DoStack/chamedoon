import { expect, test, type Page } from '@playwright/test'
import { META } from '../src/mocks/fixtures'
import { APP, mockApi, PHONE, savedCity, TORONTO } from './api-mock'

// Every other spec in this folder runs on the mock's meta, which has no `features` at all, so the whole suite also holds
// the app to how it behaved before P3. This one looks for the P3 parts themselves.
const OFF: Array<[string, object]> = [
  ['features missing', META],
  ['both off', { ...META, alerts_on: true, features: { alerts: false, account: false, posting: false, smart_posting: false } }],
]

const P3_CALLS = /\/api\/v1\/me\/(alerts|account|notifications)/
const P3_CHUNKS = /\/assets\/(alerts|alert-|account|inbox|notif|safety|messages)/

function watch(page: Page) {
  const seen: string[] = []
  page.on('request', (request) => seen.push(request.url()))
  return seen
}

for (const [name, meta] of OFF) {
  test.describe(name, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(PHONE)
      await savedCity(page, TORONTO)
      await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: meta } })
    })

    test('the list has no alert chip, and the tabs are the ones it had', async ({ page }) => {
      const seen = watch(page)
      await page.goto(APP())
      const nav = page.getByTestId('main-nav')
      await expect(nav.getByRole('tab')).toHaveText(['جستجو', 'نشان‌شده‌ها', 'ثبت آگهی', 'حساب کاربری'])
      await expect(page.getByTestId('count-bar')).toBeVisible()
      await expect(page.getByTestId('count-bar').getByRole('button')).toHaveCount(2)
      await expect(page.getByTestId('alert-chip')).toHaveCount(0)
      await expect(nav.getByTestId('tab-badge')).toHaveCount(0)
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(page.locator('html')).toHaveAttribute('lang', 'fa')
      expect(seen.filter((url) => P3_CALLS.test(url) || P3_CHUNKS.test(url))).toEqual([])
    })

    test('«حساب کاربری» still goes to the old page', async ({ page }) => {
      await page.goto(APP())
      await page.getByTestId('main-nav').getByRole('tab', { name: 'حساب کاربری' }).click()
      await page.waitForURL('**/app/home/mine/?*')
    })

    test('the saved tab is the bookmarks list alone', async ({ page }) => {
      const seen = watch(page)
      await page.goto(APP('/app/v2/saved'))
      await expect(page.getByTestId('saved-screen')).toBeVisible()
      await expect(page.getByRole('heading', { name: 'نشان‌شده‌ها' })).toBeVisible()
      await expect(page.getByTestId('saved-tabs')).toHaveCount(0)
      await expect(page.getByTestId('saved-empty')).toBeVisible()
      expect(seen.filter((url) => P3_CALLS.test(url) || P3_CHUNKS.test(url))).toEqual([])
    })

    for (const path of ['account', 'inbox', 'alerts/11', 'safety', 'settings/notifications']) {
      test(`/${path} acts like any unknown path`, async ({ page }) => {
        const seen = watch(page)
        await page.goto(APP(`/app/v2/${path}`))
        await expect(page.getByTestId('count-bar')).toBeVisible()
        expect(new URL(page.url()).pathname).toMatch(/^\/app\/v2\/?$/)
        expect(seen.filter((url) => P3_CALLS.test(url) || P3_CHUNKS.test(url))).toEqual([])
      })
    }

    test('a language or theme kept on the device changes nothing', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.ui', JSON.stringify({ locale: 'en', theme: 'dark' })))
      await page.goto(APP('/app/v2/', '&tglang=de'))
      await expect(page.getByTestId('count-bar')).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(page.locator('html')).not.toHaveClass(/(^|\s)dark(\s|$)/)
      await expect(page.getByTestId('main-nav').getByRole('tab').first()).toHaveText('جستجو')
    })
  })
}
