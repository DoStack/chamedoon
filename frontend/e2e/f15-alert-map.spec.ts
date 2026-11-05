import { expect, test, type Page } from '@playwright/test'
import { ALERTS } from '../src/mocks/fixtures'
import { APP, mockApi, p3Meta, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const MAP = APP('/app/v2/map', '&tgmap=1')
const STYLE = 'https://tiles.openfreemap.org/styles/positron'
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e8eaed' } }] }
const none = { status: 200, body: { ...ALERTS, results: [], active_count: 0 } }
const chip = (page: Page) => page.getByTestId('alert-chip')

// WebGL with no GPU, on the CI runners too.
test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } })

async function open(page: Page, meta: object = {}) {
  await page.route(`${STYLE}*`, (route) => route.fulfill({ json: PLAIN }))
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta({ map_style_url: STYLE, ...meta }), 'GET /api/v1/me/alerts/': none })
  await page.goto(MAP)
  await expect(page.getByTestId('map-area')).toBeVisible()
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('the bell lives on the list: the map has none, and its filter stays', async ({ page }) => {
      await open(page)
      await expect(page.getByTestId('filter-button')).toBeVisible()
      await expect(chip(page)).toHaveCount(0)
      await shot(page, 'F15-09-map', size)
      await page.getByTestId('map-to-list').click()
      await expect(chip(page)).toBeVisible()
    })
  })
}
