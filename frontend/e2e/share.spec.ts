import { expect, test, type Page } from '@playwright/test'
import { CONTACT, DEEPLINK_LANDING, listing, TORONTO as CARDS } from '../src/mocks/fixtures'
import { APP, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const ID = CARDS[0].id
const openShare = async (page: Page, api = {}) => {
  await mockApi(page, api)
  await page.goto(APP(`/app/v2/listing/${ID}`))
  await page.locator('[data-testid="actions"]:visible').getByRole('button', { name: 'اشتراک‌گذاری' }).click()
  return page.getByTestId('share-sheet')
}
const opened = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('F13#1: sheet previews public fields and the link, with no contact', async ({ page }) => {
      const sheet = await openShare(page)
      await expect(page.getByTestId('share-preview')).toContainText('۱٬۸۵۰')
      await expect(sheet.getByRole('textbox', { name: 'لینک' })).toHaveValue(`t.me/chamedoon_home_bot?startapp=home_ca_toronto_l${ID}_src-share`)
      await expect(sheet).not.toContainText('username')
      await expect(sheet).not.toContainText('416')
      await shot(page, 'F13-01', size)
    })

    test('F13#2: copy gives the full link and closes the sheet', async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
      const sheet = await openShare(page)
      await sheet.getByRole('button', { name: 'کپی لینک' }).click()
      await expect(page.getByTestId('toast')).toContainText('لینک کپی شد')
      await expect(sheet).toBeHidden()
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`https://t.me/chamedoon_home_bot?startapp=home_ca_toronto_l${ID}_src-share`)
      await shot(page, 'F13-02', size)
    })

    test('F13#3: send opens Telegram with text and link', async ({ page }) => {
      const sheet = await openShare(page)
      await sheet.getByRole('button', { name: 'ارسال در تلگرام' }).click()
      const [url] = await opened(page)
      expect(url).toContain('https://t.me/share/url?')
      const params = new URL(url).searchParams
      expect(params.get('url')).toContain('src-share')
      expect(params.get('text')).toContain('۱٬۸۵۰ CA$')
      expect(params.get('text')).toContain('نورث یورک')
      expect(url).not.toContain(CONTACT.source_url)
    })

    test('F13#4: copy denied keeps the sheet and says so', async ({ page }) => {
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) } }))
      const sheet = await openShare(page)
      await sheet.getByRole('button', { name: 'کپی لینک' }).click()
      await expect(page.getByTestId('toast')).toContainText('کپی نشد')
      await expect(sheet).toBeVisible()
      await shot(page, 'F13-04', size)
    })

    test('a listing with no metro still gets a link that opens it', async ({ page }) => {
      const sheet = await openShare(page, { [`/api/v1/public/listings/${ID}/`]: { status: 200, body: listing({ ...CARDS[0], metro: '', metro_slug: '' }) } })
      await expect(sheet.getByRole('textbox', { name: 'لینک' })).toHaveValue(`t.me/chamedoon_home_bot?startapp=home_${ID}`)
    })

    test('a closed listing keeps share, per F10#6', async ({ page }) => {
      await mockApi(page, { [`/api/v1/public/listings/${ID}/`]: { status: 200, body: listing(CARDS[0], { state: 'closed', closed_reason: 'owner' }) } })
      await page.goto(APP(`/app/v2/listing/${ID}`))
      await expect(page.getByTestId('closed-note')).toBeVisible()
      await expect(page.locator('button[aria-label="اشتراک‌گذاری"]:visible')).toHaveCount(1)
    })

    test('the receiver of a shared link lands on that listing', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, kind: 'listing', listing_id: ID, listing_state: 'active', country: 'CA', city: 'Toronto', city_slug: 'toronto', city_known: true, src: 'share' } } })
      await page.goto(APP('/app/v2/', `&tgstart=home_ca_toronto_l${ID}_src-share`))
      await expect(page.getByTestId('detail')).toContainText('۱٬۸۵۰')
    })
  })
}
