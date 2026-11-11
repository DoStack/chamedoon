import { expect, test, type Page } from '@playwright/test'
import { TORONTO as CARDS, snapshot } from '../src/mocks/fixtures'
import { APP, mockApi, savedCity, TORONTO } from './api-mock'

// Telegram Desktop: a mouse, no touch. Drag and wheel scroll the rows; a drag opens nothing.
test.use({ hasTouch: false, viewport: { width: 360, height: 700 } })

const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 3"><rect width="4" height="3" fill="#9ab"/></svg>'
const CITY = { ...snapshot('toronto'), listings: CARDS.map((card) => ({ ...card, thumb: '/ph/1.svg', thumbs: [1, 2, 3, 4].map((n) => `/ph/${n}.svg`) })) }

async function open(page: Page) {
  await savedCity(page, TORONTO)
  await page.route('**/ph/**', (route) => route.fulfill({ status: 200, contentType: 'image/svg+xml', body: SVG }))
  await mockApi(page, { '/feeds/toronto.v1.json': { status: 200, body: CITY } })
  await page.goto(APP('/app/home/'))
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
}
const left = (page: Page, id: string) => page.getByTestId(id).first().evaluate((el) => Math.abs(el.scrollLeft))
const center = async (page: Page, id: string) => {
  // The header collapses on first paint and moves the rows: let it settle, then measure.
  await page.waitForTimeout(600)
  const box = await page.getByTestId(id).first().boundingBox()
  if (!box) throw new Error(id)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test('wheel and drag scroll the filter row', async ({ page }) => {
  await open(page)
  // The filter row scrolls away as the list does, a compact bar taking its place: bring it back.
  await page.getByTestId('list-scroll').evaluate((el) => el.scrollTo(0, 0))
  const row = page.getByTestId('filter-row')
  expect(await row.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true)
  const { x, y } = await center(page, 'filter-button')
  await page.mouse.move(x, y)
  await page.mouse.wheel(0, 120)
  await expect.poll(() => left(page, 'filter-row')).toBeGreaterThan(20)
  const before = await left(page, 'filter-row')
  await page.mouse.down()
  await page.mouse.move(x + 60, y, { steps: 6 })
  await page.mouse.up()
  expect(await left(page, 'filter-row')).not.toBe(before)
})

test('wheel, drag and arrows move a photo strip, and a drag does not open the listing', async ({ page }) => {
  await open(page)
  const { x, y } = await center(page, 'photo-strip')
  await page.mouse.move(x, y)
  await page.mouse.wheel(0, 120)
  await expect.poll(() => left(page, 'photo-strip')).toBeGreaterThan(50)
  await page.waitForTimeout(500)
  const before = await left(page, 'photo-strip')
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 200, y, { steps: 8 })
  await page.mouse.up()
  await page.waitForTimeout(400)
  expect(await left(page, 'photo-strip')).not.toBe(before)
  expect(new URL(page.url()).pathname).not.toContain('/listing/')
  await expect(page.getByTestId('strip-next').first()).toBeVisible()
  await page.getByTestId('strip-next').first().click()
  expect(new URL(page.url()).pathname).not.toContain('/listing/')
})
