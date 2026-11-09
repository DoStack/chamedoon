import { expect, test, type Page } from '@playwright/test'
import { TORONTO as CARDS } from '../src/mocks/fixtures'
import { APP, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'
import { NO_USERNAME, ON, P, READY_OFFER, seed, stubStorage } from './post-helpers'

// A poster without a Telegram username: Telegram's contact button, a phone-only post, E.164 numbers, and the seeker's
// call, copy and WhatsApp buttons. Telegram's own popup and the contact it sends the bot are mocked.
const ID = CARDS[0].id
const calls = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __calls: string[] } } }).Telegram.WebApp.__calls)
const opened = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)
const next = (page: Page) => page.getByTestId('post-next')

async function openOffer(page: Page, extra = '', username = false) {
  await seed(page, 'offer', { ...READY_OFFER, step: 4 })
  await savedCity(page, TORONTO)
  await mockApi(page, { ...ON, ...(username ? {} : { 'POST /api/v1/auth/exchange/': NO_USERNAME }) })
  await stubStorage(page)
  await page.goto(APP(`${P}/offer`, extra))
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
    })

    test('no username: one tap shares the number Telegram verified, and the post can go on', async ({ page }) => {
      await openOffer(page, '&tgtheme=dark')
      await expect(page.getByRole('button', { name: 'ساختن نام کاربری' })).toHaveCount(0)
      await expect(next(page)).toBeDisabled()
      await shot(page, 'PHONE-01-step4', size)
      const read = page.waitForRequest((request) => request.url().includes('/api/v1/me/phone/'))
      await page.getByTestId('phone-share').click()
      await read
      expect(await calls(page)).toContain('requestContact')
      await expect(page.getByTestId('phone-input')).toHaveValue('+14165550100')
      await expect(next(page)).toBeEnabled()
      await shot(page, 'PHONE-02-shared', size)
    })

    test('a declined popup leaves the field as it was', async ({ page }) => {
      await openOffer(page, '&tgcontact=no')
      await page.getByTestId('phone-share').click()
      await expect(page.getByTestId('phone-input')).toHaveValue('')
      await expect(next(page)).toBeDisabled()
    })

    test('a typed number becomes E.164, and the post sends it with no username', async ({ page }) => {
      await openOffer(page)
      await page.getByTestId('phone-input').fill('00989356706868')
      await page.getByTestId('phone-input').blur()
      await expect(page.getByTestId('phone-input')).toHaveValue('+989356706868')
      await next(page).click()
      const sent = page.waitForRequest((request) => request.url().includes('/me/listings/') && request.method() === 'POST')
      await page.getByTestId('post-publish').click()
      expect((await sent).postDataJSON().phone).toBe('+989356706868')
    })

    test('a typed text that is not a number holds the post back', async ({ page }) => {
      await openOffer(page, '', true)
      await expect(next(page)).toBeEnabled()
      await page.getByTestId('phone-input').fill('call me')
      await expect(next(page)).toBeDisabled()
      await expect(page.getByText('شماره تلفن درست نیست')).toBeVisible()
    })

    test('with a username the share button is offered too', async ({ page }) => {
      await openOffer(page, '', true)
      await expect(page.getByTestId('contact-username')).toBeVisible()
      await expect(page.getByTestId('phone-share')).toBeVisible()
    })

    test('the seeker can call, copy or WhatsApp the number', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP(`/app/v2/listing/${ID}`, '&tgtheme=dark'))
      await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
      const phone = page.getByTestId('contact-phone')
      await expect(phone).toContainText('+1 416 555 0100')
      await expect(page.getByTestId('contact-call')).toHaveAttribute('href', 'tel:+14165550100')
      await expect(page.getByTestId('contact-copy')).toBeVisible()
      await shot(page, 'PHONE-03-contact', size)
      await page.getByTestId('contact-whatsapp').click()
      expect(await opened(page)).toContain('https://wa.me/14165550100')
    })

    test('Telegram Desktop gets no call button', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP(`/app/v2/listing/${ID}`).replace('tgmock=ios', 'tgmock=tdesktop'))
      await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
      await expect(page.getByTestId('contact-phone')).toBeVisible()
      await expect(page.getByTestId('contact-call')).toHaveCount(0)
      await expect(page.getByTestId('contact-whatsapp')).toBeVisible()
    })
  })
}
