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
const calls = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __calls: string[] } } }).Telegram.WebApp.__calls)
const opened = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('F13#1: sheet shows the ad text and the link, with no contact', async ({ page }) => {
      const sheet = await openShare(page)
      await expect(page.getByTestId('share-preview')).toContainText('۱٬۸۵۰')
      const text = sheet.getByRole('textbox', { name: 'متن آگهی' })
      await expect(text).toHaveValue(/برای اطلاعات بیشتر و تماس:\nhttps:\/\/t\.me\/chamedoon_home_bot\?startapp=home_ca_toronto_l\d+_src-share$/)
      await expect(text).toHaveValue(/نزدیک مترو/)
      await expect(sheet.getByRole('button', { name: 'ذخیره‌ی عکس‌ها' })).toBeVisible()
      expect(await text.inputValue()).not.toMatch(/username|416/)
      await shot(page, 'F13-01', size)
    })

    test('F13#2: copy gives the full text and closes the sheet', async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
      const sheet = await openShare(page)
      await expect(sheet.getByRole('textbox', { name: 'متن آگهی' })).toHaveValue(/نزدیک مترو/)
      await sheet.getByRole('button', { name: 'کپی متن' }).click()
      await expect(page.getByTestId('toast')).toContainText('متن کپی شد')
      await expect(sheet).toBeHidden()
      const copied = await page.evaluate(() => navigator.clipboard.readText())
      expect(copied).toContain('🏠 آپارتمان')
      expect(copied).toMatch(new RegExp(`startapp=home_ca_toronto_l${ID}_src-share$`))
      await shot(page, 'F13-02', size)
    })

    test('F13#3: send opens the Telegram chat picker with the prepared message', async ({ page }) => {
      const sheet = await openShare(page)
      await expect(sheet.getByRole('textbox', { name: 'متن آگهی' })).toHaveValue(/نزدیک مترو/)
      await sheet.getByRole('button', { name: 'ارسال در تلگرام' }).click()
      expect(await calls(page)).toContain(`shareMessage:prep-${ID}`)
      await expect(sheet).toBeHidden()
    })

    test('F13#3b: without shareMessage the text goes through t.me/share/url', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP(`/app/v2/listing/${ID}`, '&tgversion=7.10'))
      await page.locator('[data-testid="actions"]:visible').getByRole('button', { name: 'اشتراک‌گذاری' }).click()
      const sheet = page.getByTestId('share-sheet')
      await expect(sheet.getByRole('textbox', { name: 'متن آگهی' })).toHaveValue(/نزدیک مترو/)
      await sheet.getByRole('button', { name: 'ارسال در تلگرام' }).click()
      const [url] = await opened(page)
      expect(url).toContain('https://t.me/share/url?')
      const params = new URL(url).searchParams
      expect(params.get('url')).toContain('src-share')
      expect(params.get('text')).toContain('۱٬۸۵۰ CA$')
      expect(params.get('text')).toContain('برای اطلاعات بیشتر و تماس:')
      expect(params.get('text')).not.toContain('src-share')
      expect(url).not.toContain(CONTACT.source_url)
    })

    test('F13#4: copy denied keeps the sheet and says so', async ({ page }) => {
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) } }))
      const sheet = await openShare(page)
      await sheet.getByRole('button', { name: 'کپی متن' }).click()
      await expect(page.getByTestId('toast')).toContainText('کپی نشد')
      await expect(sheet).toBeVisible()
      await shot(page, 'F13-04', size)
    })

    test('save photos asks Telegram for each photo', async ({ page }) => {
      const sheet = await openShare(page)
      await expect(sheet.getByRole('textbox', { name: 'متن آگهی' })).toHaveValue(/نزدیک مترو/)
      await sheet.getByRole('button', { name: 'ذخیره‌ی عکس‌ها' }).click()
      await expect.poll(async () => (await calls(page)).filter((call) => call.startsWith('downloadFile:'))).toHaveLength(2)
    })

    test('a listing with no metro, and no server answer, still gets a link that opens it', async ({ page }) => {
      const sheet = await openShare(page, { [`/api/v1/public/listings/${ID}/`]: { status: 200, body: listing({ ...CARDS[0], metro: '', metro_slug: '' }) }, [`/api/v1/me/listings/${ID}/share/`]: 'abort' })
      await expect(sheet.getByRole('textbox', { name: 'متن آگهی' })).toHaveValue(new RegExp(`startapp=home_${ID}$`))
    })

    test('a closed listing keeps share, per F10#6', async ({ page }) => {
      await mockApi(page, { [`/api/v1/public/listings/${ID}/`]: { status: 200, body: listing(CARDS[0], { state: 'closed', closed_reason: 'owner' }) } })
      await page.goto(APP(`/app/v2/listing/${ID}`))
      await expect(page.getByTestId('closed-note')).toBeVisible()
      await expect(page.locator('button[aria-label="اشتراک‌گذاری"]:visible')).toHaveCount(1)
    })

    test('the receiver of a shared link lands on that listing', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, kind: 'listing', listing_id: ID, listing_state: 'active', country: 'CA', city: 'Toronto', city_slug: 'toronto', city_known: true, src: 'share' } } })
      const events: string[] = []
      page.on('console', (message) => events.push(message.text()))
      await page.goto(APP('/app/v2/', `&tgstart=home_ca_toronto_l${ID}_src-share`))
      await expect(page.getByTestId('detail')).toContainText('۱٬۸۵۰')
      expect(events.some((text) => text.startsWith('[chamedoon] share_landing'))).toBe(true)
    })
  })
}
