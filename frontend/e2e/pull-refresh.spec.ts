import { expect, test, type Page } from '@playwright/test'
import { APP, mockApi, savedCity, TORONTO } from './api-mock'
import { COURIER, mockCourier } from './courier/mock'

// Tab reselect and pull-to-refresh, on both apps, phone and desktop (TAB-RESELECT-PTR-2026-11-21).
type Event = { name: string; data: Record<string, unknown> }

function events(page: Page) {
  const seen: Event[] = []
  page.on('console', async (message) => {
    const match = /^\[chamedoon\] (nav_tab_reselect|pull_to_refresh|screen_refresh_done)\b/.exec(message.text())
    if (match) seen.push({ name: match[1], data: ((await message.args()[1]?.jsonValue()) as Record<string, unknown>) ?? {} })
  })
  return seen
}
const named = (seen: Event[], name: string) => seen.filter((event) => event.name === name)

function counter(page: Page, part: string) {
  const hits = { n: 0 }
  page.on('request', (request) => {
    if (request.url().includes(part)) hits.n += 1
  })
  return hits
}

// One finger straight down by `dy` (and sideways by `dx`), then released unless `release` is false.
async function pull(page: Page, selector: string, dy: number, { dx = 0, release = true } = {}) {
  await page.locator(selector).first().evaluate(
    (element, { dy, dx, release }) => {
      const touch = (x: number, y: number) => new Touch({ identifier: 1, target: element, clientX: x, clientY: y })
      const fire = (type: string, x: number, y: number) =>
        element.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true, touches: type === 'touchend' ? [] : [touch(x, y)], changedTouches: [touch(x, y)] }))
      fire('touchstart', 100, 100)
      for (let step = 1; step <= 5; step++) fire('touchmove', 100 + (dx * step) / 5, 100 + (dy * step) / 5)
      if (release) fire('touchend', 100 + dx, 100 + dy)
    },
    { dy, dx, release },
  )
}
const release = (page: Page, selector: string) => page.locator(selector).first().evaluate((element) => element.dispatchEvent(new TouchEvent('touchend', { bubbles: true })))

const LIST = '[data-testid="list-scroll"]'
const toTop = (page: Page) => page.locator(LIST).evaluate((element) => (element.scrollTop = 0))
const shift = (page: Page) => page.locator(LIST).evaluate((element) => (element.firstElementChild as HTMLElement).style.transform)

