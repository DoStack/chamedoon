import { expect, test, type Page } from '@playwright/test'
import { META_P3, snapshot, TORONTO_AREAS } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

// F09. The tile server is stood in for: a style with no tiles draws a plain map, and pins are DOM over it.
const MAP = APP('/app/v2/map', '&tgmap=1')
const STYLE = 'https://tiles.openfreemap.org/styles/positron'
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e8eaed' } }] }
const WITH_TILES = { version: 8, sources: { t: { type: 'raster', tiles: ['https://tiles.openfreemap.org/t/{z}/{x}/{y}.png'], tileSize: 256 } }, layers: [{ id: 't', type: 'raster', source: 't' }] }
const FILE = '/feeds/toronto.v1.json'

const pins = (page: Page) => page.getByTestId('map-pin')
const pin = (page: Page, area: number) => page.locator(`[data-testid="map-pin"][data-area="${area}"]`)
const list = (page: Page) => page.getByTestId('map-to-list')
const toast = (page: Page) => page.getByTestId('toast')

async function plainStyle(page: Page, style: object = PLAIN) {
  await page.route(`${STYLE}*`, (route) => route.fulfill({ json: style }))
}

// WebGL with no GPU, on the CI runners too.
test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } })

test.describe('a narrow phone', () => {
  test('nothing in the count bar runs off the screen, with the map and an alert on', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 })
    await savedCity(page, TORONTO)
    await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META_P3, map_style_url: 'https://tiles.example/style.json' } } })
    await page.goto(APP('/app/v2/', '&tgmap=1'))
    await expect(page.getByTestId('map-button')).toBeVisible()
    const overflow = await page.getByTestId('count-bar').evaluate((bar) => [...bar.querySelectorAll('button')].some((button) => {
      const box = button.getBoundingClientRect()
      return box.left < 0 || box.right > window.innerWidth
    }))
    expect(overflow).toBe(false)
    // One row (tap targets are 44 high): count, map, sort, view toggle, nothing wrapped onto a second line.
    expect((await page.getByTestId('count-bar').boundingBox())!.height).toBeLessThanOrEqual(52)
  })
})

