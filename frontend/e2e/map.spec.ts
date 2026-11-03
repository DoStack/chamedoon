import { expect, test, type Page } from '@playwright/test'
import { snapshot, TORONTO_AREAS } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

// F09. The tile server is stood in for: a style with no tiles draws a plain map, and pins are DOM over it.
const MAP = APP('/app/v2/map', '&tgmap=1')
const STYLE = 'https://tiles.openfreemap.org/styles/positron'
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e8eaed' } }] }
const WITH_TILES = { version: 8, sources: { t: { type: 'raster', tiles: ['https://tiles.openfreemap.org/t/{z}/{x}/{y}.png'], tileSize: 256 } }, layers: [{ id: 't', type: 'raster', source: 't' }] }
const FILE = '/feeds/toronto.v1.json'

const pins = (page: Page) => page.getByTestId('map-pin')
const pin = (page: Page, area: number) => page.locator(`[data-testid="map-pin"][data-area="${area}"]`)
const count = (page: Page) => page.getByTestId('map-count')
const toast = (page: Page) => page.getByTestId('toast')

async function plainStyle(page: Page, style: object = PLAIN) {
  await page.route(`${STYLE}*`, (route) => route.fulfill({ json: style }))
}

// WebGL with no GPU, on the CI runners too.
test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } })

