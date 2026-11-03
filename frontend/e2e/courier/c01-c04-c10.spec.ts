import { expect, test, type Page } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

const RETURNING = { status: 200, body: { ...fx('me'), prefs: { ...fx('me').prefs, side: 'DEMAND', last_origin: 'CA:toronto', last_destination: 'IR:tehran' } } }
const LIST = 'side=DEMAND&from=CA:toronto&to=IR:tehran'
const manyCards = (start: number, next: number | null, total: number) => ({
  status: 200,
  body: { items: Array.from({ length: 20 }, (_, index) => ({ ...fx('cardTrip'), id: start + index })), total, next_offset: next },
})
const cards = (page: Page) => page.getByTestId('request-card')

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C01 on ${size}`, () => {
    test.use({ viewport })

    test('C01.1 first direct open asks one question, and the answer is saved', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER())
      await expect(page.getByTestId('role-choice')).toContainText('بسته دارید یا مسافرید؟')
      await page.getByTestId('role-SUPPLY').click()
      await expect(page.getByTestId('routes')).toBeVisible()
      await expect(page.getByTestId('count')).toContainText('بسته')
      await expect.poll(() => calls.find((call) => call.method === 'PATCH')?.body).toEqual({ side: 'SUPPLY' })
    })

    test('C01.2 all routes with frequent routes, and a frequent chip sets the route', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('?side=DEMAND'))
      await expect(page.getByTestId('frequent-chips')).toBeVisible()
      await page.getByTestId('frequent-chips').getByRole('button').first().click()
      await expect(page.getByTestId('route-from')).toContainText('تورنتو')
      await expect(page.getByTestId('route-to')).toContainText('تهران')
    })

    test('C01.3 a channel link opens the request with no question', async ({ page }) => {
      await mockCourier(page, { 'POST deeplink/': { status: 200, body: { ...fx('deeplink'), kind: 'request', id: 101, screen: null } }, 'requests/101/': 'hang' })
      await page.goto(COURIER('', '&tgstart=explore_101'))
      await page.waitForURL(/\/courier\/request\/101/)
      await expect(page.getByTestId('role-choice')).toHaveCount(0)
    })

    test('C01.5 return user gets the last side and route, with no question', async ({ page }) => {
      await mockCourier(page, { 'me/': RETURNING })
      await page.goto(COURIER())
      await expect(page.getByTestId('routes')).toBeVisible()
      await expect(page.getByTestId('role-choice')).toHaveCount(0)
      await expect(page.getByTestId('route-from')).toContainText('تورنتو')
    })

    test('C01.6 a malformed link opens the list with a message', async ({ page }) => {
      await mockCourier(page, { 'POST deeplink/': coded(400, 'start_invalid'), 'me/': RETURNING })
      await page.goto(COURIER('', '&tgstart=!!bad'))
      await expect(page.getByTestId('toast')).toContainText('این لینک معتبر نیست')
      await expect(page.getByTestId('routes')).toBeVisible()
    })

    test('C01 a good link to something gone is not found', async ({ page }) => {
      await mockCourier(page, { 'POST deeplink/': coded(404, 'not_found') })
      await page.goto(COURIER('', '&tgstart=explore_999'))
      await expect(page.getByTestId('not-found')).toBeVisible()
    })

    test('C01 a housing link goes to the housing app', async ({ page }) => {
      await mockCourier(page, { 'POST deeplink/': { status: 200, body: { ...fx('deeplink'), kind: 'housing', screen: null } } })
      await page.route('**/app/v2/?*', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: 'housing' }))
      await page.goto(COURIER('', '&tgstart=home'))
      await page.waitForURL(/\/app\/v2\/\?/)
    })

    test('C01 an outreach link to other travelers is a list of those ids, read only', async ({ page }) => {
      const calls = await mockCourier(page, { 'POST deeplink/': { status: 200, body: { ...fx('deeplink'), kind: 'travelers', ids: [101, 102], screen: null, read_only: true } }, 'me/': RETURNING })
      await page.goto(COURIER('', '&tgstart=o_abcdefgh'))
      await expect(cards(page).first()).toBeVisible()
      expect(calls.find((call) => call.path === 'requests/')?.query).toContain('ids=101%2C102')
    })

    test('C01 saving the side fails: the list carries on', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/prefs/': coded(500, 'server_error') })
      await page.goto(COURIER())
      await page.getByTestId('role-DEMAND').click()
      await expect(cards(page).first()).toBeVisible()
    })
  })

  test.describe(`C02 on ${size}`, () => {
    test.use({ viewport })

    const openFrom = async (page: Page, extra = LIST) => {
      await page.goto(COURIER(`?${extra}`))
      await page.getByTestId('route-from').click()
      return page.getByTestId('route-sheet')
    }

    test('C02.1 frequent routes and countries, with no flags', async ({ page }) => {
      await mockCourier(page)
      const sheet = await openFrom(page, 'side=DEMAND')
      await expect(sheet.getByTestId('frequent-route').first()).toBeVisible()
      await expect(sheet.getByTestId('country-IR')).toContainText('ایران')
      await expect(sheet.getByTestId('anywhere')).toBeVisible()
      await sheet.getByTestId('frequent-route').first().click()
      await expect(page.getByTestId('route-to')).toContainText('تهران')
    })

    test('C02.2 to C02.4 origin city, then destination country and city', async ({ page }) => {
      await mockCourier(page)
      const sheet = await openFrom(page, 'side=DEMAND')
      await sheet.getByTestId('country-CA').click()
      await sheet.getByTestId('city-toronto').click()
      await expect(sheet).toContainText('به کدام کشور؟')
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-tehran').click()
      await expect(page.getByTestId('route-sheet')).toHaveCount(0)
      expect(page.url()).toContain('from=CA%3Atoronto')
      expect(page.url()).toContain('to=IR%3Atehran')
    })

    test('C02.5 a typed city not in the list is added by name', async ({ page }) => {
      const calls = await mockCourier(page)
      const sheet = await openFrom(page, 'side=DEMAND&from=CA:toronto')
      await sheet.getByRole('tab', { name: /^به/ }).click()
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-type').click()
      await sheet.getByTestId('city-search').fill('رشت')
      await expect(sheet.getByTestId('city-not-listed')).toContainText('رشت در فهرست نیست')
      await sheet.getByTestId('city-add').click()
      await expect(page.getByTestId('route-sheet')).toHaveCount(0)
      expect(calls.find((call) => call.path === 'cities/')?.body).toEqual({ country: 'IR', name: 'رشت' })
      expect(page.url()).toContain('to=IR%3Arasht')
    })

    test('C02.5 a rejected typed city says so and stays', async ({ page }) => {
      await mockCourier(page, { 'POST cities/': coded(400, 'city_invalid') })
      const sheet = await openFrom(page, 'side=DEMAND&from=CA:toronto')
      await sheet.getByRole('tab', { name: /^به/ }).click()
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-type').click()
      await sheet.getByTestId('city-search').fill('xx')
      await sheet.getByTestId('city-add').click()
      await expect(sheet.getByTestId('city-invalid')).toBeVisible()
    })

    test('C02.6 the origin city is disabled as destination', async ({ page }) => {
      await mockCourier(page)
      const sheet = await openFrom(page, 'side=DEMAND&from=CA:toronto')
      await sheet.getByRole('tab', { name: /^به/ }).click()
      await sheet.getByTestId('country-CA').click()
      await expect(sheet.getByTestId('city-toronto')).toBeDisabled()
    })

    test('C02.6 a same-city pair in the URL drops the destination', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': (route) => (new URL(route.request().url()).searchParams.get('destination') ? coded(400, 'same_city') : { status: 200, body: fx('travelers') }) })
      await page.goto(COURIER('?side=DEMAND&from=CA:toronto&to=CA:toronto'))
      await expect(cards(page).first()).toBeVisible()
      await expect(page.getByTestId('route-to')).toContainText('همه‌جا')
    })

    test('C02.7 swap changes the list at once', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('route-swap').click()
      await expect(page.getByTestId('route-from')).toContainText('تهران')
      await expect.poll(() => calls.filter((call) => call.path === 'requests/').at(-1)?.query).toContain('origin=IR%3Atehran')
    })

    test('C02.8 the list fails: retry, and a typed city still works on pinned countries', async ({ page }) => {
      await mockCourier(page, { 'locations/': coded(500, 'server_error') })
      const sheet = await openFrom(page, 'side=DEMAND')
      await expect(sheet.getByTestId('locations-failed')).toContainText('فهرست شهرها بارگذاری نشد', { timeout: 10_000 })
      await sheet.getByTestId('country-IR').click()
      await sheet.getByTestId('city-search').fill('رشت')
      await expect(sheet.getByTestId('city-add')).toBeVisible()
    })

    test('C02 back goes up one step, then closes with the route unchanged', async ({ page }) => {
      await mockCourier(page)
      const sheet = await openFrom(page)
      await sheet.getByTestId('country-CA').click()
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(sheet.getByTestId('country-CA')).toBeVisible()
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('route-sheet')).toHaveCount(0)
      await expect(page.getByTestId('route-from')).toContainText('تورنتو')
    })
  })

  test.describe(`C03 on ${size}`, () => {
    test.use({ viewport })

    test('C03.1 travelers, RTL, no sideways scroll, no name on any card', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page)).toHaveCount(4)
      await expect(page.getByTestId('count')).toContainText('۴ مسافر')
      await expect(cards(page).first()).toContainText('هزینه: توافقی')
      await expect(page.getByTestId('trust-badge').first()).toContainText('۲ ارسال موفق')
      expect(await noSidewaysScroll(page)).toBe(true)
      const columns = await page.evaluate(() => getComputedStyle(document.querySelector('[data-testid="request-card"]')!.parentElement!).gridTemplateColumns.split(' ').length)
      expect(columns).toBe(size === 'phone' ? 1 : 3)
    })

    test('C03.2 packages for a traveler', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('?side=SUPPLY&from=CA:toronto&to=IR:tehran'))
      await expect(page.getByTestId('count')).toContainText('۳ بسته')
      await expect(cards(page).first()).toContainText('تا ')
    })

    test('C03.3 first load: skeletons, with side, route and filters visible', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': 'hang' })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('card-skeleton').first()).toBeVisible()
      await expect(page.getByTestId('route-from')).toBeVisible()
      await expect(page.getByTestId('filter-row')).toBeVisible()
    })

    test('C03.4 and C03.5 load more by scrolling, to the end of the list', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': (route) => (new URL(route.request().url()).searchParams.get('offset') === '0' ? manyCards(1000, 20, 40) : manyCards(2000, null, 40)) })
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page)).toHaveCount(20)
      await page.getByTestId('request-card').last().scrollIntoViewIfNeeded()
      await page.locator('[aria-hidden].h-px').scrollIntoViewIfNeeded()
      await expect(cards(page)).toHaveCount(40)
      await expect(page.getByTestId('list-end')).toContainText('همه‌ی ۴۰ مسافر را دیدید')
    })

    test('C03.6 pull down refreshes, and no refresh button on a current client', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page).first()).toBeVisible()
      await expect(page.getByTestId('list-refresh')).toHaveCount(0)
      await page.evaluate(() => {
        const root = document.querySelector('[data-testid="routes"] .flex.flex-col.gap-2')!
        const touch = (y: number) => new Touch({ identifier: 1, target: root, clientX: 100, clientY: y })
        root.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: [touch(100)], changedTouches: [touch(100)] }))
        root.dispatchEvent(new TouchEvent('touchend', { bubbles: true, touches: [], changedTouches: [touch(260)] }))
      })
      await expect.poll(() => calls.filter((call) => call.path === 'requests/').length).toBeGreaterThan(1)
    })

    test('C03.6 an old client has a refresh button', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`, '&tgversion=7.0'))
      await expect(page.getByTestId('list-refresh')).toBeVisible()
    })

    test('C03.7 next page fails: retry under the cards', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'GET requests/': (route) => (new URL(route.request().url()).searchParams.get('offset') === '0' ? manyCards(1000, 20, 40) : fail ? coded(500, 'server_error') : manyCards(2000, null, 40)) })
      await page.goto(COURIER(`?${LIST}`))
      await page.locator('[aria-hidden].h-px').scrollIntoViewIfNeeded()
      await expect(page.getByTestId('more-failed')).toContainText('ادامه‌ی فهرست بارگذاری نشد', { timeout: 15_000 })
      fail = false
      await page.getByTestId('more-failed').getByRole('button').click()
      await expect(cards(page)).toHaveCount(40)
    })

    test('C03.8 route with nobody: alert and post', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': { status: 200, body: { items: [], total: 0, next_offset: null } } })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('route-empty')).toContainText('هنوز مسافری برای این مسیر نیست')
      await page.getByTestId('empty-post').click()
      await page.waitForURL(/\/post\/package\?from=CA%3Atoronto&to=IR%3Atehran/)
    })

    test('C03.9 multi-stop card', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page).nth(2)).toContainText('تهران ← تورنتو و مونترال')
    })

    test('C03.10 first load fails: the header stays, retry works', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'GET requests/': () => (fail ? 'abort' : { status: 200, body: fx('travelers') }) })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('list-failed')).toContainText('فهرست بارگذاری نشد', { timeout: 10_000 })
      await expect(page.getByTestId('route-from')).toBeVisible()
      fail = false
      await page.getByTestId('list-failed').getByRole('button').click()
      await expect(cards(page).first()).toBeVisible()
    })

    test('C03.11 one result', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': { status: 200, body: { items: [fx('cardTrip')], total: 1, next_offset: null } } })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('list-end')).toContainText('همین یک مسافر است')
    })

    test('C03.12 thin data reads as the design says', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page).nth(3)).toContainText('تاریخ پرواز نامشخص')
      await expect(cards(page).nth(3)).toContainText('جا: نامشخص')
      await page.goto(COURIER('?side=SUPPLY'))
      await expect(cards(page).nth(2)).toContainText('بدون عجله')
      await expect(cards(page).nth(2)).toContainText('وزن نامشخص')
    })

    test('C18.1 connection drops with data on screen: bar, and the list stays', async ({ page, context }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await expect(cards(page).first()).toBeVisible()
      await context.setOffline(true)
      await expect(page.getByTestId('offline-bar')).toContainText('اتصال اینترنت برقرار نیست')
      await expect(cards(page).first()).toBeVisible()
      await context.setOffline(false)
      await expect(page.getByTestId('offline-bar')).toHaveCount(0)
    })

    test('C18.2 and C18.9 list errors', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': coded(429, 'rate_limited') })
      await page.goto(COURIER(`?${LIST}`))
      await expect(page.getByTestId('rate-limited')).toContainText('درخواست‌ها زیاد شد')
    })
  })

  test.describe(`C04 on ${size}`, () => {
    test.use({ viewport })

    test('C04.1 time sheet applies one month', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open-month').click()
      await page.getByTestId('filter-months').getByRole('radio').nth(1).click()
      await page.getByTestId('filter-apply').click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
      await expect.poll(() => calls.filter((call) => call.path === 'requests/').at(-1)?.query).toMatch(/month=\d{4}-\d{2}/)
    })

    test('C04.2 and C04.6 size sheet with live counts: zero disables, the button counts', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open-size').click()
      const sizes = page.getByTestId('filter-sizes').getByRole('radio')
      await expect(sizes.nth(0)).toContainText('مدرک / نامه')
      await expect(sizes.nth(1)).toBeDisabled()
      await expect(page.getByTestId('filter-apply')).toContainText('نمایش ۶ مسافر')
    })

    test('C04.3 the traveler reads «فقط مدرک»', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('?side=SUPPLY&from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('filter-open-size').click()
      await expect(page.getByTestId('filter-sizes').getByRole('radio').nth(0)).toContainText('فقط مدرک')
      await expect(page.getByTestId('filter-apply')).toContainText('بسته')
    })

    test('C04.4 and C04.5 all filters, then chips each with a remove', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open').click()
      await page.getByTestId('filter-months').getByRole('radio').first().click()
      await page.getByTestId('filter-sizes').getByRole('radio').nth(0).click()
      await page.getByTestId('filter-apply').click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(2)
      await page.getByTestId('filter-chip').first().click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
    })

    test('C04.4 back closes with filters not applied', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open').click()
      await page.getByTestId('filter-sizes').getByRole('radio').nth(0).click()
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('filter-sheet')).toHaveCount(0)
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
    })

    test('C04.7 no results with filters: clear and alert', async ({ page }) => {
      await mockCourier(page, { 'GET requests/': (route) => (new URL(route.request().url()).searchParams.get('size') ? { status: 200, body: { items: [], total: 0, next_offset: null } } : { status: 200, body: fx('travelers') }) })
      await page.goto(COURIER(`?${LIST}&size=3p`))
      await expect(page.getByTestId('filter-empty')).toContainText('مسافری با این فیلترها نیست')
      await page.getByTestId('filters-clear').click()
      await expect(cards(page).first()).toBeVisible()
    })

    test('C04.8 counts fail: options work with no numbers', async ({ page }) => {
      await mockCourier(page, { 'requests/facets/': coded(500, 'server_error') })
      await page.goto(COURIER(`?${LIST}`))
      await page.getByTestId('filter-open-size').click()
      await expect(page.getByTestId('filter-apply')).toHaveText('نمایش نتایج')
      await expect(page.getByTestId('filter-sizes').getByRole('radio').nth(1)).toBeEnabled()
    })

    test('C04 a bad month or size in the URL drops out', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER(`?${LIST}&month=1999-01&size=huge`))
      await expect(cards(page).first()).toBeVisible()
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
      const sent = calls.find((call) => call.path === 'requests/')!.query
      expect(sent).not.toContain('month')
      expect(sent).not.toContain('size')
    })
  })

  test.describe(`C10 on ${size}`, () => {
    test.use({ viewport })

    const openSheet = async (page: Page, extra = LIST) => {
      await page.goto(COURIER(`?${extra}`))
      await page.getByTestId('alert-button').click()
      return page.getByTestId('alert-sheet')
    }

    test('C10.1 and C10.2 summary, saved, the button goes on', async ({ page }) => {
      const on = { ...fx('alertSaved').alert }
      let alerts = fx('alerts')
      const calls = await mockCourier(page, {
        'GET me/alerts/': () => ({ status: 200, body: alerts }),
        'POST me/alerts/': () => {
          alerts = { ...alerts, alerts: [...alerts.alerts, on] } as typeof alerts
          return { status: 201, body: fx('alertSaved') }
        },
      })
      const sheet = await openSheet(page, `${LIST}&size=3`)
      await expect(sheet).toContainText('تورنتو ← تهران')
      await expect(sheet).toContainText('بسته دارم')
      await expect(sheet).toContainText('۱ تا ۳ کیلو')
      await sheet.getByTestId('alert-save').click()
      await expect(page.getByTestId('toast')).toContainText('گوش به زنگ روشن شد')
      await expect(page.getByTestId('alert-button-on')).toBeVisible()
      expect(calls.find((call) => call.method === 'POST' && call.path === 'me/alerts/')?.body).toEqual({ side: 'DEMAND', origin: 'CA:toronto', destination: 'IR:tehran', month: '', size: '3' })
    })

    test('C10.3 needs a full route, picked in the sheet', async ({ page }) => {
      await mockCourier(page)
      const sheet = await openSheet(page, 'side=DEMAND')
      await expect(sheet.getByTestId('alert-need-route')).toBeVisible()
      await expect(sheet.getByTestId('alert-save')).toBeDisabled()
      await sheet.getByTestId('route-from').click()
      const route = page.getByTestId('route-sheet')
      await expect(route.getByTestId('anywhere')).toHaveCount(0)
      await route.getByTestId('country-CA').click()
      await route.getByTestId('city-toronto').click()
      await route.getByTestId('country-IR').click()
      await route.getByTestId('city-tehran').click()
      await expect(page.getByTestId('alert-sheet').getByTestId('alert-save')).toBeEnabled()
    })

    test('C10.4 cap: goes to manage', async ({ page }) => {
      await mockCourier(page, { 'POST me/alerts/': coded(409, 'alert_cap') })
      const sheet = await openSheet(page)
      await sheet.getByTestId('alert-save').click()
      await expect(sheet.getByTestId('alert-cap')).toContainText('حداکثر ۵ گوش به زنگ دارید')
      await sheet.getByTestId('alert-manage').click()
      await page.waitForURL(/\/alerts/)
    })

    test('C10.5 same route re-enabled', async ({ page }) => {
      await mockCourier(page, { 'POST me/alerts/': { status: 200, body: { ...fx('alertSaved'), created: false } } })
      const sheet = await openSheet(page)
      await sheet.getByTestId('alert-save').click()
      await expect(page.getByTestId('toast')).toContainText('گوش به زنگ دوباره روشن شد')
    })

    test('C10.6 saved while sending is off', async ({ page }) => {
      await mockCourier(page, { 'POST me/alerts/': { status: 201, body: { ...fx('alertSaved'), sending_on: false } } })
      const sheet = await openSheet(page)
      await sheet.getByTestId('alert-save').click()
      await expect(page.getByTestId('toast')).toContainText('ذخیره شد. فرستادن خبرها به‌زودی روشن می‌شود.')
    })

    test('C10.7 save fails: toast with retry, the button stays off', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'POST me/alerts/': () => (fail ? coded(500, 'server_error') : { status: 201, body: fx('alertSaved') }) })
      const sheet = await openSheet(page)
      await sheet.getByTestId('alert-save').click()
      await expect(page.getByTestId('toast')).toContainText('گوش به زنگ ذخیره نشد')
      await expect(page.getByTestId('alert-button')).toBeVisible()
      fail = false
      await page.getByTestId('toast').getByRole('button').click()
      await expect(page.getByTestId('toast').filter({ hasText: 'گوش به زنگ روشن شد' })).toBeVisible()
    })
  })
}
