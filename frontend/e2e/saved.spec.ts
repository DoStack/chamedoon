import { expect, test, type Page, type Route } from '@playwright/test'
import { TORONTO as CARDS, listing } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const row = (card: (typeof CARDS)[number], patch: object = {}) => ({ listing_id: card.id, state: 'active', card, closed_reason: null, note: '', saved_at: new Date().toISOString(), ...patch })
const page1 = (results: unknown[], next_cursor: string | null = null, total = results.length) => ({ status: 200, body: { results, total, next_cursor } })
const LIST = '/api/v1/me/saved/'
const IDS = '/api/v1/me/saved/ids/'
const ID = CARDS[0].id
const toast = (page: Page) => page.getByTestId('toast')
const nav = (page: Page) => page.getByTestId('main-nav')
const openSaved = (page: Page) => page.goto(APP('/app/v2/saved'))
const cards = (page: Page) => page.getByTestId('listing-card')
const bookmark = (page: Page, at = 0) => page.getByTestId('bookmark').nth(at)

// Writes as the server sees them, in order, and the ids the server would then answer with.
function record(page: Page, answer: (method: string, route: Route) => unknown = () => undefined, seed: number[] = []) {
  const writes: string[] = []
  const server = new Set(seed)
  void page.route(`**${IDS}`, (route) => route.fulfill({ json: { listing_ids: [...server] } }))
  void page.route(/\/api\/v1\/me\/saved\/\d+\/$/, async (route) => {
    const request = route.request()
    const id = Number(new URL(request.url()).pathname.split('/').at(-2))
    writes.push(`${request.method()} ${id} ${request.postData() ?? ''}`.trim())
    const custom = answer(request.method(), route)
    if (custom) return custom as Promise<void>
    if (request.method() === 'DELETE') {
      server.delete(id)
      return route.fulfill({ status: 204 })
    }
    server.add(id)
    const note = (JSON.parse(request.postData() ?? '{}') as { note?: string }).note ?? 'نوشته‌ی قبلی'
    return route.fulfill({ json: row(CARDS[0], { listing_id: id, note }) })
  })
  return writes
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test.describe('marking', () => {
      test('F14#1: marks at once, says so, and undo takes it back', async ({ page }) => {
        await mockApi(page)
        const writes = record(page)
        await page.goto(APP())
        await expect(cards(page).first()).toBeVisible()
        await bookmark(page).click()
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
        await expect(toast(page)).toContainText('نشان شد')
        await shot(page, 'F14-01', size)
        await expect.poll(() => writes).toEqual([`PUT ${ID} {}`])
        await toast(page).getByRole('button', { name: 'برگردان' }).click()
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'false')
        await expect.poll(() => writes.at(-1)).toBe(`DELETE ${ID}`)
      })

      test('tapping the mark does not open the listing, and the card still does', async ({ page }) => {
        await mockApi(page)
        record(page)
        await page.goto(APP())
        await bookmark(page).click()
        await expect(page).not.toHaveURL(/listing/)
        await cards(page).first().click()
        await expect(page).toHaveURL(/listing/)
      })

      test('a double tap is one tap', async ({ page }) => {
        let release = () => {}
        const held = new Promise<void>((resolve) => (release = resolve))
        await mockApi(page)
        const writes = record(page, (_method, route) => held.then(() => route.fulfill({ json: row(CARDS[0]) })))
        await page.goto(APP())
        await bookmark(page).dblclick()
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
        release()
        await expect.poll(() => writes.length).toBe(1)
      })

      test('F14#7: a failed mark goes back and offers retry, which works', async ({ page }) => {
        let fail = true
        await mockApi(page)
        const writes = record(page, (_method, route) => (fail ? route.fulfill({ status: 500, json: { code: 'server_error' } }) : undefined))
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('ذخیره نشد')
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'false')
        await shot(page, 'F14-07', size)
        fail = false
        await toast(page).getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
        await expect.poll(() => writes.length).toBe(2)
      })

      test('offline, a mark goes back with F14#7', async ({ page }) => {
        await mockApi(page)
        record(page, (_method, route) => route.abort('failed'))
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('ذخیره نشد')
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'false')
      })

      test('rate limited goes back with F25#6', async ({ page }) => {
        await mockApi(page)
        record(page, (_method, route) => route.fulfill({ status: 429, json: { code: 'rate_limited' } }))
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('چند لحظه‌ی دیگر')
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'false')
      })

      test('marking a listing that has gone says so and goes back', async ({ page }) => {
        await mockApi(page)
        record(page, (_method, route) => route.fulfill({ status: 404, json: { code: 'listing_gone' } }))
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('این آگهی دیگر نیست')
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'false')
      })

      test('browsing alone does not exchange, and the device keeps the marks', async ({ page }) => {
        let exchanged = false
        page.on('request', (request) => {
          if (request.url().includes('/auth/exchange/')) exchanged = true
        })
        await page.addInitScript((id) => localStorage.setItem('chamedoon.saved_ids', JSON.stringify([id])), ID)
        await mockApi(page)
        await page.goto(APP())
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
        expect(exchanged).toBe(false)
      })

      test('once a session exists the ids come from the server', async ({ page }) => {
        await mockApi(page)
        record(page, undefined, [CARDS[1].id])
        await page.goto(APP())
        await bookmark(page).click()
        await expect(page.locator('[data-testid="bookmark"][aria-pressed="true"]')).toHaveCount(2)
      })

      test('an ids answer older than a mark does not take the mark back', async ({ page }) => {
        // The answer is read before the write lands, and arrives after the write has finished.
        let putDone = () => {}
        const landed = new Promise<void>((resolve) => (putDone = resolve))
        let asked = 0
        await mockApi(page)
        await page.route(`**${IDS}`, async (route) => {
          asked += 1
          if (asked > 1) return route.fulfill({ json: { listing_ids: [ID] } })
          await landed
          await new Promise((resolve) => setTimeout(resolve, 150))
          return route.fulfill({ json: { listing_ids: [] } })
        })
        await page.route(/\/api\/v1\/me\/saved\/\d+\/$/, async (route) => {
          await route.fulfill({ json: row(CARDS[0]) })
          putDone()
        })
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('نشان شد')
        await expect.poll(() => asked).toBe(2)
        await page.waitForTimeout(150)
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
      })

      test('removing from the list card reads the note first, so undo brings it back', async ({ page }) => {
        await page.addInitScript((id) => localStorage.setItem('chamedoon.saved_ids', JSON.stringify([id])), ID)
        await mockApi(page)
        const writes = record(page, undefined, [ID])
        await page.goto(APP())
        await bookmark(page).click()
        await expect(toast(page)).toContainText('از نشان‌شده‌ها برداشته شد')
        await expect.poll(() => writes).toEqual([`PUT ${ID} {}`, `DELETE ${ID}`])
        await toast(page).getByRole('button', { name: 'برگردان' }).click()
        await expect.poll(() => writes.at(-1)).toBe(`PUT ${ID} {"note":"نوشته‌ی قبلی"}`)
      })
    })

    test.describe('detail', () => {
      test('the bar has the mark, and it works', async ({ page }) => {
        await mockApi(page)
        const writes = record(page)
        await page.goto(APP(`/app/v2/listing/${ID}`))
        const mark = page.locator('[data-testid="actions"]:visible').getByTestId('bookmark')
        await mark.click()
        await expect(mark).toHaveAttribute('aria-pressed', 'true')
        await expect.poll(() => writes).toEqual([`PUT ${ID} {}`])
      })

      test('a closed listing shows the mark on its photo only if marked', async ({ page }) => {
        await mockApi(page, { [`/api/v1/public/listings/${ID}/`]: { status: 200, body: listing(CARDS[0], { state: 'closed', closed_reason: 'owner' }) } })
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await expect(page.getByTestId('closed-note')).toBeVisible()
        await expect(page.getByRole('button', { name: 'برداشتن نشان' })).toHaveCount(0)
        await page.evaluate((id) => localStorage.setItem('chamedoon.saved_ids', JSON.stringify([id])), ID)
        await page.reload()
        await expect(page.getByTestId('closed-note')).toBeVisible()
        await expect(page.getByRole('button', { name: 'برداشتن نشان' }).first()).toBeVisible()
      })

      test('F11#17: no way to contact offers the mark', async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem('chamedoon.warned', '1'))
        await mockApi(page, { [`POST /api/v1/me/listings/${ID}/contact/`]: { status: 200, body: { has_any: false } } })
        record(page)
        await page.goto(APP(`/app/v2/listing/${ID}`))
        await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
        await page.getByTestId('bookmark-action').click()
        await expect(page.getByTestId('bookmark-action')).toContainText('برداشتن نشان')
      })
    })

    test.describe('the tab', () => {
      test('the nav has all four tabs, and the tab opens the screen, active', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([]) })
        await page.goto(APP())
        await expect(nav(page).getByRole('tab')).toHaveText(['جستجو', 'نشان‌شده‌ها', 'ثبت آگهی', 'حساب کاربری'])
        await nav(page).getByRole('tab', { name: 'نشان‌شده‌ها' }).click()
        await expect(page.getByTestId('saved-screen')).toBeVisible()
        await expect(nav(page).getByRole('tab', { name: 'نشان‌شده‌ها' })).toHaveAttribute('data-state', 'active')
      })

      test('«جستجو» returns to the list with its filters and scroll', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([]) })
        await page.goto(APP('/app/v2/?types=room'))
        await expect(page.getByTestId('filter-chip')).toHaveCount(1)
        await nav(page).getByRole('tab', { name: 'نشان‌شده‌ها' }).click()
        await expect(page.getByTestId('saved-empty')).toBeVisible()
        await nav(page).getByRole('tab', { name: 'جستجو' }).click()
        await expect(page).toHaveURL(/types=room/)
        await expect(page.getByTestId('filter-chip')).toHaveCount(1)
      })

      test('F14#6: empty, and «دیدن آگهی‌ها» goes to the list', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([]) })
        await openSaved(page)
        await expect(page.getByTestId('saved-empty')).toContainText('هنوز آگهی‌ای نشان نکرده‌اید')
        await shot(page, 'F14-06', size)
        await page.getByRole('button', { name: 'دیدن آگهی‌ها' }).click()
        await expect(page.getByTestId('list-scroll')).toBeVisible()
      })

      test('F14#2: the list with a note, and a card opens its listing, and Back returns', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[0], { note: 'شنبه ۶ عصر بازدید' }), row(CARDS[1]), row(CARDS[2])]) })
        await openSaved(page)
        await expect(cards(page)).toHaveCount(3)
        await expect(page.getByTestId('saved-count')).toContainText('۳')
        await expect(page.getByTestId('note-line')).toContainText('یادداشت: شنبه ۶ عصر بازدید')
        await expect(page.locator('[data-testid="bookmark"][aria-pressed="true"]')).toHaveCount(3)
        await page.waitForTimeout(150) // The ids answer, whichever came first, keeps what the list said.
        await expect(page.locator('[data-testid="bookmark"][aria-pressed="true"]')).toHaveCount(3)
        await shot(page, 'F14-02', size)
        await cards(page).first().click()
        await expect(page).toHaveURL(/listing/)
        await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
        await expect(page.getByTestId('saved-screen')).toBeVisible()
      })

      test('F14#3, #4: write a note, with the count and the limit', async ({ page }) => {
        let saved = false
        await mockApi(page)
        await page.route(`**${LIST}`, (route) => route.fulfill({ json: page1([row(CARDS[0], saved ? { note: 'بپرسم' } : {})]).body }))
        const writes = record(page, () => {
          saved = true
        })
        await openSaved(page)
        await page.getByTestId('note-add').click()
        const sheet = page.getByTestId('note-sheet')
        await expect(page.getByTestId('note-count')).toContainText('۰ / ۲۰۰')
        await sheet.getByRole('textbox').fill('بپرسم')
        await expect(page.getByTestId('note-count')).toContainText('۵ / ۲۰۰')
        await shot(page, 'F14-03', size)
        await sheet.getByRole('textbox').fill('ن'.repeat(250))
        await expect(sheet.getByRole('textbox')).not.toHaveValue('ن'.repeat(250))
        await sheet.getByRole('textbox').fill('بپرسم')
        await sheet.getByRole('button', { name: 'ذخیره یادداشت' }).click()
        await expect(sheet).toBeHidden()
        await expect(toast(page)).toContainText('یادداشت ذخیره شد')
        await expect(page.getByTestId('note-line')).toContainText('بپرسم')
        await shot(page, 'F14-04', size)
        expect(writes).toEqual([`PUT ${ID} {"note":"بپرسم"}`])
      })

      test('F14#9: an empty note removes it, and undo brings it back', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[0], { note: 'قبلی' })]) })
        const writes = record(page)
        await openSaved(page)
        await page.getByTestId('note-line').getByRole('button', { name: 'ویرایش' }).click()
        const sheet = page.getByTestId('note-sheet')
        await expect(sheet.getByRole('textbox')).toHaveValue('قبلی')
        await sheet.getByRole('textbox').fill('  ')
        await sheet.getByRole('button', { name: 'ذخیره یادداشت' }).click()
        await expect(toast(page)).toContainText('یادداشت حذف شد')
        await shot(page, 'F14-09', size)
        await toast(page).getByRole('button', { name: 'برگردان' }).click()
        await expect.poll(() => writes).toEqual([`PUT ${ID} {"note":""}`, `PUT ${ID} {"note":"قبلی"}`])
      })

      test('a note that fails keeps the sheet and the text', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[0])]) })
        record(page, (_method, route) => route.fulfill({ status: 500, json: { code: 'server_error' } }))
        await openSaved(page)
        await page.getByTestId('note-add').click()
        const sheet = page.getByTestId('note-sheet')
        await sheet.getByRole('textbox').fill('متن')
        await sheet.getByRole('button', { name: 'ذخیره یادداشت' }).click()
        await expect(toast(page)).toContainText('ذخیره نشد')
        await expect(sheet.getByRole('textbox')).toHaveValue('متن')
      })

      test('F14#5: removing drops the row with the count, and undo sends the note back', async ({ page }) => {
        await page.addInitScript((id) => localStorage.setItem('chamedoon.saved_ids', JSON.stringify([id])), ID)
        await mockApi(page, { [LIST]: page1([row(CARDS[0], { note: 'قبلی' }), row(CARDS[1])]) })
        const writes = record(page, undefined, [ID, CARDS[1].id])
        await openSaved(page)
        await expect(cards(page)).toHaveCount(2)
        await bookmark(page).click()
        await expect(cards(page)).toHaveCount(1)
        await expect(page.getByTestId('saved-count')).toContainText('۱')
        await expect(toast(page)).toContainText('از نشان‌شده‌ها برداشته شد')
        await shot(page, 'F14-05', size)
        await toast(page).getByRole('button', { name: 'برگردان' }).click()
        await expect(cards(page)).toHaveCount(2)
        await expect.poll(() => writes).toEqual([`DELETE ${ID}`, `PUT ${ID} {"note":"قبلی"}`])
      })

      test('removing the last row shows empty', async ({ page }) => {
        await page.addInitScript((id) => localStorage.setItem('chamedoon.saved_ids', JSON.stringify([id])), ID)
        await mockApi(page, { [LIST]: page1([row(CARDS[0])]) })
        record(page, undefined, [ID])
        await openSaved(page)
        await expect(bookmark(page)).toHaveAttribute('aria-pressed', 'true')
        await bookmark(page).click()
        await expect(page.getByTestId('saved-empty')).toBeVisible()
      })

      test('F14#4: a closed item stays, and offers similar ones', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[0], { state: 'closed', closed_reason: 'owner' })]) })
        await openSaved(page)
        await expect(cards(page).first()).toContainText('بسته شد')
        await page.getByTestId('saved-similar').click()
        await expect(page).toHaveURL(/city=toronto&types=apartment/)
      })

      test('F14#8: a gone item only offers removing it, with no undo', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[0], { state: 'gone', card: null }), row(CARDS[1])]) })
        const writes = record(page)
        await openSaved(page)
        await expect(page.getByTestId('saved-gone')).toContainText('این آگهی دیگر نیست')
        await shot(page, 'F14-08', size)
        await page.getByTestId('saved-gone').getByRole('button', { name: 'برداشتن نشان' }).click()
        await expect(page.getByTestId('saved-gone')).toHaveCount(0)
        await expect(toast(page).getByRole('button')).toHaveCount(0)
        await expect.poll(() => writes).toEqual([`DELETE ${ID}`])
      })

      test('a merged item writes to its listing_id and opens card.id', async ({ page }) => {
        await mockApi(page, { [LIST]: page1([row(CARDS[1], { listing_id: 555 })]) })
        const writes = record(page)
        await openSaved(page)
        await page.getByTestId('note-add').click()
        await page.getByTestId('note-sheet').getByRole('textbox').fill('متن')
        await page.getByTestId('note-sheet').getByRole('button', { name: 'ذخیره یادداشت' }).click()
        await expect.poll(() => writes).toEqual(['PUT 555 {"note":"متن"}'])
        await cards(page).first().click()
        await expect(page).toHaveURL(new RegExp(`listing/${CARDS[1].id}`))
      })

      test('many items load page by page, and a failed page offers retry', async ({ page }) => {
        const many = Array.from({ length: 60 }, (_, i) => row({ ...CARDS[0], id: 2000 + i }))
        let fail = true
        await mockApi(page)
        await page.route(`**${LIST}?*`, (route) => (fail ? route.fulfill({ status: 500, json: { code: 'server_error' } }) : route.fulfill({ json: page1(many.slice(50), null, 60).body })))
        await page.route(`**${LIST}`, (route) => route.fulfill({ json: page1(many.slice(0, 50), '50', 60).body }))
        await openSaved(page)
        await expect(page.getByTestId('saved-count')).toContainText('۶۰')
        const scroll = page.getByTestId('saved-scroll')
        await expect
          .poll(async () => {
            await scroll.evaluate((element) => element.scrollTo(0, 1_000_000))
            return page.getByTestId('more-failed').count()
          })
          .toBe(1)
        fail = false
        await page.getByTestId('more-failed').getByRole('button').click()
        await expect(cards(page)).toHaveCount(60)
      })

      test('F25#8: the list fails with retry, and retry recovers', async ({ page }) => {
        let fail = true
        await mockApi(page)
        await page.route(`**${LIST}`, (route) => (fail ? route.fulfill({ status: 400, json: { code: 'bad_request' } }) : route.fulfill({ json: page1([row(CARDS[0])]).body })))
        await openSaved(page)
        await expect(page.getByTestId('saved-failed')).toContainText('بارگذاری نشد')
        await expect(nav(page)).toBeVisible()
        fail = false
        await page.getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(cards(page)).toHaveCount(1)
      })

      test('F25#4: skeleton at once, then the wait message', async ({ page }) => {
        await mockApi(page, { [LIST]: 'hang' })
        await openSaved(page)
        await expect(page.getByTestId('saved-loading')).toBeVisible()
        await expect(page.getByTestId('slow-note')).toBeVisible({ timeout: 4000 })
        await expect(nav(page)).toBeVisible()
      })

      test('F25#1: offline shows the bar and retry', async ({ page, context }) => {
        await mockApi(page, { [LIST]: 'abort' })
        await page.goto(APP())
        await expect(cards(page).first()).toBeVisible()
        await context.setOffline(true)
        await nav(page).getByRole('tab', { name: 'نشان‌شده‌ها' }).click()
        await expect(page.getByTestId('offline-bar')).toBeVisible()
        await expect(page.getByTestId('saved-failed')).toBeVisible()
        await context.setOffline(false)
      })

      test('F02#7: a session that has ended replaces the screen', async ({ page }) => {
        await mockApi(page, { 'POST /api/v1/auth/exchange/': coded(401, 'session_expired') })
        await openSaved(page)
        await expect(page.getByText('نشست شما تمام شده است')).toBeVisible()
      })
    })
  })
}