test.describe('flag off', () => {
  test('no «نقشه» button, /map goes to the list, and the map library never loads', async ({ page }) => {
    const asked: string[] = []
    page.on('request', (request) => {
      if (/maplibre|openfreemap/.test(request.url())) asked.push(request.url())
    })
    await savedCity(page, TORONTO)
    await mockApi(page)
    await page.goto(APP('/app/v2/map'))
    await expect(page.getByTestId('listing-card').first()).toBeVisible()
    await expect(page.getByTestId('map-button')).toHaveCount(0)
    await expect(page).toHaveURL(/\/app\/v2\/$/)
    expect(asked).toEqual([])
  })
})

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('F09#1: one pin per area with its count, and the filters of the list', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(APP('/app/v2/', '&tgmap=1&max=2000'))
      await page.getByTestId('map-button').click()
      await expect(page).toHaveURL(/\/app\/v2\/map\?.*max=2000/)
      await expect(pins(page).first()).toBeVisible()
      await expect(page.getByTestId('filter-chip').first()).toBeVisible()
      await page.getByTestId('filter-chip').first().click()
      await expect(pins(page)).toHaveCount(5)
      await expect(pin(page, 1)).toHaveText('نورث یورک · ۳')
      await expect(count(page)).toHaveText('۱۰ آگهی در این ناحیه')
      await shot(page, 'f09-1-default', size)
    })

    test('RTL page, LTR canvas, and no sideways scroll', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pins(page).first()).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(page.getByTestId('map-canvas')).toHaveAttribute('dir', 'ltr')
      await expect(pin(page, 1)).toHaveAttribute('dir', 'rtl')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      if (size === 'desktop') {
        const [sheet, map] = await Promise.all([page.getByTestId('map-sheet').boundingBox(), page.getByTestId('map-area').boundingBox()])
        expect(sheet!.x).toBeGreaterThan(map!.x)
        await expect(page.getByTestId('map-to-list')).toBeHidden()
      } else {
        await expect(page.getByTestId('map-to-list')).toBeVisible()
      }
    })

    test('F09#2: a pin filters the sheet, and Back or a second tap drops it', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await pin(page, 1).click()
      await expect(pin(page, 1)).toHaveAttribute('aria-pressed', 'true')
      await expect(count(page)).toHaveText('۳ آگهی')
      await expect(page.getByTestId('map-cards').getByTestId('listing-card')).toHaveCount(3)
      await shot(page, 'f09-2-selected', size)
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(pin(page, 1)).toHaveAttribute('aria-pressed', 'false')
      await pin(page, 1).click()
      await pin(page, 1).click()
      await expect(count(page)).toHaveText('۱۰ آگهی در این ناحیه')
    })

    test('F09#3, F09#6: after a pan it offers this region, and an empty one zooms back out', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pins(page).first()).toBeVisible()
      const box = (await page.getByTestId('map-area').boundingBox())!
      const from = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
      // Far enough that every pin leaves the view.
      for (let i = 0; i < 4; i++) {
        await page.mouse.move(from.x + 100, from.y)
        await page.mouse.down()
        await page.mouse.move(from.x - 100, from.y, { steps: 6 })
        await page.mouse.up()
      }
      await expect(page.getByTestId('map-search-here')).toBeVisible()
      await shot(page, 'f09-3-panned', size)
      await page.getByTestId('map-search-here').click()
      await expect(page.getByTestId('map-empty')).toBeVisible()
      await shot(page, 'f09-6-empty-area', size)
      await page.getByTestId('map-empty').getByRole('button', { name: 'کوچک‌نمایی' }).click()
      await expect(count(page)).toHaveText('۱۰ آگهی در این ناحیه')
    })

    test('F09#4: where Telegram can, «مکان من» asks it and shows the dot', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await page.getByTestId('map-locate').click()
      await expect(page.getByTestId('map-me')).toBeAttached()
      await shot(page, 'f09-4-location', size)
    })

    test('F09#4: no location control on an older client', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(`${MAP}&tgversion=7.10`)
      await expect(pins(page).first()).toBeVisible()
      await expect(page.getByTestId('map-locate')).toHaveCount(0)
    })

    test('F09#5: location off says so and opens the settings', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(`${MAP}&tglocation=denied`)
      await page.getByTestId('map-locate').click()
      await expect(toast(page)).toContainText('دسترسی به مکان خاموش است')
      await shot(page, 'f09-5-location-off', size)
      await toast(page).getByRole('button', { name: 'تنظیمات' }).click()
      expect(await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __calls: string[] } } }).Telegram.WebApp.__calls)).toContain('openLocationSettings')
    })

    test('F09#7: zoomed far out hides the pins and asks to zoom in', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pins(page).first()).toBeVisible()
      const out = page.getByRole('button', { name: 'کوچک‌نمایی' })
      const chip = page.getByTestId('map-zoom-in-chip')
      await expect(async () => {
        await out.click()
        await expect(chip).toBeVisible({ timeout: 400 })
      }).toPass({ timeout: 15_000 })
      await expect(pins(page)).toHaveCount(0)
      await expect(count(page)).toHaveText('۱۰ آگهی در تورنتو')
      await shot(page, 'f09-7-zoomed-out', size)
      await chip.click()
      await expect(pins(page)).toHaveCount(5)
    })

    test('F09#8: no style, the map says so, the list stays, and retry brings it back', async ({ page }) => {
      await mockApi(page)
      let good = false
      await page.route(`${STYLE}*`, (route) => (good ? route.fulfill({ json: PLAIN }) : route.abort('failed')))
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
      await expect(count(page)).toHaveText('۱۰ آگهی در تورنتو')
      await expect(page.getByTestId('map-sheet')).toBeVisible()
      await shot(page, 'f09-8-failed', size)
      good = true
      await page.getByTestId('map-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(pins(page)).toHaveCount(5)
    })

    test('F09#8: tiles that all fail count as no map', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page, WITH_TILES)
      await page.route('https://tiles.openfreemap.org/t/**', (route) => route.abort('failed'))
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
    })

    test('F09#8: no WebGL is the same state', async ({ page }) => {
      await page.addInitScript(() => {
        const real = HTMLCanvasElement.prototype.getContext
        HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
          return kind.startsWith('webgl') ? null : (real as (...args: unknown[]) => unknown).call(this, kind, ...rest)
        } as typeof HTMLCanvasElement.prototype.getContext
      })
      await mockApi(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
    })

    test('F09#8: areas that fail hide the map too', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/areas/': coded(500, 'server_error') })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
      await expect(count(page)).toHaveText('۱۰ آگهی در تورنتو')
    })

    test('offline: the bar shows, the failed map keeps the list', async ({ page, context }) => {
      await mockApi(page)
      await page.route(`${STYLE}*`, (route) => route.abort('internetdisconnected'))
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
      await context.setOffline(true)
      await expect(page.getByTestId('offline-bar')).toBeVisible()
      await expect(count(page)).toHaveText('۱۰ آگهی در تورنتو')
      await shot(page, 'f09-offline', size)
      await context.setOffline(false)
    })

    test('slow style: placeholder, then the note', async ({ page }) => {
      await mockApi(page)
      await page.route(`${STYLE}*`, () => undefined)
      await page.goto(MAP)
      await expect(page.getByTestId('map-loading')).toBeVisible()
      await expect(count(page)).toBeVisible()
    })

    test('F09#9: listings with no area are counted and lead to the list', async ({ page }) => {
      const file = snapshot('toronto')
      await mockApi(page, { [FILE]: { status: 200, body: { ...file, listings: file.listings.map((card) => (card.id === 101 || card.id === 102 ? { ...card, area_id: null } : card)) } } })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-off-map')).toContainText('۲ آگهی بدون محله')
      await expect(count(page)).toHaveText('۸ آگهی در این ناحیه')
      await shot(page, 'f09-9-off-map', size)
      await page.getByTestId('map-off-map').getByRole('button', { name: 'دیدن در فهرست' }).click()
      await expect(page).toHaveURL(/\/app\/v2\/\?/)
    })

    test('an area with no point: its listings go off the map, never to a wrong place', async ({ page }) => {
      const file = snapshot('toronto')
      await mockApi(page, { [FILE]: { status: 200, body: { ...file, listings: file.listings.map((card) => (card.id === 101 ? { ...card, area_id: 6 } : card)) } } })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-off-map')).toContainText('۱ آگهی بدون محله')
      await expect(pin(page, 6)).toHaveCount(0)
    })

    test('empty city: says so, with another city as the way out', async ({ page }) => {
      await mockApi(page, { [FILE]: { status: 200, body: { ...snapshot('toronto'), listings: [] } } })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-city-empty')).toContainText('تورنتو')
      await expect(pins(page)).toHaveCount(0)
      await shot(page, 'f09-empty-city', size)
      await page.getByTestId('map-city-empty').getByRole('button', { name: 'شهر دیگر' }).click()
      await expect(page.getByTestId('city-sheet').or(page.getByRole('dialog'))).toBeVisible()
    })

    test('filters that match nothing: clear them from here', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(`${MAP}&max=1&unpriced=0`)
      await expect(page.getByTestId('map-filter-empty')).toBeVisible()
      await page.getByTestId('map-filter-empty').getByRole('button', { name: 'پاک کردن همه' }).click()
      await expect(pins(page)).toHaveCount(5)
    })

    test('an area all seen before this visit is dimmed', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.visit.toronto', new Date(Date.now() - 10 * 3_600_000).toISOString()))
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pin(page, 2)).toHaveAttribute('data-seen', 'true')
      await expect(pin(page, 1)).toHaveAttribute('data-seen', 'false')
    })
  })
}

test.describe('phone sheet', () => {
  test('drag up grows it, a tap collapses it to its title', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS[0][1])
    await savedCity(page, TORONTO)
    await mockApi(page)
    await plainStyle(page)
    await page.goto(MAP)
    const sheet = page.getByTestId('map-sheet')
    await expect(sheet).toHaveAttribute('data-size', 'half')
    const handle = (await page.getByTestId('map-sheet-handle').boundingBox())!
    await page.mouse.move(handle.x + handle.width / 2, handle.y + 5)
    await page.mouse.down()
    await page.mouse.move(handle.x + handle.width / 2, handle.y - 100)
    await page.mouse.up()
    await expect(sheet).toHaveAttribute('data-size', 'full')
    await page.getByTestId('map-sheet-handle').click()
    await expect(sheet).toHaveAttribute('data-size', 'peek')
    await expect(page.getByTestId('map-cards')).toBeHidden()
  })
})