for (const [size, viewport] of [
  ['phone', { width: 390, height: 844 }],
  ['desktop', { width: 1440, height: 900 }],
] as const) {
  test.describe(`housing on ${size}`, () => {
    test.use({ viewport, hasTouch: true })

    test.beforeEach(async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
    })

    test('reselect at the top refreshes, with the indicator', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await toTop(page)
      const feeds = counter(page, '/feeds/')
      await page.getByTestId('main-nav').getByRole('tab', { name: 'جستجو' }).click()
      await expect(page.getByTestId('pull-indicator')).toHaveAttribute('data-state', 'refreshing')
      await expect(page.getByTestId('pull-indicator')).toHaveCount(0)
      expect(feeds.n).toBeGreaterThan(0)
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(1)
      expect(named(seen, 'nav_tab_reselect')[0].data).toMatchObject({ product: 'home', tab: 'search', scrolled_from_px: 0, action: 'refresh', surface: 'miniapp' })
      expect(named(seen, 'screen_refresh_done')[0].data).toMatchObject({ product: 'home', screen: 'search', trigger: 'tab', ok: true })
      expect(named(seen, 'screen_refresh_done')[0].data.duration_ms).toEqual(expect.any(Number))
    })

    test('reselect when scrolled goes back to the top, then refreshes', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      const from = await page.locator(LIST).evaluate((element) => {
        element.scrollTop = 400
        return element.scrollTop
      })
      expect(from).toBeGreaterThan(0)
      await page.getByTestId('main-nav').getByRole('tab', { name: 'جستجو' }).click()
      await expect.poll(() => page.locator(LIST).evaluate((element) => element.scrollTop)).toBe(0)
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(1)
      expect(named(seen, 'nav_tab_reselect')[0].data).toMatchObject({ product: 'home', tab: 'search', scrolled_from_px: Math.round(from), action: 'scroll_top' })
    })

    test('pull past the threshold refreshes', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await toTop(page)
      const feeds = counter(page, '/feeds/')
      await pull(page, LIST, 220, { release: false })
      await expect(page.getByTestId('pull-indicator')).toHaveAttribute('data-state', 'armed')
      expect(await shift(page)).toMatch(/translateY\(\d+/)
      await page.screenshot({ path: `test-results/ptr-pull-${size}.png` })
      await release(page, LIST)
      await expect(page.getByTestId('pull-indicator')).toHaveAttribute('data-state', 'refreshing')
      await expect(page.getByTestId('pull-indicator')).toHaveCount(0)
      expect(feeds.n).toBeGreaterThan(0)
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(1)
      const pulled = named(seen, 'pull_to_refresh')[0].data
      expect(pulled).toMatchObject({ product: 'home', screen: 'search', result: 'refreshed', surface: 'miniapp' })
      expect(pulled.pulled_px as number).toBeGreaterThanOrEqual(64)
      expect(pulled.duration_ms).toEqual(expect.any(Number))
      expect(named(seen, 'screen_refresh_done')[0].data).toMatchObject({ trigger: 'pull', ok: true })
      await expect.poll(() => shift(page)).toBe('')
    })

    test('pull below the threshold eases back without a refresh', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await toTop(page)
      const feeds = counter(page, '/feeds/')
      await pull(page, LIST, 60)
      await expect(page.getByTestId('pull-indicator')).toHaveCount(0)
      await expect.poll(() => named(seen, 'pull_to_refresh').length).toBe(1)
      expect(named(seen, 'pull_to_refresh')[0].data).toMatchObject({ result: 'cancelled' })
      expect(named(seen, 'pull_to_refresh')[0].data.pulled_px as number).toBeLessThan(64)
      await page.waitForTimeout(600)
      expect(feeds.n).toBe(0)
      expect(named(seen, 'screen_refresh_done')).toHaveLength(0)
      await expect.poll(() => shift(page)).toBe('')
    })

    test('no pull when scrolled down, or on a sideways swipe', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await page.locator(LIST).evaluate((element) => (element.scrollTop = 300))
      await pull(page, LIST, 220)
      await toTop(page)
      await pull(page, LIST, 40, { dx: 200 })
      await page.waitForTimeout(400)
      expect(named(seen, 'pull_to_refresh')).toHaveLength(0)
      await expect(page.getByTestId('pull-indicator')).toHaveCount(0)
    })

    test('no double refresh while one is in flight', async ({ page }) => {
      const seen = events(page)
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await toTop(page)
      const tab = page.getByTestId('main-nav').getByRole('tab', { name: 'جستجو' })
      await tab.click()
      await tab.click()
      await pull(page, LIST, 220)
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(1)
      await page.waitForTimeout(800)
      expect(named(seen, 'screen_refresh_done')).toHaveLength(1)
    })

    test('reduced motion: spinner only, the content stays put', async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.goto(APP())
      await expect(page.getByTestId('listing-card').first()).toBeVisible()
      await toTop(page)
      await pull(page, LIST, 220, { release: false })
      await expect(page.getByTestId('pull-indicator')).toBeVisible()
      expect(await shift(page)).toBe('')
      await release(page, LIST)
      await expect(page.getByTestId('pull-indicator')).toHaveAttribute('data-state', 'refreshing')
      expect(await shift(page)).toBe('')
    })
  })

  test.describe(`courier on ${size}`, () => {
    test.use({ viewport, hasTouch: true })
    const ROUTES = 'side=DEMAND&from=CA:toronto&to=IR:tehran'

    test('reselect and pull refresh the routes list', async ({ page }) => {
      const seen = events(page)
      const calls = await mockCourier(page)
      await page.goto(COURIER(`?${ROUTES}`))
      await expect(page.getByTestId('request-card').first()).toBeVisible()
      const before = calls.filter((call) => call.path === 'requests/').length
      await page.getByTestId('courier-nav').getByRole('tab', { name: 'مسیرها' }).click()
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(1)
      expect(calls.filter((call) => call.path === 'requests/').length).toBeGreaterThan(before)
      expect(named(seen, 'nav_tab_reselect')[0].data).toMatchObject({ product: 'courier', tab: 'routes', action: 'refresh', surface: 'miniapp' })
      await pull(page, '[data-testid="routes"] .flex.flex-col.gap-2', 220)
      await expect.poll(() => named(seen, 'screen_refresh_done').length).toBe(2)
      expect(named(seen, 'pull_to_refresh')[0].data).toMatchObject({ product: 'courier', screen: 'routes', result: 'refreshed' })
      expect(named(seen, 'screen_refresh_done')[1].data).toMatchObject({ product: 'courier', screen: 'routes', trigger: 'pull' })
    })
  })
}
