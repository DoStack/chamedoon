import { expect, test, type Page } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

const back = (page: Page) => page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())

// The first three steps with a frequent route, as most people go.
async function packageToReview(page: Page) {
  await page.goto(COURIER('post/package'))
  await page.getByTestId('post-frequent').first().click()
  await page.getByTestId('post-when').getByRole('radio').nth(1).click()
  await page.getByTestId('post-next').click()
  await page.getByTestId('post-size').getByRole('radio').nth(2).click()
  await page.getByTestId('post-categories').getByRole('button').first().or(page.getByTestId('post-categories').getByRole('radio').first()).first().click()
  await page.getByTestId('post-note').fill('دو جلد کتاب')
  await page.getByTestId('post-next').click()
  await expect(page.getByTestId('post-step-4')).toBeVisible()
}

async function tripToReview(page: Page) {
  await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
  await page.getByTestId('post-when').getByRole('radio').nth(1).click()
  await page.getByTestId('post-next').click()
  await page.getByTestId('post-day-12').click()
  await page.getByTestId('post-next').click()
  await page.getByTestId('post-size').getByRole('radio').nth(3).click()
  await page.getByTestId('post-next').click()
  await expect(page.getByTestId('post-step-4')).toBeVisible()
}

test('the trip review reads a date in Persian, and the bar fills from the right', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockCourier(page)
  await tripToReview(page)
  const review = page.getByTestId('post-review')
  await expect(review).toContainText('۱۲')
  await expect(review).not.toContainText(/\d{4}-\d{2}/)
  await page.goto(COURIER('post/package'))
  // A quarter done: what shows is the right end of the track. The part sliding past its left edge is clipped, so read `left`.
  const start = () => Promise.all(['progress', 'progress-indicator'].map((slot) => page.locator(`[data-testid="post-progress"] [data-slot="${slot}"]`).evaluate((node) => node.getBoundingClientRect().left))).then(([track, bar]) => bar - track)
  await expect.poll(start).toBeGreaterThan(100)
})

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C07 on ${size}`, () => {
    test.use({ viewport })

    test('C07.1 chooser, on the post tab', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post'))
      await expect(page.getByTestId('post-chooser')).toContainText('چه چیزی ثبت می‌کنید؟')
      await page.getByTestId('post-package').click()
      await expect(page.getByTestId('post-step-1')).toContainText('مسیر بسته؟')
      expect(await noSidewaysScroll(page)).toBe(true)
    })

    test('C07.2 other route goes through the route sheet, with no «anywhere»', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/package'))
      await page.getByTestId('post-other-route').click()
      const sheet = page.getByTestId('route-sheet')
      await expect(sheet.getByTestId('anywhere')).toHaveCount(0)
      await sheet.getByTestId('country-CA').click()
      await sheet.getByTestId('city-toronto').click()
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-tehran').click()
      await expect(page.getByTestId('post-step-2')).toContainText('کی باید برسد؟')
    })

    test('C07.3 continue waits for a choice, and from a list the route is already set', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/package?from=CA:toronto&to=IR:tehran'))
      await expect(page.getByTestId('post-step-2')).toContainText('تورنتو ← تهران')
      await expect(page.getByTestId('post-next')).toBeDisabled()
      await page.getByTestId('post-when').getByRole('radio').last().click()
      await expect(page.getByTestId('post-next')).toBeEnabled()
    })

    test('C07.4 and C07.9 exact date: a bad one blocks with the copy, a good one goes', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/package?from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('post-when').getByRole('radio').nth(3).click()
      await page.getByTestId('post-date').fill('۳۲ دسامبر')
      await expect(page.getByTestId('date-error')).toContainText('این تاریخ را نفهمیدم')
      await expect(page.getByTestId('post-next')).toBeDisabled()
      await page.getByTestId('post-date').fill('۱۲ دسامبر')
      await expect(page.getByTestId('date-error')).toHaveCount(0)
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('post-step-3')).toBeVisible()
    })

    test('C07.5 size is needed, categories are optional', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/package?from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('post-when').getByRole('radio').last().click()
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('post-next')).toBeDisabled()
      await page.getByTestId('post-size').getByRole('radio').first().click()
      await expect(page.getByTestId('post-next')).toBeEnabled()
    })

    test('C07.6 prohibited sheet keeps the form', async ({ page }) => {
      await mockCourier(page)
      await packageToReview(page)
      await page.getByTestId('prohibited-open').click()
      await expect(page.getByTestId('prohibited-sheet')).toContainText(fx('guide').prohibited[0])
      await page.getByTestId('prohibited-sheet').getByRole('button', { name: 'بستن' }).click()
      await expect(page.getByTestId('post-review')).toContainText('تورنتو ← تهران')
    })

    test('C07.7 review, then saved, with an idempotency key and the bot sizes', async ({ page }) => {
      const calls = await mockCourier(page)
      await packageToReview(page)
      await expect(page.getByTestId('post-review')).toContainText('۱ تا ۳ کیلو')
      await expect(page.getByTestId('post-hint')).toContainText('ممنوع است')
      await page.getByTestId('post-submit').click()
      await page.waitForURL(/\/posted\/201/)
      const sent = calls.find((call) => call.method === 'POST' && call.path === 'me/requests/')!
      expect(sent.headers['idempotency-key']).toMatch(/^[A-Za-z0-9_-]{8,64}$/)
      expect(sent.body).toMatchObject({ type: 'DEMAND', origin: { country: 'CA', city: 'toronto' }, destinations: [{ country: 'IR', city: 'tehran' }], size: '3', description: 'دو جلد کتاب' })
    })

    test('C07.8 server rejects: the form stays, retry with the same key', async ({ page }) => {
      let fail = true
      const calls = await mockCourier(page, { 'POST me/requests/': () => (fail ? coded(400, 'server_rejected', { fields: { x: ['y'] } }) : { status: 201, body: fx('mineActive') }) })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-failed')).toContainText('ثبت نشد. دوباره تلاش کنید.')
      await expect(page.getByTestId('post-review')).toContainText('تورنتو ← تهران')
      await expect(page.locator('body')).not.toContainText('server_rejected')
      fail = false
      await page.getByTestId('post-submit').click()
      await page.waitForURL(/\/posted\//)
      const keys = calls.filter((call) => call.method === 'POST' && call.path === 'me/requests/').map((call) => call.headers['idempotency-key'])
      expect(new Set(keys).size).toBe(1)
    })

    test('C07.9 the server refuses the date: back on that step', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': coded(400, 'date_invalid', { fields: { when: ['date_invalid'] } }) })
      await page.goto(COURIER('post/package?from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('post-when').getByRole('radio').nth(3).click()
      await page.getByTestId('post-date').fill('۱۲ دسامبر')
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-size').getByRole('radio').first().click()
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('date-error')).toBeVisible()
    })

    test('C07.10 submitting locks the button', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': 'hang' })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-submit')).toBeDisabled()
      await expect(page.getByTestId('post-submit')).toContainText('در حال ثبت')
    })

    test('C07.11 a similar request: see it, or post anyway', async ({ page }) => {
      let duplicate = true
      const calls = await mockCourier(page, { 'POST me/requests/': () => (duplicate ? coded(409, 'duplicate_request', { request_id: 55 }) : { status: 201, body: fx('mineActive') }) })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-duplicate')).toContainText('برای همین مسیر یک درخواست فعال دارید.')
      duplicate = false
      await page.getByTestId('post-new-anyway').click()
      await page.waitForURL(/\/posted\//)
      expect(calls.filter((call) => call.method === 'POST' && call.path === 'me/requests/').at(-1)?.body).toMatchObject({ allow_duplicate: true })
    })

    test('C07.11 see the earlier request', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': coded(409, 'duplicate_request', { request_id: 55 }) })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await page.getByTestId('post-see-old').click()
      await page.waitForURL(/\/me\/requests\/55/)
    })

    test('C07.12 back steps up, and leaving a started form asks', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/package?from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('post-when').getByRole('radio').last().click()
      await back(page)
      await expect(page.getByTestId('leave-confirm')).toContainText('از فرم خارج می‌شوید؟')
      await page.getByTestId('leave-stay').click()
      await expect(page.getByTestId('leave-confirm')).toHaveCount(0)
      await expect(page.getByTestId('post-step-2')).toBeVisible()
      await back(page)
      await page.getByTestId('leave-go').click()
      await expect(page.getByTestId('post-chooser')).toBeVisible()
    })

    test('C18.8 connection lost on submit: the same key resends and lands on the saved request', async ({ page }) => {
      let first = true
      const calls = await mockCourier(page, {
        'POST me/requests/': () => {
          if (first) {
            first = false
            return 'abort'
          }
          return { status: 201, body: fx('mineActive') }
        },
      })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await page.waitForURL(/\/posted\//)
      const keys = calls.filter((call) => call.method === 'POST' && call.path === 'me/requests/').map((call) => call.headers['idempotency-key'])
      expect(keys.length).toBe(2)
      expect(keys[0]).toBe(keys[1])
    })

    test('C18.8 still no connection: the form stays with a retry', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': 'abort' })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-failed')).toBeVisible()
    })

    test('C07 same city from the server: back on the route', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': coded(400, 'same_city') })
      await packageToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-same-city')).toContainText('مقصد باید با مبدا فرق داشته باشد.')
    })
  })

  test.describe(`C08 on ${size}`, () => {
    test.use({ viewport })

    test('C08.1 several stops in one country', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
      await back(page).catch(() => {})
      await page.goto(COURIER('post/trip'))
      await page.getByTestId('route-from').click()
      const sheet = page.getByTestId('route-sheet')
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-tehran').click()
      await sheet.getByTestId('country-CA').click()
      await sheet.getByTestId('city-toronto').click()
      await expect(page.getByTestId('post-stops')).toBeVisible()
      await page.getByTestId('post-stops').getByRole('button', { name: 'مونترال' }).or(page.getByTestId('post-stops').getByRole('radio', { name: 'مونترال' })).first().click()
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('post-step-2')).toContainText('تورنتو')
      await expect(page.getByTestId('post-step-2')).toContainText('مونترال')
    })

    test('C08.2, C08.3 month, then the day; no «no hurry» for a traveler', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
      await expect(page.getByTestId('post-when')).not.toContainText('عجله ندارم')
      await page.getByTestId('post-when').getByRole('radio').nth(1).click()
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('post-days')).toBeVisible()
      await expect(page.getByTestId('post-next')).toBeDisabled()
      await page.getByTestId('post-day-12').click()
      await expect(page.getByTestId('post-next')).toBeEnabled()
    })

    test('C08.4, C08.5 space and refused, review, saved', async ({ page }) => {
      const calls = await mockCourier(page)
      await tripToReview(page)
      await expect(page.getByTestId('post-review')).toContainText('قبول نمی‌کند')
      await expect(page.getByTestId('post-step-4')).toContainText('هر مسافر حق دارد بسته را باز کند و ببیند.')
      await page.getByTestId('post-submit').click()
      await page.waitForURL(/\/posted\//)
      const sent = calls.find((call) => call.method === 'POST' && call.path === 'me/requests/')!.body as { type: string; when: { kind: string; date: string }; category_codes: string[] }
      expect(sent.type).toBe('SUPPLY')
      expect(sent.when.kind).toBe('exact')
      expect(sent.when.date).toMatch(/^\d{4}-\d{2}-12$/)
      expect(sent.category_codes.length).toBeGreaterThan(0)
    })

    test('C08.6 no date: continue says so', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('post-no-date')).toContainText('تاریخ پرواز را انتخاب کنید.')
    })

    test('C08.6 the server asks for a date: back on that step', async ({ page }) => {
      await mockCourier(page, { 'POST me/requests/': coded(400, 'flight_date_required') })
      await tripToReview(page)
      await page.getByTestId('post-submit').click()
      await expect(page.getByTestId('post-no-date')).toBeVisible()
    })

    test('C08.7 nothing ticked: blocked with the copy', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
      await page.getByTestId('post-when').getByRole('radio').nth(1).click()
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-day-12').click()
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-size').getByRole('radio').first().click()
      const chips = page.getByTestId('post-categories').locator('button[aria-pressed="true"], button[data-state="on"]')
      while ((await chips.count()) > 0) await chips.first().click()
      await expect(page.getByTestId('post-no-category')).toContainText('حداقل یک مورد را انتخاب کنید.')
      await expect(page.getByTestId('post-next')).toBeDisabled()
    })

    test('C08.8 a past day says so and is not chosen', async ({ page }) => {
      test.skip(new Date().getDate() === 1, 'The first of the month has no past day.')
      await mockCourier(page)
      await page.goto(COURIER('post/trip?from=IR:tehran&to=CA:toronto'))
      await page.getByTestId('post-when').getByRole('radio').first().click()
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-day-1').click()
      await expect(page.getByTestId('toast')).toContainText('این روز گذشته است.')
      await expect(page.getByTestId('post-next')).toBeDisabled()
    })
  })

  test.describe(`C09 on ${size}`, () => {
    test.use({ viewport })

    test('C09.1 suggestions, with the channel notice, and no contact', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('posted/201'))
      await expect(page.getByTestId('posted')).toContainText('ثبت شد')
      await expect(page.getByTestId('posted-channel')).toContainText('بدون نام و آیدی')
      await expect(page.getByTestId('posted')).toContainText('تطبیق‌های پیشنهادی')
      await expect(page.getByTestId('request-card').first()).toBeVisible()
      await expect(page.locator('body')).not.toContainText('@')
      await page.getByTestId('request-card').first().click()
      await page.waitForURL(/\/request\/\d+/)
    })

    test('C09.3 nobody: alert and mine', async ({ page }) => {
      await mockCourier(page, { 'GET me/requests/201/counterparts/': { status: 200, body: { items: [] } } })
      await page.goto(COURIER('posted/201'))
      await expect(page.getByTestId('posted-nobody')).toContainText('هنوز مسافری برای این مسیر نیست')
      await page.getByTestId('posted-alert').click()
      await expect(page.getByTestId('alert-sheet')).toBeVisible()
    })

    test('C09.4 channel off', async ({ page }) => {
      await mockCourier(page, { 'GET me/requests/201/': { status: 200, body: { ...fx('mineActive'), channel_on: false } } })
      await page.goto(COURIER('posted/201'))
      await expect(page.getByTestId('posted-channel-off')).toContainText('انتشار در کانال فعلاً خاموش است.')
      await expect(page.getByTestId('posted')).toContainText('درخواست‌های متناسب')
    })

    test('C09.5 saved, but the list failed: retry', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'GET me/requests/201/counterparts/': () => (fail ? coded(503, 'matches_failed') : { status: 200, body: fx('counterparts') }) })
      await page.goto(COURIER('posted/201'))
      await expect(page.getByTestId('posted-failed')).toContainText('فهرست مسافرها بارگذاری نشد')
      fail = false
      await page.getByTestId('posted-failed').getByRole('button').click()
      await expect(page.getByTestId('request-card').first()).toBeVisible()
    })

    test('C09 browse goes to that route on that side', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('posted/201'))
      await page.getByTestId('posted-browse').click()
      await page.waitForURL(/side=DEMAND&from=CA%3Atoronto&to=IR%3Atehran/)
    })
  })
}
