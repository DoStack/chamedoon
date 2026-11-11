import { expect, test } from '@playwright/test'
import { CONTACT, TORONTO as CARDS } from '../src/mocks/fixtures'
import { APP, mockApi, PHONE, savedCity, TORONTO } from './api-mock'

test.beforeEach(async ({ page }) => {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
})

test('QA-23: a contact link that is not https is not opened', async ({ page }) => {
  const id = CARDS[0].id
  await mockApi(page, { [`POST /api/v1/me/listings/${id}/contact/`]: { status: 200, body: { ...CONTACT, telegram_url: 'javascript:alert(1)' } } })
  await page.goto(APP(`/app/home/listing/${id}`))
  await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
  await page.getByTestId('contact-telegram').getByRole('button').click()
  const opened = await page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)
  expect(opened).toEqual([])
})

test('QA-11: the budget caption keeps CA$ in reading order', async ({ page }) => {
  await mockApi(page)
  await page.goto(APP('/app/home/?max=1900'))
  await page.getByTestId('filter-button').click()
  const caption = page.getByTestId('filter-sheet').locator('h3', { hasText: 'بودجه' }).locator('..').locator('bdi')
  await expect(caption).toContainText('CA$')
  const ordered = await caption.evaluate((element) => {
    const text = element.firstChild!
    const at = (index: number) => {
      const range = document.createRange()
      range.setStart(text, index)
      range.setEnd(text, index + 1)
      return range.getBoundingClientRect().left
    }
    const start = text.textContent!.indexOf('CA$')
    return at(start) < at(start + 2)
  })
  expect(ordered).toBe(true)
})
