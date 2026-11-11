import { expect, test } from '@playwright/test'
import { COURIER, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

const LIST = 'side=DEMAND&from=CA:toronto&to=IR:tehran'
const V4 = { v4_route_bar: true, v4_filter_sheet: true, v4_smart_search: false, v4_connections: false, v4_report: false }
const meta = (features: Record<string, boolean>) => ({ status: 200, body: { ...fx('meta'), features } })
const facets = { status: 200, body: { ...fx('facets'), categories: [{ code: 'DOCUMENTS', count: 7 }, { code: 'CLOTHES', count: 5 }, { code: 'MEDICINE', count: 0 }] } }

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`V4 list on ${size}`, () => {
    test.use({ viewport })

    test('switches off: the current list, no v4 bar', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('request-card').first()).toBeVisible()
      await expect(page.getByTestId('route-bar-v4')).toHaveCount(0)
      await expect(page.getByTestId('date-tile').first()).toBeVisible()
    })

    test('route bar: side switch, count, swap', async ({ page }) => {
      await mockCourier(page, { 'meta/': meta(V4) })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('route-bar-v4')).toBeVisible()
      await expect(page.getByTestId('count')).toContainText('۴ مسافر')
      expect(await noSidewaysScroll(page)).toBe(true)
      await page.getByTestId('route-swap').click()
      await page.waitForURL(/from=IR%3Atehran&to=CA%3Atoronto/)
      await page.getByTestId('side-SUPPLY').click()
      await page.waitForURL(/side=SUPPLY/)
    })

    test('filter sheet: category with counts, chip with remove, badge', async ({ page }) => {
      const calls = await mockCourier(page, { 'meta/': meta(V4), 'requests/facets/': facets })
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open').click()
      await expect(page.getByTestId('filter-categories')).toBeVisible()
      await expect(page.getByTestId('filter-categories').getByRole('radio', { name: /دارو/ })).toBeDisabled()
      await page.getByTestId('filter-categories').getByRole('radio', { name: /مدرک/ }).click()
      await page.getByTestId('filter-apply').click()
      await page.waitForURL(/cat=DOCUMENTS/)
      await expect(page.getByTestId('filter-chip')).toContainText('مدرک')
      expect(calls.some((call) => call.path === 'requests/' && call.query.includes('category=DOCUMENTS'))).toBe(true)
      await page.getByTestId('filter-chip').click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
    })

    test('a category in a link is ignored while the sheet switch is off', async ({ page }) => {
      const calls = await mockCourier(page, { 'meta/': meta({ ...V4, v4_filter_sheet: false }) })
      await page.goto(COURIER(`?${LIST}&cat=DOCUMENTS`))
      await expect(page.getByTestId('request-card').first()).toBeVisible()
      expect(calls.some((call) => call.query.includes('category='))).toBe(false)
    })
  })
}
