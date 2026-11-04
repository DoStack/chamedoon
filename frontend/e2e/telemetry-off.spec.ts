import { expect, test } from '@playwright/test'
import { APP, mockApi, savedCity, TORONTO } from './api-mock'

// A build with no PostHog key, which is every test and mock build: nothing loads and nothing leaves.
test('no key: no PostHog chunk and no request to it', async ({ page }) => {
  const seen: string[] = []
  page.on('request', (request) => {
    if (/posthog|telemetry-client|\/ingest/.test(request.url())) seen.push(request.url())
  })
  await savedCity(page, TORONTO)
  await mockApi(page)
  await page.goto(APP())
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
  await page.waitForTimeout(3000)
  expect(seen).toEqual([])
})