test.describe('on for everyone', () => {
  test('«نقشه» on the list, and /map opens the map with no test switch', async ({ page }) => {
    await plainStyle(page)
    await savedCity(page, TORONTO)
    await mockApi(page)
    await page.goto(APP())
    await expect(page.getByTestId('listing-card').first()).toBeVisible()
    await expect(page.getByTestId('map-button')).toBeVisible()
    await page.goto(APP('/app/v2/map'))
    await expect(page).toHaveURL(/\/app\/v2\/map/)
    await expect(pins(page).first()).toBeVisible()
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
      await expect(page.getByTestId('filter-button')).toContainText('۱')
      await page.goto(MAP)
      await expect(pins(page)).toHaveCount(5)
      await expect(pin(page, 9001)).toHaveText('نورث یورک ۳')
      await shot(page, 'f09-1-default', size)
    })

    test('RTL page, LTR canvas, and no sideways scroll', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pins(page).first()).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
      await expect(page.getByTestId('map-canvas')).toHaveAttribute('dir', 'ltr')
      await expect(pin(page, 9001)).toHaveAttribute('dir', 'rtl')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await expect(list(page)).toBeVisible()
    })

    test('F09#2: a pin shows its card, and a second tap drops it', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await pin(page, 9001).click()
      await expect(pin(page, 9001)).toHaveAttribute('aria-pressed', 'true')
      await expect(page.getByTestId('map-cards').getByTestId('listing-card')).toHaveCount(3)
      await shot(page, 'f09-2-selected', size)
      await pin(page, 9001).click()
      await expect(pin(page, 9001)).toHaveAttribute('aria-pressed', 'false')
      await expect(page.getByTestId('map-cards')).toHaveCount(0)
    })

    test('a pin open, «لیست» opens the list on that area, with its «محله» chip and the other filters', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(APP('/app/v2/map', '&max=5000'))
      await pin(page, 9001).click()
      await expect(pin(page, 9001)).toHaveAttribute('aria-pressed', 'true')
      await list(page).click()
      await expect(page.getByTestId('list-scroll')).toBeVisible()
      const url = new URL(page.url())
      expect(url.searchParams.getAll('areas')).toEqual(['نورث یورک'])
      expect(url.searchParams.get('max')).toBe('5000')
      const cards = page.getByTestId('list-scroll').getByTestId('listing-card')
      await expect(cards).toHaveCount(3)
      for (const text of await cards.allTextContents()) expect(text).toContain('نورث یورک')
      await expect(page.getByTestId('filter-chip').filter({ hasText: 'نورث یورک' })).toBeVisible()
      await page.getByTestId('filter-chip').filter({ hasText: 'نورث یورک' }).click()
      await expect(page).not.toHaveURL(/areas=/)
    })

    test('Back with a pin open does what «لیست» does; with none, the list keeps its filters', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      const back = () => page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await page.goto(APP('/app/v2/map', '&max=5000'))
      await pin(page, 9001).click()
      await expect(pin(page, 9001)).toHaveAttribute('aria-pressed', 'true')
      await back()
      await expect(page.getByTestId('list-scroll')).toBeVisible()
      expect(new URL(page.url()).searchParams.getAll('areas')).toEqual(['نورث یورک'])
      await page.goto(APP('/app/v2/map', '&max=5000'))
      await expect(pin(page, 9001)).toBeVisible()
      await back()
      await expect(page.getByTestId('list-scroll')).toBeVisible()
      const url = new URL(page.url())
      expect(url.searchParams.getAll('areas')).toEqual([])
      expect(url.searchParams.get('max')).toBe('5000')
    })

    test('a pan moves the map and nothing else: no region to search any more', async ({ page }) => {
      await mockApi(page)
      await plainStyle(page)
      await page.goto(MAP)
      await expect(pins(page).first()).toBeVisible()
      const box = (await page.getByTestId('map-area').boundingBox())!
      const from = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
      await page.mouse.move(from.x + 100, from.y)
      await page.mouse.down()
      await page.mouse.move(from.x - 100, from.y, { steps: 6 })
      await page.mouse.up()
      await shot(page, 'f09-3-panned', size)
      await expect(page.getByTestId('map-search-here')).toHaveCount(0)
      await expect(page.getByTestId('map-to-list')).toBeVisible()
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
      await expect(list(page)).toBeVisible()
      await shot(page, 'f09-7-zoomed-out', size)
      await chip.click()
      await expect(pins(page)).toHaveCount(5)
    })

    test('F09#8: no style, the map says so, «لیست» stays, and retry brings it back', async ({ page }) => {
      await mockApi(page)
      let good = false
      await page.route(`${STYLE}*`, (route) => (good ? route.fulfill({ json: PLAIN }) : route.abort('failed')))
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
      await expect(list(page)).toBeVisible()
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
      await expect(list(page)).toBeVisible()
    })

    test('offline: the bar shows, and the failed map still leads to the list', async ({ page, context }) => {
      await mockApi(page)
      await page.route(`${STYLE}*`, (route) => route.abort('internetdisconnected'))
      await page.goto(MAP)
      await expect(page.getByTestId('map-failed')).toBeVisible()
      await context.setOffline(true)
      await expect(page.getByTestId('offline-bar')).toBeVisible()
      await expect(list(page)).toBeVisible()
      await shot(page, 'f09-offline', size)
      await context.setOffline(false)
    })

    test('slow style: placeholder, then the note', async ({ page }) => {
      await mockApi(page)
      await page.route(`${STYLE}*`, () => undefined)
      await page.goto(MAP)
      await expect(page.getByTestId('map-loading')).toBeVisible()
      await expect(list(page)).toBeVisible()
    })

    test('F09#9: listings with no area are counted and lead to the list', async ({ page }) => {
      const file = snapshot('toronto')
      await mockApi(page, { [FILE]: { status: 200, body: { ...file, listings: file.listings.map((card) => (card.id === 101 || card.id === 102 ? { ...card, area_id: null } : card)) } } })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-off-map')).toContainText('۲ آگهی بدون محله')
      await shot(page, 'f09-9-off-map', size)
      await page.getByTestId('map-off-map').click()
      await expect(page).toHaveURL(/\/app\/v2\/?\?/)
    })

    test('an area with no point: its listings go off the map, never to a wrong place', async ({ page }) => {
      const file = snapshot('toronto')
      const areas = { ...TORONTO_AREAS, areas: TORONTO_AREAS.areas.map((row) => (row.id === 9006 ? { ...row, lat: null, lng: null } : row)) }
      await mockApi(page, {
        [FILE]: { status: 200, body: { ...file, listings: file.listings.map((card) => (card.id === 101 ? { ...card, area_id: 9006 } : card)) } },
        '/api/v1/public/areas/': { status: 200, body: areas },
      })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-off-map')).toContainText('۱ آگهی بدون محله')
      await expect(pin(page, 9006)).toHaveCount(0)
    })

    test('empty city: says so, with another city as the way out', async ({ page }) => {
      await mockApi(page, { [FILE]: { status: 200, body: { ...snapshot('toronto'), listings: [] } } })
      await plainStyle(page)
      await page.goto(MAP)
      await expect(page.getByTestId('map-city-empty')).toContainText('تورنتو')
      await expect(pins(page)).toHaveCount(0)
      await shot(page, 'f09-empty-city', size)
      await page.getByTestId('map-city-empty').getByRole('button', { name: 'شهر دیگر' }).click()
      await expect(page.getByTestId('city-sheet')).toBeVisible()
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
      await expect(pin(page, 9004)).toHaveAttribute('data-seen', 'true')
      await expect(pin(page, 9001)).toHaveAttribute('data-seen', 'false')
    })
  })
}
