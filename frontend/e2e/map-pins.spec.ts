import { expect, test, type Page } from '@playwright/test'
import { EXCHANGE, TORONTO_AREAS } from '../src/mocks/fixtures'
import { APP, mockApi, p3Meta, PHONE, savedCity, TORONTO } from './api-mock'

// F09: a pin marks its area's point at every zoom. A pin that sat in the page flow drifted by a fixed number of
// pixels, a long way on the ground zoomed out and almost nothing zoomed in.
const STYLE = 'https://tiles.openfreemap.org/styles/positron'
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e8eaed' } }] }
const ZOOMS = [9, 11, 13, 15, 17]
const TOLERANCE_PX = 3

// Real points from backend/home/area_catalog.py, put on the Toronto fixture's areas that have listings.
const REAL: Record<number, { name: string; lat: number; lng: number }> = {
  9001: { name: 'North York', lat: 43.7543, lng: -79.4491 },
  9004: { name: 'Plateau-Mont-Royal', lat: 45.5218, lng: -73.5822 },
  9002: { name: 'Neukölln', lat: 52.4811, lng: 13.4354 },
}
const AREAS = { ...TORONTO_AREAS, areas: TORONTO_AREAS.areas.map((row) => (REAL[row.id] ? { ...row, lat: REAL[row.id].lat, lng: REAL[row.id].lng } : row)) }

type Probe = { center: { x: number; y: number }; projected: { x: number; y: number } }

// The pin is centred on its point: the middle of its box, against MapLibre's projection, in the same frame.
async function measure(page: Page, id: number, zoom: number): Promise<Probe> {
  const { lat, lng } = REAL[id]
  await page.evaluate(
    ({ zoom, lat, lng }) => {
      const map = (window as unknown as { __chamedoonMap: import('maplibre-gl').Map }).__chamedoonMap
      // Off the middle, so a pin stuck at the centre does not pass by luck.
      map.jumpTo({ center: [lng, lat], zoom })
      map.panBy([37, -53], { duration: 0 })
    },
    { zoom, lat, lng },
  )
  // Zoomed far out the map shows no pins, so a pin can first appear here.
  await expect(page.locator(`[data-testid="map-pin"][data-area="${id}"]`)).toBeVisible()
  return page.evaluate(
    async ({ id, lat, lng }) => {
      await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
      const map = (window as unknown as { __chamedoonMap: import('maplibre-gl').Map }).__chamedoonMap
      const canvas = map.getContainer().getBoundingClientRect()
      const box = document.querySelector(`[data-testid="map-pin"][data-area="${id}"]`)!.getBoundingClientRect()
      const point = map.project([lng, lat])
      return {
        center: { x: box.left + box.width / 2 - canvas.left, y: box.top + box.height / 2 - canvas.top },
        projected: { x: point.x, y: point.y },
      }
    },
    { id, lat, lng },
  )
}

async function open(page: Page, locale: 'fa' | 'en', style: object | null = PLAIN) {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
  await page.addInitScript((ui) => localStorage.setItem('chamedoon.ui', JSON.stringify(ui)), { locale, theme: 'light' })
  await mockApi(page, {
    '/api/v1/public/meta/': p3Meta({ map_style_url: STYLE }),
    '/api/v1/public/areas/': { status: 200, body: AREAS },
    'POST /api/v1/auth/exchange/': { status: 200, body: { ...EXCHANGE, prefs: { ...EXCHANGE.prefs, locale, theme: 'light' } } },
  })
  if (style) await page.route(`${STYLE}*`, (route) => route.fulfill({ json: style }))
  await page.goto(APP('/app/home/map'))
  await page.waitForFunction(() => '__chamedoonMap' in window && (window as unknown as { __chamedoonMap: import('maplibre-gl').Map }).__chamedoonMap.loaded(), null, { timeout: 20_000 })
}

// WebGL with no GPU, on the CI runners too.
test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } })

for (const [language, dir] of [
  ['fa', 'rtl'],
] as const) {
  test(`${language}: every pin stays on its point at zoom ${ZOOMS.join(', ')}`, async ({ page }) => {
    await open(page, language)
    await expect(page.locator('html')).toHaveAttribute('dir', dir)
    for (const id of Object.keys(REAL).map(Number)) {
      for (const zoom of ZOOMS) {
        const { center, projected } = await measure(page, id, zoom)
        const off = Math.hypot(center.x - projected.x, center.y - projected.y)
        expect(off, `${REAL[id].name} at zoom ${zoom}: pin centre ${JSON.stringify(center)}, point ${JSON.stringify(projected)}`).toBeLessThanOrEqual(TOLERANCE_PX)
      }
    }
  })
}

// `SHOTS=<dir>` keeps pictures over the real tiles, for the eye: Plateau-Mont-Royal at three zooms, with a dot on the true point.
test('pictures at three zooms', async ({ page }) => {
  const dir = process.env.SHOTS
  test.skip(!dir, 'SHOTS is not set')
  await open(page, 'fa', null)
  for (const zoom of [9, 13, 17]) {
    const { projected } = await measure(page, 9004, zoom)
    await page.evaluate(({ x, y }) => {
      document.getElementById('true-point')?.remove()
      const dot = Object.assign(document.createElement('div'), { id: 'true-point' })
      const canvas = document.querySelector('[data-testid="map-canvas"]')!.getBoundingClientRect()
      Object.assign(dot.style, { position: 'fixed', left: `${canvas.left + x - 4}px`, top: `${canvas.top + y - 4}px`, width: '8px', height: '8px', borderRadius: '50%', background: 'red', zIndex: '99', pointerEvents: 'none' })
      document.body.append(dot)
    }, projected)
    await page.waitForTimeout(2500) // Tiles.
    await page.screenshot({ path: `${dir}/plateau-z${zoom}.png` })
  }
})
