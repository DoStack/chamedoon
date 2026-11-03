import { expect, test, type Page } from '@playwright/test'
import { TORONTO as CARDS, listing, snapshot } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, TORONTO, VIEWPORTS } from './api-mock'

const pressBack = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
const cards = (page: Page) => page.getByTestId('listing-card')
const scroller = (page: Page) => page.getByTestId('list-scroll')
const scrollTop = (page: Page) => scroller(page).evaluate((element) => element.scrollTop)
const scrollTo = (page: Page, top: number) => scroller(page).evaluate((element, value) => element.scrollTo(0, value), top)
const LONG = Array.from({ length: 80 }, (_, i) => ({ ...CARDS[0], id: 1000 + i }))
const LONG_FILE = { '/feeds/toronto.v1.json': { status: 200, body: { ...snapshot('toronto'), listings: LONG } } }

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('QA-01: Telegram Back from a listing keeps the filters', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/v2/?types=room'))
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
      const count = await page.getByTestId('count-bar').locator('b').textContent()
      await cards(page).first().click()
      await expect(page).toHaveURL(/listing/)
      await pressBack(page)
      await expect(page).toHaveURL(/types=room/)
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
      await expect(page.getByTestId('count-bar').locator('b')).toHaveText(count!)
    })

    test('QA-01: Telegram Back returns to the same scroll, past the first page', async ({ page }) => {
      await mockApi(page, LONG_FILE)
      await page.goto(APP())
      await expect(cards(page).first()).toBeVisible()
      // To the end first, so the second batch is on the page before the position is taken.
      await scrollTo(page, 1_000_000)
      await expect.poll(() => cards(page).count()).toBeGreaterThan(30)
      await scrollTo(page, 3000)
      await expect.poll(() => scrollTop(page)).toBe(3000)
      const before = await cards(page).count()
      await cards(page).first().dispatchEvent('click') // A real click would scroll to the card.
      await expect(page).toHaveURL(/listing/)
      await pressBack(page)
      await expect(scroller(page)).toBeVisible()
      expect(await cards(page).count()).toBeGreaterThanOrEqual(before)
      await expect.poll(() => scrollTop(page)).toBe(3000)
    })

    test('QA-01: the browser back also returns to the same scroll', async ({ page }) => {
      await mockApi(page, LONG_FILE)
      await page.goto(APP())
      await expect(cards(page).first()).toBeVisible()
      await scrollTo(page, 600)
      await expect.poll(() => scrollTop(page)).toBe(600)
      await cards(page).first().dispatchEvent('click') // A real click would scroll to the card.
      await expect(page).toHaveURL(/listing/)
      await page.goBack()
      await expect.poll(() => scrollTop(page)).toBe(600)
    })

    test('a list reached forwards starts at the top', async ({ page }) => {
      await mockApi(page, LONG_FILE)
      await page.goto(APP())
      await expect(cards(page).first()).toBeVisible()
      await scrollTo(page, 600)
      await expect.poll(() => scrollTop(page)).toBe(600)
      await cards(page).first().dispatchEvent('click') // A real click would scroll to the card.
      await expect(page).toHaveURL(/listing/)
      await page.evaluate(() => {
        history.pushState(null, '', '/app/v2/?sort=cheap')
        dispatchEvent(new PopStateEvent('popstate'))
      })
      await expect(scroller(page)).toBeVisible()
      expect(await scrollTop(page)).toBe(0)
    })

    test('QA-01: from a similar listing, Back goes to the closed listing, then to the list', async ({ page }) => {
      await mockApi(page, { [`/api/v1/public/listings/${CARDS[0].id}/`]: { status: 200, body: listing(CARDS[0], { state: 'closed', closed_reason: 'owner' }) } })
      await page.goto(APP('/app/v2/?types=bed1'))
      await cards(page).first().click()
      await expect(page.getByTestId('closed-note')).toBeVisible()
      await cards(page).first().click()
      await expect(page.getByTestId('closed-note')).toBeHidden()
      await pressBack(page)
      await expect(page.getByTestId('closed-note')).toBeVisible()
      await pressBack(page)
      await expect(page).toHaveURL(/types=bed1/)
      await expect(scroller(page)).toBeVisible()
    })

    test('Back works while a listing is still loading', async ({ page }) => {
      await mockApi(page, { [`/api/v1/public/listings/${CARDS[0].id}/`]: 'hang' })
      await page.goto(APP('/app/v2/?types=bed1'))
      await cards(page).first().click()
      await expect(page.getByTestId('detail-loading')).toBeVisible()
      await pressBack(page)
      await expect(page).toHaveURL(/types=bed1/)
      await expect(scroller(page)).toBeVisible()
    })

    test('Back from a listing that is gone keeps the filters', async ({ page }) => {
      await mockApi(page, { [`/api/v1/public/listings/${CARDS[0].id}/`]: coded(404, 'listing_gone') })
      await page.goto(APP('/app/v2/?types=bed1'))
      await cards(page).first().click()
      await expect(page.getByTestId('listing-gone')).toBeVisible()
      await pressBack(page)
      await expect(page).toHaveURL(/types=bed1/)
    })

    test('F01#9: opened straight from a link, Back still goes to the list', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP(`/app/v2/listing/${CARDS[0].id}`))
      await expect(page.getByTestId('actions').first()).toBeAttached()
      await pressBack(page)
      await expect(scroller(page)).toBeVisible()
    })

    test('the live state: a 404 manifest means the feed, and the feed pages on', async ({ page }) => {
      const asked: string[] = []
      await mockApi(page, { '/feeds/manifest.json': { status: 404, body: '<!doctype html><title>Not Found</title>', html: true } })
      await page.route('**/api/v1/public/feed/**', (route) => {
        const start = Number(new URL(route.request().url()).searchParams.get('cursor') ?? 0)
        asked.push(String(start))
        return route.fulfill({
          json: { generated_at: new Date().toISOString(), results: LONG.slice(start, start + 60), total: LONG.length, next_cursor: start + 60 < LONG.length ? String(start + 60) : null },
        })
      })
      await page.goto(APP())
      await expect(cards(page).first()).toBeVisible()
      await expect(page.getByTestId('list-failed')).toHaveCount(0)
      await expect(page.getByTestId('count-bar').locator('b')).toContainText('+')
      await expect
        .poll(async () => {
          await scrollTo(page, 1_000_000)
          return asked.includes('60')
        })
        .toBe(true)
      await expect(page.getByTestId('count-bar').locator('b')).toHaveText('۸۰ آگهی')
    })
  })
}
