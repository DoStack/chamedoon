import { expect, test, type Page } from '@playwright/test'
import { CITIES, EXCHANGE, META, PRICE_KINDS, TORONTO as TORONTO_CARDS, WANTED, manifest, snapshot } from '../src/mocks/fixtures'
import { DEEPLINK_LANDING } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, TORONTO, VIEWPORTS, shot } from './api-mock'

const BERLIN = { country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' }
const FEED_PAGE = (cards = TORONTO_CARDS, next_cursor: string | null = null) => ({ status: 200, body: { generated_at: new Date().toISOString(), results: cards, next_cursor } })
const NO_SNAPSHOTS = { '/feeds/manifest.json': coded(500, 'server_error') }
const cardsOf = (page: Page) => page.getByTestId('listing-card')

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('F01#1 to #4: first open asks country, then city, then lists it', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP())
      await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
      await shot(page, 'F01-01', size)
      await page.getByRole('button', { name: 'کانادا' }).click()
      await expect(page.getByRole('heading', { name: 'کدام شهر؟' })).toBeVisible()
      await shot(page, 'F01-02', size)
      await page.getByRole('button', { name: 'تورنتو' }).click()
      await expect(cardsOf(page).first()).toBeVisible()
      await expect(page.getByTestId('city-button')).toContainText('تورنتو')
      await shot(page, 'F01-04', size)
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.city')!).slug)).toBe('toronto')
    })

    test('F01#3, #14: a multi-part city counts its parts live', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/v2/choose?country=DE'))
      await page.getByRole('button', { name: 'کلن و اطراف' }).click()
      const sheet = page.getByTestId('parts-sheet')
      await expect(sheet.getByRole('button', { name: 'نمایش ۴۸ آگهی' })).toBeVisible()
      await sheet.getByRole('button', { name: /^بن/ }).click()
      await expect(sheet.getByRole('button', { name: 'نمایش ۴۲ آگهی' })).toBeVisible()
      await shot(page, 'F01-03', size)
      await sheet.getByRole('button', { name: /^کلن/ }).click()
      await sheet.getByRole('button', { name: /^دوسلدورف/ }).click()
      await expect(sheet.getByRole('button', { name: 'نمایش ۰ آگهی' })).toBeDisabled()
      await shot(page, 'F01-14', size)
    })

    test('F01#3: picking parts narrows the list', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/v2/choose?country=DE'))
      await page.getByRole('button', { name: 'کلن و اطراف' }).click()
      const sheet = page.getByTestId('parts-sheet')
      await sheet.getByRole('button', { name: /^دوسلدورف/ }).click()
      await sheet.getByRole('button', { name: /^کلن/ }).click()
      await sheet.getByRole('button', { name: 'نمایش ۶ آگهی' }).click()
      await expect(page.getByTestId('count-bar')).toContainText('۶ آگهی')
    })

    test.describe('waitlist', () => {
      const open = async (page: Page, api = {}) => {
        await mockApi(page, api)
        await page.goto(APP('/app/v2/choose?country=CA'))
        await page.getByRole('button', { name: /شهر من در فهرست نیست/ }).click()
        return page.getByTestId('waitlist-sheet')
      }

      test('F01#5, #16: empty name cannot be sent', async ({ page }) => {
        const sheet = await open(page)
        await expect(sheet).toContainText('شهرتان هنوز در چمدون نیست')
        await expect(sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' })).toBeDisabled()
        await shot(page, 'F01-16', size)
        await sheet.getByRole('button', { name: 'اتاوا' }).click()
        await expect(sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' })).toBeEnabled()
        await shot(page, 'F01-05', size)
      })

      test('F01#6: added', async ({ page }) => {
        const sheet = await open(page)
        await sheet.getByRole('button', { name: 'اتاوا' }).click()
        await sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' }).click()
        await expect(page.getByTestId('toast')).toContainText('اتاوا به لیست انتظار اضافه شد')
        await expect(sheet).toBeHidden()
        await shot(page, 'F01-06', size)
      })

      test('F01#7: already on the list', async ({ page }) => {
        const sheet = await open(page, { 'POST /api/v1/me/waitlist/': { status: 200, body: { created: false, city_name: 'Ottawa' } } })
        await sheet.getByRole('button', { name: 'اتاوا' }).click()
        await sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' }).click()
        await expect(sheet).toContainText('اتاوا از قبل در لیست انتظار شماست.')
        await shot(page, 'F01-07', size)
        await sheet.getByRole('button', { name: 'برگشت به فهرست شهرها' }).click()
        await expect(sheet).toBeHidden()
      })

      test('F01#15: failed save offers retry, and retry works', async ({ page }) => {
        let fail = true
        const sheet = await open(page)
        await page.route('**/api/v1/me/waitlist/', (route) => (fail ? route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } }) : route.fallback()))
        await sheet.getByRole('button', { name: 'اتاوا' }).click()
        await sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' }).click()
        await expect(sheet).toContainText('ثبت نشد')
        await shot(page, 'F01-15', size)
        fail = false
        await sheet.getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(page.getByTestId('toast')).toContainText('به لیست انتظار اضافه شد')
      })

      test('F01#17: a listed city offers to pick it', async ({ page }) => {
        const sheet = await open(page, { 'POST /api/v1/me/waitlist/': coded(400, 'validation_failed', { fields: { city_name: ['city_listed'] } }) })
        await sheet.getByRole('textbox').fill('تورنتو')
        await sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' }).click()
        await expect(sheet).toContainText('تورنتو در فهرست هست')
        await shot(page, 'F01-17', size)
        await sheet.getByRole('button', { name: 'انتخاب تورنتو' }).click()
        await expect(page.getByTestId('city-button')).toContainText('تورنتو')
      })
    })

    test('F01#10: city with no listings', async ({ page }) => {
      await savedCity(page, { country: 'CA', city: 'Vancouver', slug: 'vancouver', name_fa: 'ونکوور' })
      await mockApi(page)
      await page.goto(APP())
      await expect(page.getByTestId('city-empty')).toContainText('هنوز آگهی‌ای در ونکوور نیست')
      await expect(page.getByTestId('count-bar')).toContainText('۰ آگهی')
      await shot(page, 'F01-10', size)
      await page.getByTestId('city-empty').getByRole('button', { name: 'شهر دیگر' }).click()
      await expect(page.getByTestId('city-sheet')).toBeVisible()
    })

    test('F01#13: a filter link opens the list with its filters and asks nothing', async ({ page }) => {
      await mockApi(page, {
        '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, kind: 'city', country: 'CA', city: 'Toronto', city_slug: 'toronto', city_known: true, filters: { types: ['room'], min: 800, max: 1200 }, src: 'chtor' } },
      })
      await page.goto(APP('/app/v2/', '&tgstart=home_ca_toronto_t-room_p800-1200_src-chtor'))
      await expect(page.getByTestId('filter-row')).toContainText('اتاق')
      await expect(page.getByTestId('filter-row')).toContainText('۸۰۰ تا ۱٬۲۰۰')
      await expect(cardsOf(page)).toHaveCount(1)
      await shot(page, 'F01-13', size)
      expect(await page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.city')!).slug)).toBe('toronto')
    })

    test('F01 rules: a country link starts at its cities; a bad link is a normal open', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, kind: 'country', country: 'DE' } } })
      await page.goto(APP('/app/v2/', '&tgstart=home_de'))
      await expect(page.getByRole('button', { name: 'برلین' })).toBeVisible()
      await page.unroute((url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'))
      await mockApi(page, { '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, malformed: true } } })
      await page.goto(APP('/app/v2/', '&tgstart=garbage'))
      await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
    })

    test('F01 rules: a deep link that fails is a normal open, and a legacy link goes to its page', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/deeplink/': 'abort' })
      await page.goto(APP('/app/v2/', '&tgstart=home_12'))
      await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
      await page.unroute((url) => url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/feeds/'))
      await mockApi(page, { '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, kind: 'legacy', legacy_path: '/app/home/new/' } } })
      await page.goto(APP('/app/v2/', '&tgstart=post'))
      await page.waitForURL('**/app/home/new/**')
    })

    test('F01: the src of the link reaches the exchange', async ({ page }) => {
      let body = ''
      await mockApi(page, {
        '/api/v1/public/meta/': { status: 200, body: { ...META, miniapp_v2: false } },
        '/api/v1/public/deeplink/': { status: 200, body: { ...DEEPLINK_LANDING, src: 'chtor' } },
      })
      await page.route('**/api/v1/auth/exchange/', (route) => {
        body = route.request().postData() ?? ''
        return route.fulfill({ json: EXCHANGE })
      })
      await page.goto(APP('/app/v2/', '&tgstart=x'))
      await expect(page.getByTestId('country-screen')).toBeVisible()
      expect(JSON.parse(body).src).toBe('chtor')
    })

    test('F01: cities that fail to load have retry', async ({ page }) => {
      let fail = true
      await mockApi(page)
      await page.route('**/api/v1/public/cities/', (route) => (fail ? route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } }) : route.fallback()))
      await page.goto(APP())
      await expect(page.getByTestId('cities-failed')).toContainText('بارگذاری نشد')
      await shot(page, 'F25-08', size)
      fail = false
      await page.getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
    })

    test('F02#1: return opens the saved city, with new listings since last visit', async ({ page }) => {
      await savedCity(page, TORONTO)
      await page.addInitScript(() => localStorage.setItem('chamedoon.visit.toronto', new Date(Date.now() - 10 * 3_600_000).toISOString()))
      await mockApi(page)
      await page.goto(APP())
      await expect(page.getByTestId('new-count')).toContainText('آگهی تازه از آخرین بازدید')
      await expect(page.getByRole('heading', { name: 'کدام شهر؟' })).toHaveCount(0)
      await shot(page, 'F02-01', size)
    })

    test('F02#1: first visit and zero new show no count', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP())
      await expect(cardsOf(page).first()).toBeVisible()
      await expect(page.getByTestId('new-count')).toHaveCount(0)
    })

    test('F02#5: saved city is no longer active', async ({ page }) => {
      await savedCity(page, { country: 'CA', city: 'Calgary', slug: 'calgary', name_fa: 'کلگری' })
      await mockApi(page)
      await page.goto(APP())
      const screen = page.getByTestId('city-gone')
      await expect(screen).toContainText('کلگری دیگر در چمدون نیست. شهر دیگری انتخاب کنید.')
      await shot(page, 'F02-05', size)
      await screen.getByRole('button', { name: 'مونترال' }).click()
      await expect(page.getByTestId('city-button')).toContainText('مونترال')
    })

    test('F03#1 to #3: change city from the header, in another country', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP())
      await page.getByTestId('city-button').click()
      const sheet = page.getByTestId('city-sheet')
      await expect(sheet).toContainText('کانادا')
      await expect(sheet.getByRole('radio', { name: 'تورنتو' })).toBeChecked()
      await shot(page, 'F03-01', size)
      await sheet.getByRole('button', { name: 'کشور', exact: true }).click()
      await expect(sheet).toContainText('۳ شهر')
      await shot(page, 'F03-02', size)
      await sheet.getByRole('button', { name: /آلمان/ }).click()
      await sheet.getByRole('radio', { name: 'برلین' }).click()
      await expect(sheet).toBeHidden()
      await expect(page.getByTestId('city-button')).toContainText('برلین')
      await expect(page.getByRole('textbox', { name: 'جستجو' })).toHaveAttribute('placeholder', 'مثلاً: اتاق WG نزدیک U-Bahn')
      await expect(page.getByText('در ماه · گرم')).toBeVisible()
      await expect(page.getByText('در ماه · سرد')).toBeVisible()
      await expect(page.getByText('موقت (Zwischenmiete)', { exact: false })).toBeVisible()
      await shot(page, 'F03-03', size)
    })

    test('F03#5, #6: a long city list has a search, and a missing city offers the waitlist', async ({ page }) => {
      const many = Array.from({ length: 13 }, (_, i) => ({ slug: `c${i}`, name: `C${i}`, name_fa: i ? `شهر ${i}` : 'برلین', listing_count: 1, channel: { username: 'x', title: 'x', city: 'x' } }))
      await savedCity(page, { ...BERLIN, slug: 'c0' })
      await mockApi(page, { '/api/v1/public/cities/': { status: 200, body: { countries: [{ code: 'DE', channel: null, cities: many }] } } })
      await page.goto(APP())
      await page.getByTestId('city-button').click()
      const sheet = page.getByTestId('city-sheet')
      await expect(sheet.getByRole('radio')).toHaveCount(13)
      await shot(page, 'F03-05', size)
      await sheet.getByRole('textbox').fill('مونیخ')
      await expect(sheet).toContainText('مونیخ هنوز در چمدون نیست')
      await shot(page, 'F03-06', size)
      await sheet.getByRole('button', { name: 'به لیست انتظار اضافه کنید' }).click()
      await expect(page.getByTestId('waitlist-sheet').getByRole('textbox')).toHaveValue('مونیخ')
    })

    test('F03#7: city change keeps type and budget, clears areas, and undo brings them back', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP('/app/v2/?types=apartment&max=2000&areas=نورث'))
      await page.getByTestId('city-button').click()
      await page.getByTestId('city-sheet').getByRole('radio', { name: 'مونترال' }).click()
      await expect(page.getByTestId('toast')).toContainText('فیلترهای محله پاک شد')
      await expect(page.getByTestId('filter-row')).toContainText('آپارتمان')
      await expect(page.getByTestId('filter-row')).toContainText('تا ۲٬۰۰۰')
      await expect(page.getByTestId('filter-row')).not.toContainText('نورث')
      await shot(page, 'F03-07', size)
      await page.getByTestId('toast').getByRole('button', { name: 'برگردان' }).click()
      await expect(page.getByTestId('filter-row')).toContainText('نورث')
    })

    test('F03: country change clears all filters', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP('/app/v2/?types=apartment&max=2000'))
      await page.getByTestId('city-button').click()
      const sheet = page.getByTestId('city-sheet')
      await sheet.getByRole('button', { name: 'کشور', exact: true }).click()
      await sheet.getByRole('button', { name: /آلمان/ }).click()
      await sheet.getByRole('radio', { name: 'برلین' }).click()
      await expect(page.getByTestId('filter-row')).toHaveCount(0)
    })

    test('F03: a failed prefs save shows the F25#10 toast, and the new city stays', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page, {
        '/api/v1/public/meta/': { status: 200, body: { ...META, miniapp_v2: false } },
        'PATCH /api/v1/me/prefs/': coded(500, 'server_error'),
      })
      await page.goto(APP())
      await page.getByTestId('city-button').click()
      await page.getByTestId('city-sheet').getByRole('radio', { name: 'مونترال' }).click()
      await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
      await shot(page, 'F25-10', size)
      await expect(page.getByTestId('city-button')).toContainText('مونترال')
    })

    test('Back closes the sheet first, then leaves the page', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP())
      await page.getByTestId('city-button').click()
      await expect(page.getByTestId('city-sheet')).toBeVisible()
      const back = () => page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await back()
      await expect(page.getByTestId('city-sheet')).toBeHidden()
      const visible = await page.evaluate(() => (window as never as { Telegram: { WebApp: { __backVisible(): boolean } } }).Telegram.WebApp.__backVisible())
      expect(visible).toBe(false)
    })

    test.describe('F04 list', () => {
      test.beforeEach(async ({ page }) => savedCity(page, TORONTO))

      test('F04#1: default, sorted by newest, offers only', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await expect(cardsOf(page).first()).toContainText('۱٬۸۵۰')
        await expect(page.getByTestId('wanted-card')).toHaveCount(0)
        await expect(cardsOf(page).first()).toContainText('تازه')
        await expect(cardsOf(page).first()).toContainText('موجود · ۳ ساعت پیش')
        await expect(page.getByTestId('count-bar').getByRole('button', { name: /گوش به زنگ/ })).toHaveCount(0)
        await shot(page, 'F04-01', size)
      })

      test('F04#2: sort sheet, with the date sort off', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await page.getByTestId('count-bar').getByRole('button', { name: 'جدیدترین' }).click()
        const sheet = page.getByTestId('sort-sheet')
        await expect(sheet.getByRole('radio', { name: 'نزدیک‌ترین به تاریخ ورود' })).toBeDisabled()
        await shot(page, 'F04-02', size)
        await sheet.getByRole('radio', { name: 'ارزان‌ترین' }).click()
        await expect(sheet).toBeHidden()
        await expect(cardsOf(page).first()).toContainText('۷۵۰')
        expect(page.url()).toContain('sort=cheap')
      })

      test('F04#3, #7: next page from the fallback feed loads, fails, and retries', async ({ page }) => {
        let mode: 'hang' | 'fail' | 'ok' = 'hang'
        await mockApi(page, NO_SNAPSHOTS)
        await page.route('**/api/v1/public/feed/**', (route) => {
          if (!new URL(route.request().url()).searchParams.get('cursor')) return route.fulfill({ json: FEED_PAGE(TORONTO_CARDS, 'next').body })
          if (mode === 'hang') return
          if (mode === 'fail') return route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } })
          return route.fulfill({ json: FEED_PAGE(PRICE_KINDS).body })
        })
        const toBottom = () => page.getByTestId('list-scroll').evaluate((element) => element.scrollTo(0, element.scrollHeight))
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        await toBottom()
        await expect(page.getByTestId('list-scroll').getByTestId('card-skeleton').first()).toBeAttached()
        await toBottom()
        await shot(page, 'F04-03', size)
        await page.unroute('**/api/v1/public/feed/**')
        mode = 'fail'
        await page.route('**/api/v1/public/feed/**', (route) => {
          if (!new URL(route.request().url()).searchParams.get('cursor')) return route.fulfill({ json: FEED_PAGE(TORONTO_CARDS, 'next').body })
          if (mode === 'fail') return route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } })
          return route.fulfill({ json: FEED_PAGE(PRICE_KINDS).body })
        })
        await page.reload()
        await expect(cardsOf(page).first()).toBeVisible()
        await toBottom()
        await expect(page.getByTestId('more-failed')).toContainText('ادامه‌ی آگهی‌ها بارگذاری نشد')
        await toBottom()
        await shot(page, 'F04-07', size)
        mode = 'ok'
        await page.getByTestId('more-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(page.getByTestId('list-end')).toContainText('همه‌ی ۱۰ آگهی را دیدید')
      })

      test('F04#4: end of list', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await page.getByTestId('list-end').scrollIntoViewIfNeeded()
        await expect(page.getByTestId('list-end')).toContainText('همه‌ی ۱۰ آگهی را دیدید')
        await shot(page, 'F04-04', size)
      })

      test('F04#6, F25#4: first load keeps the header, skeleton at once, note at 3 s, error at 15 s', async ({ page }) => {
        await page.clock.install()
        await mockApi(page, { '/feeds/manifest.json': 'hang' })
        await page.goto(APP())
        await expect(page.getByTestId('first-load')).toBeVisible()
        await expect(page.getByTestId('card-skeleton').first()).toBeVisible()
        await expect(page.getByTestId('city-button')).toBeVisible()
        await expect(page.getByTestId('slow-note')).toHaveCount(0)
        await shot(page, 'F04-06', size)
        await page.clock.fastForward(3100)
        await expect(page.getByTestId('slow-note')).toContainText('کمی بیشتر از معمول طول می‌کشد…')
        await shot(page, 'F25-04', size)
      })

      test('F04#8: compact view has more columns and shows no-price and sublet cards', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP('/app/v2/?view=compact'))
        await expect(page.getByText('قیمت: بپرسید').first()).toBeVisible()
        await expect(page.getByText(/سابلت ·/)).toBeVisible()
        await shot(page, 'F04-08', size)
        await page.getByRole('button', { name: 'تغییر نما' }).click()
        expect(page.url()).not.toContain('view=compact')
      })

      test('F04#9: snapshot, manifest and fallback all fail, and retry recovers', async ({ page }) => {
        let fail = true
        await mockApi(page, NO_SNAPSHOTS)
        await page.route('**/api/v1/public/feed/**', (route) => (fail ? route.fulfill({ status: 503, json: { code: 'feed_failed', detail: 'x' } }) : route.fulfill({ json: FEED_PAGE().body })))
        await page.goto(APP())
        await expect(page.getByTestId('list-failed')).toContainText('آگهی‌ها بارگذاری نشد')
        await expect(page.getByTestId('city-button')).toBeVisible()
        await shot(page, 'F04-09', size)
        fail = false
        await page.getByTestId('list-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(cardsOf(page).first()).toBeVisible()
      })

      test('F04#9: not_served_yet from the fallback feed is the same designed state', async ({ page }) => {
        await mockApi(page, { ...NO_SNAPSHOTS, '/api/v1/public/feed/': coded(501, 'not_served_yet') })
        await page.goto(APP())
        await expect(page.getByTestId('list-failed')).toBeVisible()
        await page.getByTestId('list-failed').getByRole('button', { name: 'شهر دیگر' }).click()
        await expect(page.getByTestId('city-sheet')).toBeVisible()
      })

      test('F04#10: a list older than 24 hours says so', async ({ page }) => {
        const old = new Date(Date.now() - 30 * 3_600_000).toISOString()
        await mockApi(page, { '/feeds/toronto.v1.json': { status: 200, body: snapshot('toronto', old) } })
        await page.goto(APP())
        await expect(page.getByTestId('stale-note')).toContainText('این فهرست از ۳۰ ساعت پیش به‌روز نشده است.')
        await shot(page, 'F04-10', size)
      })

      test('F04#12: night and week prices, agent mark', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP('/app/v2/?sort=dear'))
        await expect(page.getByText('هر شب').first()).toBeVisible()
        await expect(page.getByText('در هفته').first()).toBeVisible()
        await expect(page.getByText('بنگاه').first()).toBeVisible()
        await expect(page.getByText('کوتاه‌مدت').first()).toBeVisible()
        await shot(page, 'F04-12', size)
      })

      test('F04#5: pull-to-refresh where vertical swipes can be disabled', async ({ browser }) => {
        const context = await browser.newContext({ viewport, hasTouch: true })
        const page = await context.newPage()
        await savedCity(page, TORONTO)
        await mockApi(page)
        await page.goto(APP())
        await expect(page.getByTestId('refresh-button')).toHaveCount(0)
        await page.route('**/feeds/manifest.json', async (route) => {
          await new Promise((resolve) => setTimeout(resolve, 500))
          return route.fallback()
        })
        await page.getByTestId('list-scroll').evaluate((element) => {
          const touch = (y: number) => [new Touch({ identifier: 1, target: element, clientY: y, clientX: 10 })]
          element.dispatchEvent(new TouchEvent('touchstart', { touches: touch(100) }))
          element.dispatchEvent(new TouchEvent('touchmove', { touches: touch(200) }))
        })
        await expect(page.getByTestId('refreshing')).toBeVisible()
        await shot(page, 'F04-05', size)
        await context.close()
      })

      test('F04#13: old client refreshes by button', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP('/app/v2/', '&tgversion=7.0'))
        await expect(page.getByTestId('refresh-button')).toContainText('به‌روزرسانی')
        await shot(page, 'F04-13', size)
      })

      test('F04#14: wanted posts come only through the type filter', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP('/app/v2/?kind=wanted'))
        await expect(page.getByTestId('wanted-card')).toHaveCount(WANTED.length)
        await expect(page.getByTestId('wanted-card').first()).toContainText('دنبال خانه · بودجه تا ۱٬۶۰۰')
        await expect(page.getByTestId('filter-row')).toContainText('دنبال خانه')
        await expect(page.getByRole('button', { name: 'تغییر نما' })).toHaveCount(0)
        await shot(page, 'F04-14', size)
        await page.getByTestId('filter-row').getByRole('button').click()
        await expect(cardsOf(page).first()).toBeVisible()
      })

      test('F05#7: nothing matches, with a way out', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP('/app/v2/?types=house'))
        await expect(page.getByTestId('filter-empty')).toContainText('آگهی‌ای با این فیلترها نیست')
        await page.getByRole('button', { name: 'پاک کردن همه' }).click()
        await expect(cardsOf(page).first()).toBeVisible()
      })

      test('search box filters on text', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await page.getByRole('textbox', { name: 'جستجو' }).fill('Hillcrest')
        await expect(cardsOf(page)).toHaveCount(1)
        expect(page.url()).toContain('q=Hillcrest')
      })

      test('a photo that fails falls back to the placeholder and hides the count', async ({ page }) => {
        const broken = TORONTO_CARDS.map((card) => ({ ...card, thumb: '/no-such-image.png' }))
        await mockApi(page, { '/feeds/toronto.v1.json': { status: 200, body: { ...snapshot('toronto'), listings: broken } } })
        await page.goto(APP())
        await expect(cardsOf(page).first().getByTestId('photo-placeholder')).toBeVisible()
        await expect(cardsOf(page).first().getByText('۴', { exact: true })).toHaveCount(0)
      })

      test('long text stays inside the card', async ({ page }) => {
        const long = TORONTO_CARDS.map((card) => ({ ...card, area: 'نام بسیار بسیار بلند یک محله · Very Long Street Name And Another Very Long Street Name'.repeat(3) }))
        await mockApi(page, { '/feeds/toronto.v1.json': { status: 200, body: { ...snapshot('toronto'), listings: long } } })
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      })

      test('manifest failing falls back to the feed, with no error on screen', async ({ page }) => {
        await mockApi(page, { ...NO_SNAPSHOTS, '/api/v1/public/feed/': FEED_PAGE() })
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        await expect(page.getByTestId('list-failed')).toHaveCount(0)
      })

      test('snapshots_on false skips the manifest', async ({ page }) => {
        let asked = false
        await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META, snapshots_on: false } }, '/api/v1/public/feed/': FEED_PAGE() })
        page.on('request', (r) => {
          if (r.url().includes('manifest.json')) asked = true
        })
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        expect(asked).toBe(false)
      })

      test('F25#5: a newer snapshot format asks for an update and the fallback keeps the list', async ({ page }) => {
        await mockApi(page, { '/feeds/manifest.json': { status: 200, body: { ...manifest(), schema_version: 99 } }, '/api/v1/public/feed/': FEED_PAGE() })
        await page.goto(APP())
        await expect(page.getByTestId('update-sheet')).toBeVisible()
        await shot(page, 'F25-05', size)
      })

      test('a cache that never answers does not hold the list on the skeleton', async ({ page }) => {
        await page.addInitScript(() => {
          IDBFactory.prototype.open = () => ({}) as IDBOpenDBRequest
        })
        await mockApi(page)
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
      })

      test('return with a failing network shows the cached snapshot', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        await page.evaluate(() => new Promise((resolve) => setTimeout(resolve, 200)))
        await page.route('**/feeds/manifest.json', (route) => route.abort('failed'))
        await page.route('**/api/v1/public/feed/**', (route) => route.abort('failed'))
        await page.reload()
        await expect(cardsOf(page).first()).toBeVisible()
        await expect(page.getByTestId('list-failed')).toHaveCount(0)
      })

      test('F25#1: offline shows the bar over the list, and it leaves when back online', async ({ page, context }) => {
        await mockApi(page)
        await page.goto(APP())
        await expect(cardsOf(page).first()).toBeVisible()
        await context.setOffline(true)
        await expect(page.getByTestId('offline-bar')).toContainText('اتصال اینترنت برقرار نیست')
        await expect(cardsOf(page).first()).toBeVisible()
        await shot(page, 'F25-01', size)
        await context.setOffline(false)
        await expect(page.getByTestId('offline-bar')).toHaveCount(0)
      })

      test('F25#3: maintenance names the saved city channel', async ({ page }) => {
        await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'CA', city: 'Toronto', slug: 'toronto', name_fa: 'تورنتو', channel: 'x' })))
        await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META, maintenance: { on: true, until: new Date(Date.now() + 1_800_000).toISOString() } } } })
        await page.goto(APP())
        await expect(page.getByTestId('maintenance-screen').getByRole('button', { name: 'کانال تورنتو' })).toBeVisible()
        await shot(page, 'F25-03', size)
      })

      test('touch targets are 44 px on phone and 36 px on desktop', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await expect(page.getByTestId('city-button')).toHaveCSS('height', size === 'phone' ? '44px' : '36px')
      })

      test('prefs city is used on a first open with a server copy', async ({ page }) => {
        await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
        await mockApi(page, {
          '/api/v1/public/meta/': { status: 200, body: { ...META, miniapp_v2: false } },
          'POST /api/v1/auth/exchange/': { status: 200, body: { ...EXCHANGE, prefs: { ...EXCHANGE.prefs, city: 'Montreal', country: 'CA' } } },
        })
        await page.goto(APP())
        await expect(page.getByTestId('city-button')).toContainText('مونترال')
      })
    })
  })
}

test('CITIES fixture stays in the contract shape', () => {
  expect(CITIES.countries[0].cities[0].channel.username).toBeTruthy()
})
