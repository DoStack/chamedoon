import { expect, test, type Page } from '@playwright/test'
import { META, TORONTO as CARDS, listing, snapshot } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const ID = CARDS[0].id
const DETAIL = `/api/v1/public/listings/${ID}/`
const CONTACT = `POST /api/v1/me/listings/${ID}/contact/`
const OK = (body: unknown) => ({ status: 200, body })
const NOT_MEMBER = coded(403, 'not_member', { join_url: 'https://t.me/+join', channel: { username: 'x', title: 'کانال', city: 'Toronto' } })
const pressBack = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
const pressContact = (page: Page) => page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
const cards = (page: Page) => page.getByTestId('listing-card')
const fromList = async (page: Page) => {
  await page.goto(APP())
  await cards(page).first().click()
  await expect(page).toHaveURL(/listing/)
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test.describe('QA-03', () => {
      test.beforeEach(async ({ page }) => savedCity(page, TORONTO))

      test('Back on the scam warning closes it and stays on the listing', async ({ page }) => {
        await mockApi(page)
        await fromList(page)
        await pressContact(page)
        await expect(page.getByTestId('warning-sheet')).toBeVisible()
        await pressBack(page)
        await expect(page.getByTestId('warning-sheet')).toBeHidden()
        await expect(page).toHaveURL(/listing/)
        await pressBack(page)
        await expect(page.getByTestId('list-scroll')).toBeVisible()
      })

      test('Back on the join sheet returns to the gate, then to the listing', async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem('chamedoon.warned', '1'))
        await mockApi(page, { [CONTACT]: NOT_MEMBER })
        await fromList(page)
        await pressContact(page)
        await page.getByTestId('gate-sheet').getByRole('button', { name: 'عضویت در کانال' }).click()
        await expect(page.getByTestId('join-sheet')).toBeVisible()
        await pressBack(page)
        await expect(page.getByTestId('gate-sheet')).toBeVisible()
        await expect(page).toHaveURL(/listing/)
        await pressBack(page)
        await expect(page.getByTestId('gate-sheet')).toBeHidden()
        await expect(page).toHaveURL(/listing/)
      })
    })

    test.describe('QA-04', () => {
      test('a double tap on the country asks the city and saves nothing', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await page.getByRole('button', { name: 'کانادا' }).dblclick()
        await expect(page.getByRole('heading', { name: 'کدام شهر؟' })).toBeVisible()
        expect(await page.evaluate(() => localStorage.getItem('chamedoon.city'))).toBeNull()
      })

      test('the same with a 200 ms gap, and a later tap still picks', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        const box = (await page.getByRole('button', { name: 'کانادا' }).boundingBox())!
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
        await page.waitForTimeout(200)
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
        await expect(page.getByRole('heading', { name: 'کدام شهر؟' })).toBeVisible()
        expect(await page.evaluate(() => localStorage.getItem('chamedoon.city'))).toBeNull()
        await page.waitForTimeout(600)
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
        await expect(page.getByTestId('city-button')).toBeVisible()
      })
    })

    test.describe('QA-05', () => {
      test.beforeEach(async ({ page }) => savedCity(page, TORONTO))

      test('a Latin paragraph runs left to right and a Persian one right to left, with the number whole', async ({ page }) => {
        const summary_fa = 'Apartment near Finch station. Available from 2026-12-01.\nتماس: +1-416-555-0100 بعد از ساعت ۵'
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { summary_fa })) })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        const lines = page.getByTestId('description-line')
        await expect(lines).toHaveCount(2)
        const direction = (at: number) => lines.nth(at).evaluate((element) => getComputedStyle(element).direction)
        expect(await direction(0)).toBe('ltr')
        expect(await direction(1)).toBe('rtl')
        const number = lines.nth(1).locator('bdi')
        await expect(number).toHaveText('+1-416-555-0100')
        await expect(number).toHaveAttribute('dir', 'ltr')
        // The plus sign is drawn to the left of the last digit, on one line.
        const drawn = await number.evaluate((element) => {
          const text = element.firstChild!
          const at = (index: number) => {
            const range = document.createRange()
            range.setStart(text, index)
            range.setEnd(text, index + 1)
            return range.getBoundingClientRect()
          }
          const first = at(0)
          const last = at(text.textContent!.length - 1)
          return { ordered: first.left < last.left, oneLine: first.top === last.top }
        })
        expect(drawn).toEqual({ ordered: true, oneLine: true })
        await lines.nth(1).scrollIntoViewIfNeeded()
        await shot(page, 'QA-05', size)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      })
    })

    test.describe('QA-06', () => {
      test('boot shows a skeleton at once and the wait message at 3 s', async ({ page }) => {
        await mockApi(page, { '/api/v1/public/meta/': 'hang' })
        await page.goto(APP())
        await expect(page.getByTestId('checking').locator('[data-slot="skeleton"]').first()).toBeVisible()
        await expect(page.getByTestId('slow-note')).toBeVisible({ timeout: 4000 })
        await shot(page, 'QA-06-boot', size)
      })

      test('first open: rows skeleton, wait message, and the error by 15 s with no second wait', async ({ page }) => {
        test.setTimeout(30_000)
        await mockApi(page, { '/api/v1/public/cities/': 'hang' })
        await page.goto(APP())
        await expect(page.getByTestId('rows-loading')).toBeVisible()
        await expect(page.getByTestId('slow-note')).toBeVisible({ timeout: 4000 })
        await expect(page.getByTestId('cities-failed')).toBeVisible({ timeout: 13_000 })
      })

      test('listing: wait message, and the error by 15 s with no second wait', async ({ page }) => {
        test.setTimeout(30_000)
        await savedCity(page, TORONTO)
        await mockApi(page, { [DETAIL]: 'hang' })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await expect(page.getByTestId('detail-loading')).toBeVisible()
        await expect(page.getByTestId('slow-note')).toBeVisible({ timeout: 4000 })
        await expect(page.getByTestId('detail-failed')).toBeVisible({ timeout: 13_000 })
      })

      test('contact: a spinner on the button while waiting', async ({ page }) => {
        await savedCity(page, TORONTO)
        await page.addInitScript(() => localStorage.setItem('chamedoon.warned', '1'))
        await mockApi(page, { [CONTACT]: 'hang' })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await pressContact(page)
        await expect(page.locator('[data-testid="actions"]:visible').locator('[data-slot="spinner"], svg.animate-spin').first()).toBeVisible()
      })
    })

    test.describe('QA-07', () => {
      test.beforeEach(async ({ page }) => savedCity(page, TORONTO))

      test('cities failing shows F25#8 in the sheet, and retry recovers', async ({ page }) => {
        let fail = true
        await mockApi(page)
        await page.route('**/api/v1/public/cities/', (route) => (fail ? route.fulfill({ status: 400, json: { code: 'bad_request' } }) : route.fallback()))
        await page.goto(APP())
        await page.getByTestId('city-button').click()
        const sheet = page.getByTestId('city-sheet')
        await expect(sheet.getByTestId('cities-failed')).toContainText('بارگذاری نشد')
        await shot(page, 'QA-07', size)
        fail = false
        await sheet.getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(sheet.getByRole('radio', { name: 'تورنتو' })).toBeVisible()
      })

      test('cities hanging shows a skeleton, not an empty sheet', async ({ page }) => {
        await mockApi(page, { '/api/v1/public/cities/': 'hang' })
        await page.goto(APP())
        await page.getByTestId('city-button').click()
        await expect(page.getByTestId('city-sheet').getByTestId('rows-loading')).toBeVisible()
      })
    })

    test.describe('QA-08', () => {
      const allDown = (page: Page) =>
        page.route(
          (url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'),
          (route) => route.abort('failed'),
        )

      test('cold open with the API down shows the saved list and the bar, and retry recovers', async ({ page }) => {
        await savedCity(page, TORONTO)
        await mockApi(page)
        await page.goto(APP())
        await expect(cards(page).first()).toBeVisible()
        await expect.poll(() => page.evaluate(() => new Promise((resolve) => {
          const request = indexedDB.open('chamedoon', 1)
          request.onsuccess = () => {
            const count = request.result.transaction('snapshots').objectStore('snapshots').count()
            count.onsuccess = () => resolve(count.result)
          }
        }))).toBeGreaterThan(0)
        await allDown(page)
        await page.reload()
        await expect(cards(page).first()).toBeVisible()
        await expect(page.getByTestId('offline-bar')).toBeVisible()
        await shot(page, 'QA-08', size)
        await page.unroute((url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'))
      })

      test('with no list on the device, a cold open still says no connection', async ({ page }) => {
        await savedCity(page, TORONTO)
        await mockApi(page)
        await allDown(page)
        await page.goto(APP())
        await expect(page.getByTestId('offline-screen')).toBeVisible()
      })

      test('offline in session, a card reaches an error with retry', async ({ page, context }) => {
        await savedCity(page, TORONTO)
        await mockApi(page, { [DETAIL]: 'abort' })
        await page.goto(APP())
        await expect(cards(page).first()).toBeVisible()
        await context.setOffline(true)
        await cards(page).first().click()
        await expect(page.getByTestId('detail-failed')).toBeVisible()
        await context.setOffline(false)
        await page.unroute((url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'))
        await mockApi(page)
        await page.getByTestId('detail-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(page.getByTestId('detail-failed')).toBeHidden()
      })
    })

    test('QA-09: the ways out are in view above the footer on a no-result sheet', async ({ page }) => {
      await savedCity(page, TORONTO)
      await page.setViewportSize({ width: viewport.width, height: 720 })
      await mockApi(page)
      await page.goto(APP('/app/v2/?types=studio&max=800'))
      await page.getByTestId('filter-button').click()
      const ways = page.getByTestId('filter-sheet').getByTestId('ways-out')
      await expect(ways).toBeInViewport({ ratio: 1 })
      const show = page.getByTestId('filter-sheet').getByRole('button', { name: /^نمایش/ })
      await expect(show).toBeDisabled()
      await expect
        .poll(async () => (await show.boundingBox())!.y - (await ways.boundingBox())!.y - (await ways.boundingBox())!.height)
        .toBeGreaterThanOrEqual(0)
      await shot(page, 'QA-09', size)
    })

    test.describe('QA-10', () => {
      for (const [name, patch] of [
        ['area null', { area: null }],
        ['available_to bad', { available_from: '2026-12-01', available_to: 'bad', rental_mode: 'sublet' }],
      ] as const) {
        test(`one card with ${name} costs that field, not the list`, async ({ page }) => {
          await savedCity(page, TORONTO)
          const listings = [{ ...CARDS[0], ...patch }, CARDS[1]]
          await mockApi(page, { '/feeds/toronto.v1.json': OK({ ...snapshot('toronto'), listings }) })
          await page.goto(APP())
          await expect(cards(page)).toHaveCount(2)
          await expect(page.getByTestId('crash-screen')).toHaveCount(0)
        })
      }

      test('a detail with available_from bad opens', async ({ page }) => {
        await savedCity(page, TORONTO)
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { available_from: 'bad' })) })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await expect(page.locator('[data-testid="actions"]:visible')).toBeVisible()
        await expect(page.getByTestId('crash-screen')).toHaveCount(0)
      })

      for (const body of [{}, [], null]) {
        test(`meta 200 with ${JSON.stringify(body)} offers retry`, async ({ page }) => {
          await mockApi(page, { '/api/v1/public/meta/': OK(body) })
          await page.goto(APP())
          const retry = page.getByRole('button', { name: 'تلاش مجدد' })
          await expect(retry).toBeVisible()
          await page.unroute((url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'))
          await mockApi(page, { '/api/v1/public/meta/': OK(META) })
          await retry.click()
          await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
        })
      }

      test('deeplink 200 with null is a normal open', async ({ page }) => {
        await savedCity(page, TORONTO)
        await mockApi(page, { '/api/v1/public/deeplink/': OK(null) })
        await page.goto(APP('/app/v2/', '&tgstart=home_101'))
        await expect(page.getByTestId('city-button')).toBeVisible()
      })

      test('cities 200 with null offers retry on first open', async ({ page }) => {
        await mockApi(page, { '/api/v1/public/cities/': OK(null) })
        await page.goto(APP())
        await expect(page.getByTestId('cities-failed')).toBeVisible()
      })

      test('contact 200 with null reaches the failed state', async ({ page }) => {
        await savedCity(page, TORONTO)
        await page.addInitScript(() => localStorage.setItem('chamedoon.warned', '1'))
        const errors: string[] = []
        page.on('pageerror', (error) => errors.push(error.message))
        await mockApi(page, { [CONTACT]: OK(null) })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await pressContact(page)
        await expect(page.getByRole('button', { name: 'تلاش مجدد' })).toBeVisible()
        expect(errors).toEqual([])
      })
    })
  })
}
