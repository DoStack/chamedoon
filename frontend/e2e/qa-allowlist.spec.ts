import { expect, test, type Page } from '@playwright/test'
import { EXCHANGE, META } from '../src/mocks/fixtures'
import { APP, mockApi, PHONE, savedCity, TORONTO } from './api-mock'

const QA = { features: { alerts: true, account: true, posting: true, smart_posting: true, map: true }, text_search_on: true, alerts_on: true }
const CLOSED = { ...META, miniapp_v2: true, alerts_on: false, text_search_on: false, features: { alerts: false, account: false, posting: false, smart_posting: false } }
const EXCHANGE_URL = 'POST /api/v1/auth/exchange/'

function exchanges(page: Page) {
  const seen: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/auth/exchange/')) seen.push(request.url())
  })
  return seen
}

test.beforeEach(async ({ page }) => {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
})

test('nobody else: no exchange on opening, no map, the old search box', async ({ page }) => {
  const seen = exchanges(page)
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: CLOSED } })
  await page.goto(APP())
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
  await expect(page.getByTestId('list-search')).not.toHaveAttribute('readonly', '')
  await expect(page.getByTestId('map-button')).toHaveCount(0)
  expect(seen).toEqual([])
  await page.goto(APP('/app/v2/map'))
  await expect(page).not.toHaveURL(/\/map/)
})

test('the link of someone not on the list changes nothing', async ({ page }) => {
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: CLOSED }, [EXCHANGE_URL]: { status: 200, body: EXCHANGE } })
  await page.goto(APP('/app/v2/', '&tgstart=qa'))
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
  await expect(page.getByTestId('map-button')).toHaveCount(0)
  expect(await page.evaluate(() => localStorage.getItem('chamedoon.qa'))).toBeNull()
})

test('someone on the list, from the link: everything opens, and the device remembers', async ({ page }) => {
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: CLOSED }, [EXCHANGE_URL]: { status: 200, body: { ...EXCHANGE, qa: QA } } })
  await page.goto(APP('/app/v2/', '&tgstart=qa'))
  await expect(page.getByTestId('list-search')).toHaveAttribute('readonly', '')
  await expect(page.getByTestId('map-button')).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('chamedoon.qa'))).toBe('1')
  const seen = exchanges(page)
  await page.goto(APP())
  await expect(page.getByTestId('list-search')).toHaveAttribute('readonly', '')
  expect(seen.length).toBe(1)
})
