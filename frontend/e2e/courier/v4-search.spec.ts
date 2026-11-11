import { expect, test } from '@playwright/test'
import { COURIER, fx, mockCourier, PHONE } from './mock'

const meta = (features: Record<string, boolean>) => ({ status: 200, body: { ...fx('meta'), features } })

test.describe('V4 smart search', () => {
  test.use({ viewport: PHONE })

  test('off: the route goes back to the list', async ({ page }) => {
    await mockCourier(page)
    await page.goto(COURIER('search'))
    await expect(page).not.toHaveURL(/\/search/)
  })

  test('reads a line into chips and opens the list with them', async ({ page }) => {
    await mockCourier(page, { 'meta/': meta({ v4_smart_search: true, v4_route_bar: true }) })
    await page.goto(COURIER('?side=DEMAND&from=CA:toronto&to=IR:tehran'))
    await page.getByTestId('route-search').click()
    await page.waitForURL(/\/search/)
    await page.getByTestId('smart-input').fill('از تورنتو به تهران، مدرک')
    await expect(page.getByTestId('smart-from')).toContainText('تورنتو')
    await expect(page.getByTestId('smart-to')).toContainText('تهران')
    await expect(page.getByTestId('smart-cat')).toBeVisible()
    await page.getByTestId('smart-cat').click()
    await expect(page.getByTestId('smart-cat')).toHaveCount(0)
    await page.getByTestId('smart-show').click()
    await page.waitForURL(/from=CA%3Atoronto&to=IR%3Atehran/)
  })
})
