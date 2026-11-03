import { expect, test, type Page } from '@playwright/test'
import { CANADA_CHANNEL, CONTACT, DEEPLINK_LANDING, EXCHANGE, META, PRICE_KINDS, TORONTO as CARDS, WANTED, listing, snapshot } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const ID = CARDS[0].id
const DETAIL = `/api/v1/public/listings/${ID}/`
const CONTACT_URL = `POST /api/v1/me/listings/${ID}/contact/`
const VIEWER = `/api/v1/me/listings/${ID}/`
const OK = (body: unknown) => ({ status: 200, body })
const open = (page: Page, extra = '') => page.goto(APP(`/app/v2/listing/${ID}`, extra))
const NOT_MEMBER = coded(403, 'not_member', { join_url: 'https://t.me/+join', channel: { username: 'x', title: 'کانال چمدون - خانه · تورنتو', city: 'Toronto' } })
const WARNED = (page: Page) => page.addInitScript(() => localStorage.setItem('chamedoon.warned', '1'))
const opened = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)
const pressContact = (page: Page) => page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
const pressBack = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test.describe('F10 detail', () => {
      test('F10#1, #2: facts, rental type, description, about, region rule', async ({ page }) => {
        await mockApi(page)
        await open(page)
        const detail = page.getByTestId('detail')
        await expect(detail).toContainText('۱٬۸۵۰')
        await expect(detail.getByRole('heading', { level: 1 })).toContainText('آپارتمان ۱ خوابه')
        await expect(detail).toContainText('موجود · ۳ ساعت پیش')
        await expect(page.getByTestId('facts')).toContainText('مبله')
        await expect(page.getByTestId('facts')).toContainText('داخل قیمت')
        await shot(page, 'F10-01', size)
        await page.getByTestId('region-rule').scrollIntoViewIfNeeded()
        await expect(detail).toContainText('بلندمدت · حداقل ۱۲ ماه')
        await expect(page.getByTestId('about')).toContainText('اولین بار دیده شد')
        await expect(page.getByTestId('about').getByRole('button', { name: 'دیدن پست اصلی' })).toBeVisible()
        await expect(page.getByTestId('region-rule')).toContainText('در انتاریو')
        await shot(page, 'F10-02', size)
      })

      test('F10#3: viewer opens, moves, and Back closes it first', async ({ page }) => {
        await mockApi(page)
        await open(page)
        await page.getByRole('button', { name: 'نمایش عکس‌ها' }).click()
        const viewer = page.getByTestId('viewer')
        await expect(viewer).toContainText('۱ از ۴')
        await viewer.getByRole('button', { name: 'عکس ۲' }).click()
        await expect(viewer).toContainText('۲ از ۴')
        await shot(page, 'F10-03', size)
        await pressBack(page)
        await expect(viewer).toBeHidden()
        await expect(page.getByTestId('detail')).toBeVisible()
      })

      test('F10#17: viewer photo fails, with retry', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { photos: [{ url: '/no-such.png', thumb: '/no-such.png' }, { url: '/no-such-2.png', thumb: '/no-such-2.png' }] })) })
        await open(page)
        await page.getByRole('button', { name: 'نمایش عکس‌ها' }).click()
        await expect(page.getByTestId('viewer-failed')).toContainText('عکس بارگذاری نشد')
        await shot(page, 'F10-17', size)
        await page.getByTestId('viewer-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(page.getByTestId('viewer-failed')).toBeVisible()
      })

      test('F10#4: no price asks through the same gate', async ({ page }) => {
        await WARNED(page)
        const noPrice = PRICE_KINDS[3]
        await mockApi(page, { [`/api/v1/public/listings/${noPrice.id}/`]: OK(listing(noPrice, { photos: [] })), [`POST /api/v1/me/listings/${noPrice.id}/contact/`]: NOT_MEMBER })
        await page.goto(APP(`/app/v2/listing/${noPrice.id}`))
        await expect(page.getByText('قیمت: بپرسید')).toBeVisible()
        await shot(page, 'F10-04', size)
        await page.locator('[data-testid="actions"]:visible').getByRole('button', { name: 'قیمت را بپرسید' }).click()
        await expect(page.getByTestId('gate-sheet')).toBeVisible()
      })

      test('F10#5: sublet shows dates and the contract warning', async ({ page }) => {
        const sublet = CARDS[2]
        await mockApi(page)
        await page.goto(APP(`/app/v2/listing/${sublet.id}`))
        await expect(page.getByTestId('facts')).toContainText('شروع')
        await expect(page.getByTestId('facts')).toContainText('۱ دسامبر')
        await expect(page.getByTestId('facts')).toContainText('پایان')
        await expect(page.getByTestId('facts')).toContainText('مدت')
        await expect(page.getByTestId('sublet-note')).toContainText('قرارداد به نام مستأجر فعلی است')
        await shot(page, 'F10-05', size)
      })

      for (const [reason, text, state] of [
        ['owner', 'آگهی‌دهنده خبر داد', 'F10-06'],
        ['users', 'کاربران خبر دادند', 'F02-03'],
        ['expired', 'مدتی است دیده نشده است', 'F10-06b'],
      ] as const) {
        test(`F10#6, F02#3: closed by ${reason} says so, with no contact and no source post`, async ({ page }) => {
          await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { state: 'closed', closed_reason: reason })) })
          await open(page)
          await expect(page.getByTestId('closed-note')).toContainText(text)
          await expect(page.getByRole('button', { name: 'تماس' })).toHaveCount(0)
          await expect(page.getByRole('button', { name: 'دیدن پست اصلی' })).toHaveCount(0)
          await expect(page.getByText('آگهی‌های مشابه', { exact: true })).toBeVisible()
          await shot(page, state, size)
          await page.locator('.app-footer:visible, [data-testid="detail"]').getByRole('button', { name: 'دیدن آگهی‌های مشابه' }).first().click()
          await expect(page.getByTestId('count-bar')).toBeVisible()
          expect(page.url()).toContain('types=apartment')
        })
      }

      test('F10#7: no photo', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { photos: [], photo_count: 0 })) })
        await open(page)
        await expect(page.getByText('بدون عکس')).toBeVisible()
        await expect(page.getByRole('button', { name: 'نمایش عکس‌ها' })).toHaveCount(0)
        await shot(page, 'F10-07', size)
      })

      test('F10#8: detail fails, with retry and a way back', async ({ page }) => {
        let fail = true
        await mockApi(page)
        await page.route(`**${DETAIL}`, (route) => (fail ? route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } }) : route.fallback()))
        await open(page)
        await expect(page.getByTestId('detail-failed')).toContainText('آگهی باز نشد')
        await shot(page, 'F10-08', size)
        fail = false
        await page.getByRole('button', { name: 'تلاش مجدد' }).click()
        await expect(page.getByTestId('detail')).toBeVisible()
      })

      test('F10#8: back from the failure goes to results', async ({ page }) => {
        await mockApi(page, { [DETAIL]: coded(500, 'server_error') })
        await open(page)
        await page.getByRole('button', { name: 'برگشت به نتایج' }).click()
        await expect(page.getByTestId('count-bar')).toBeVisible()
      })

      test('F10#10: wanted post has no gallery and goes through the gate', async ({ page }) => {
        await WARNED(page)
        const wanted = WANTED[0]
        await mockApi(page, { [`POST /api/v1/me/listings/${wanted.id}/contact/`]: NOT_MEMBER })
        await page.goto(APP(`/app/v2/listing/${wanted.id}`))
        const detail = page.getByTestId('wanted-detail')
        await expect(detail.getByRole('heading', { level: 1 })).toContainText('دنبال خانه · بودجه تا ۱٬۶۰۰')
        await expect(page.getByTestId('facts')).toContainText('محله‌ها')
        await expect(page.getByRole('button', { name: 'نمایش عکس‌ها' })).toHaveCount(0)
        await shot(page, 'F10-10', size)
        await pressContact(page)
        await expect(page.getByTestId('gate-sheet')).toBeVisible()
      })

      test('F10#11: closed wanted post', async ({ page }) => {
        const wanted = WANTED[0]
        await mockApi(page, { [`/api/v1/public/listings/${wanted.id}/`]: OK(listing(wanted, { state: 'closed', closed_reason: 'owner' })) })
        await page.goto(APP(`/app/v2/listing/${wanted.id}`))
        await expect(page.getByTestId('wanted-detail')).toContainText('این درخواست بسته شد.')
        await expect(page.getByRole('button', { name: 'تماس' })).toHaveCount(0)
        await expect(page.getByRole('button', { name: 'برگشت به آگهی‌ها' })).toBeVisible()
        await shot(page, 'F10-11', size)
      })

      test('F10#12: night price with dates and nights', async ({ page }) => {
        const night = PRICE_KINDS[0]
        await mockApi(page, { [`/api/v1/public/listings/${night.id}/`]: OK(listing(night, { available_from: '2026-12-01', available_to: '2026-12-15' })) })
        await page.goto(APP(`/app/v2/listing/${night.id}`))
        await expect(page.getByTestId('detail')).toContainText('هر شب')
        await expect(page.getByTestId('facts')).toContainText('۱۴ شب')
        await expect(page.getByTestId('marks')).toContainText('کوتاه‌مدت')
        await shot(page, 'F10-12', size)
      })

      test('F10#13: German listing shows only what it states', async ({ page }) => {
        const de = snapshot('berlin').listings[0]
        await mockApi(page, { [`/api/v1/public/listings/${de.id}/`]: OK(listing(de, { registration: 'yes', deposit_months: 2, wbs_required: true, price_basis: 'cold', available_from: '2027-01-01', available_to: '2027-06-30' })) })
        await page.goto(APP(`/app/v2/listing/${de.id}`))
        await expect(page.getByTestId('detail')).toContainText('در ماه · سرد')
        await expect(page.getByTestId('facts')).toContainText('ملد')
        await expect(page.getByTestId('facts')).toContainText('ممکن است')
        await expect(page.getByTestId('facts')).toContainText('۲ ماه')
        await expect(page.getByTestId('marks')).toContainText('موقت (Zwischenmiete)')
        await expect(page.getByTestId('marks')).toContainText('نیاز به WBS')
        await expect(page.getByTestId('region-rule')).toHaveCount(0)
        await shot(page, 'F10-13', size)
      })

      test('F10#14: agent mark', async ({ page }) => {
        const agent = PRICE_KINDS[2]
        await mockApi(page)
        await page.goto(APP(`/app/v2/listing/${agent.id}`))
        await expect(page.getByTestId('marks')).toContainText('بنگاه')
        await shot(page, 'F10-14', size)
      })

      test('F10#15: Quebec rule, never Ontario text', async ({ page }) => {
        const qc = snapshot('montreal').listings[0]
        await mockApi(page)
        await page.goto(APP(`/app/v2/listing/${qc.id}`))
        await expect(page.getByTestId('region-rule')).toContainText('در کبک مالک نمی‌تواند ودیعه بگیرد.')
        await expect(page.getByTestId('detail')).not.toContainText('انتاریو')
        await shot(page, 'F10-15', size)
      })

      test('F10#16: no source post hides its button', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { has_source: false })) })
        await open(page)
        await expect(page.getByTestId('about')).toBeAttached()
        await expect(page.getByRole('button', { name: 'دیدن پست اصلی' })).toHaveCount(0)
        await page.getByTestId('about').scrollIntoViewIfNeeded()
        await shot(page, 'F10-16', size)
      })

      test.describe('availability', () => {
        const URL = `POST /api/v1/me/listings/${ID}/availability/`
        const ASK = { [DETAIL]: OK(listing(CARDS[0], { can_confirm: true })), [VIEWER]: OK({ is_owner: false, review_state: 'live', listing: listing(CARDS[0], { can_confirm: true }) }) }
        const answerIt = async (page: Page, name: string) => {
          await open(page)
          await page.getByTestId('availability').scrollIntoViewIfNeeded()
          await page.getByTestId('availability').getByRole('button', { name }).click()
        }

        test('F10#9: asked only when can_confirm says so', async ({ page }) => {
          await mockApi(page)
          await open(page)
          await expect(page.getByTestId('detail')).toBeVisible()
          await expect(page.getByTestId('availability')).toHaveCount(0)
        })

        for (const [name, outcome] of [['بله، موجود است', 'confirmed'], ['نه، بسته شد', 'noted']] as const) {
          test(`F10#9: «${name}» answering ${outcome} thanks, and one answer is all`, async ({ page }) => {
            await mockApi(page, { ...ASK, [URL]: OK({ outcome }) })
            await answerIt(page, name)
            await expect(page.getByTestId('toast')).toContainText('ممنون، ثبت شد.')
            await expect(page.getByTestId('availability').getByRole('button').first()).toBeDisabled()
            await shot(page, 'F10-09', size)
          })
        }

        for (const outcome of ['rented', 'closed'] as const) {
          test(`F10#18: outcome ${outcome} says it is no longer active and shows the closed detail`, async ({ page }) => {
            await mockApi(page, { ...ASK, [URL]: OK({ outcome }) })
            await answerIt(page, 'نه، بسته شد')
            await expect(page.getByTestId('toast')).toContainText('این آگهی دیگر فعال نیست.')
            await expect(page.getByTestId('closed-note')).toBeVisible()
            await shot(page, 'F10-18', size)
          })
        }

        test('F25#10: failure offers retry, and retry works', async ({ page }) => {
          await mockApi(page, { ...ASK, [URL]: coded(500, 'server_error') })
          await answerIt(page, 'بله، موجود است')
          await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
          await page.route(`**/api/v1/me/listings/${ID}/availability/`, (route) => route.fulfill({ json: { outcome: 'confirmed' } }))
          await page.getByTestId('toast').getByRole('button', { name: 'تلاش مجدد' }).click()
          await expect(page.getByText('ممنون، ثبت شد.')).toBeVisible()
        })

        test('F25#6: rate limit keeps the question', async ({ page }) => {
          await mockApi(page, { ...ASK, [URL]: coded(429, 'rate_limited') })
          await answerIt(page, 'بله، موجود است')
          await expect(page.getByTestId('toast')).toContainText('چند لحظه‌ی دیگر دوباره امتحان کنید.')
          await expect(page.getByTestId('availability').getByRole('button').first()).toBeEnabled()
        })

        test('confirm_not_allowed removes the question', async ({ page }) => {
          await mockApi(page, { ...ASK, [URL]: coded(403, 'confirm_not_allowed') })
          await answerIt(page, 'بله، موجود است')
          await expect(page.getByTestId('availability')).toHaveCount(0)
        })

        test('listing_gone goes to F02#4', async ({ page }) => {
          await mockApi(page, { ...ASK, [URL]: coded(404, 'listing_gone') })
          await answerIt(page, 'بله، موجود است')
          await expect(page.getByTestId('listing-gone')).toBeVisible()
        })
      })

      test('poster row shows only with a known author', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { poster_count: 3 })) })
        await open(page)
        await expect(page.getByTestId('about')).toContainText('۳ آگهی فعال')
      })

      test('a region with no rule shows no rule', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { region: 'BC' })) })
        await open(page)
        await expect(page.getByTestId('detail')).toBeVisible()
        await expect(page.getByTestId('region-rule')).toHaveCount(0)
      })

      test('sparse listing has no blank facts', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { rooms: null, size_m2: null, furnished: 'unknown', bills_included: 'unknown', available_from: null, area: '', summary_fa: '' })) })
        await open(page)
        await expect(page.getByTestId('detail')).toBeVisible()
        await expect(page.getByTestId('facts').locator('[data-slot=card]')).toHaveCount(0)
        await expect(page.getByText('توضیحات', { exact: true })).toHaveCount(0)
      })

      test('F04#11: a listing closed since the snapshot shows closed on its card', async ({ page }) => {
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { state: 'closed', closed_reason: 'owner' })) })
        await page.goto(APP())
        await page.getByTestId('listing-card').first().click()
        await expect(page.getByTestId('closed-note')).toBeVisible()
        await pressBack(page)
        const closed = page.getByTestId('listing-card').filter({ hasText: 'بسته شد' })
        await expect(closed).toHaveCount(1)
        await shot(page, 'F04-11', size)
      })

      test('tapping a card opens the detail, and Back returns to the list', async ({ page }) => {
        await mockApi(page)
        await page.goto(APP())
        await page.getByTestId('listing-card').first().click()
        await expect(page.getByTestId('detail')).toBeVisible()
        await pressBack(page)
        await expect(page.getByTestId('count-bar')).toBeVisible()
      })

      test('bookmark, share and map stay hidden until their flows ship', async ({ page }) => {
        await mockApi(page)
        await open(page)
        await expect(page.getByTestId('detail')).toBeVisible()
        for (const name of ['نشان کردن', 'اشتراک‌گذاری', 'نقشه', 'گزارش']) await expect(page.getByRole('button', { name })).toHaveCount(0)
      })
    })

    test.describe('F01, F02 links', () => {
      const LINK = (patch: object) => ({ '/api/v1/public/deeplink/': OK({ ...DEEPLINK_LANDING, kind: 'listing', listing_id: ID, listing_state: 'active', country: 'CA', city: 'Toronto', city_slug: 'toronto', city_known: true, ...patch }) })

      test('F01#8, #9: a first open from a listing link asks nothing, and Back shows that city', async ({ page }) => {
        await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
        const qc = snapshot('montreal').listings[0]
        await mockApi(page, LINK({ listing_id: qc.id, city: 'Montreal', city_slug: 'montreal' }))
        await page.goto(APP('/app/v2/', `&tgstart=home_${qc.id}`))
        await expect(page.getByTestId('detail')).toBeVisible()
        await expect(page.getByTestId('toast')).toContainText('شهر شما: مونترال')
        await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toHaveCount(0)
        await shot(page, 'F01-08', size)
        await pressBack(page)
        await expect(page.getByTestId('city-button')).toContainText('مونترال')
        await shot(page, 'F01-09', size)
      })

      test('F01#8: «تغییر» opens the city sheet', async ({ page }) => {
        await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
        const qc = snapshot('montreal').listings[0]
        await mockApi(page, LINK({ listing_id: qc.id, city: 'Montreal', city_slug: 'montreal' }))
        await page.goto(APP('/app/v2/', `&tgstart=home_${qc.id}`))
        await page.getByTestId('toast').getByRole('button', { name: 'تغییر' }).click()
        await expect(page.getByTestId('city-sheet')).toBeVisible()
      })

      test('F01#12: a gone link on a first open', async ({ page }) => {
        await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
        await mockApi(page, { ...LINK({ listing_state: 'gone', city: '', city_slug: '', city_known: false }), [DETAIL]: coded(404, 'listing_gone') })
        await page.goto(APP('/app/v2/', `&tgstart=home_${ID}`))
        await expect(page.getByTestId('listing-gone')).toContainText('این آگهی دیگر نیست')
        await shot(page, 'F01-12', size)
        await page.getByRole('button', { name: 'دیدن آگهی‌ها' }).click()
        await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
      })

      test('a template link to another listing wins over the launch link, and a reload does not reapply it', async ({ page }) => {
        await mockApi(page, LINK({}))
        await page.goto(APP(`/app/v2/listing/${CARDS[1].id}`, `&tgstart=home_${ID}`))
        await expect(page.getByTestId('detail')).toContainText('۲٬۴۵۰')
        await page.goto(APP('/app/v2/', `&tgstart=home_${ID}`))
        await expect(page.getByTestId('count-bar')).toBeVisible()
      })

      test('the link that brought the person to its own listing still counts as a link', async ({ page }) => {
        const qc = snapshot('montreal').listings[0]
        await mockApi(page, LINK({ listing_id: qc.id, city: 'Montreal', city_slug: 'montreal' }))
        await page.goto(APP(`/app/v2/listing/${qc.id}`, `&tgstart=home_${qc.id}`))
        await expect(page.getByTestId('toast')).toContainText('در حال دیدن مونترال')
      })

      test('F02#2: a saved-city link opens the detail with no toast', async ({ page }) => {
        await mockApi(page, LINK({}))
        await page.goto(APP('/app/v2/', `&tgstart=home_${ID}`))
        await expect(page.getByTestId('detail')).toBeVisible()
        await expect(page.getByTestId('toast')).toHaveCount(0)
        await shot(page, 'F02-02', size)
      })

      test('F02#4: gone link on return goes back to the saved city', async ({ page }) => {
        await mockApi(page, { ...LINK({ listing_state: 'gone' }), [DETAIL]: coded(404, 'listing_gone') })
        await page.goto(APP('/app/v2/', `&tgstart=home_${ID}`))
        await expect(page.getByTestId('listing-gone')).toBeVisible()
        await shot(page, 'F02-04', size)
        await page.getByRole('button', { name: 'برگشت به آگهی‌ها' }).click()
        await expect(page.getByTestId('city-button')).toContainText('تورنتو')
      })

      test('F02#8: another city keeps the saved city, and Back shows that city first', async ({ page }) => {
        const qc = snapshot('montreal').listings[0]
        await mockApi(page, LINK({ listing_id: qc.id, city: 'Montreal', city_slug: 'montreal' }))
        await page.goto(APP('/app/v2/', `&tgstart=home_${qc.id}`))
        await expect(page.getByTestId('toast')).toContainText('در حال دیدن مونترال')
        await shot(page, 'F02-08', size)
        expect(await page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.city')!).slug)).toBe('toronto')
        await pressBack(page)
        await expect(page.getByTestId('city-button')).toContainText('مونترال')
        expect(await page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.city')!).slug)).toBe('toronto')
      })

      test('F02#8: «برگشت به تورنتو» goes home', async ({ page }) => {
        const qc = snapshot('montreal').listings[0]
        await mockApi(page, LINK({ listing_id: qc.id, city: 'Montreal', city_slug: 'montreal' }))
        await page.goto(APP('/app/v2/', `&tgstart=home_${qc.id}`))
        await page.getByTestId('toast').getByRole('button', { name: 'برگشت به تورنتو' }).click()
        await expect(page.getByTestId('city-button')).toContainText('تورنتو')
      })

      test('F01 rules: a listing with no metro opens, and Back goes to F01#1 on a first open', async ({ page }) => {
        await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
        const none = { ...CARDS[0], metro: '', metro_slug: '' }
        await mockApi(page, { ...LINK({ city: '', city_slug: '', city_known: false }), [DETAIL]: OK(listing(none)) })
        await page.goto(APP('/app/v2/', `&tgstart=home_${ID}`))
        await expect(page.getByTestId('detail')).toBeVisible()
        await pressBack(page)
        await expect(page.getByRole('heading', { name: 'کشور شما؟' })).toBeVisible()
      })

      test('an owner can open their hidden listing, and others get gone', async ({ page }) => {
        await mockApi(page, {
          '/api/v1/public/meta/': OK({ ...META, miniapp_v2: false }),
          [DETAIL]: coded(404, 'listing_gone'),
          [VIEWER]: OK({ is_owner: true, review_state: 'in_review', listing: listing(CARDS[0]) }),
        })
        await open(page)
        await expect(page.getByTestId('detail')).toContainText('در انتظار بررسی')
      })

      test('F02#4: a reported listing is gone for others too', async ({ page }) => {
        await mockApi(page, {
          '/api/v1/public/meta/': OK({ ...META, miniapp_v2: false }),
          [DETAIL]: coded(404, 'listing_gone'),
          [VIEWER]: coded(404, 'listing_gone'),
        })
        await open(page)
        await expect(page.getByTestId('listing-gone')).toBeVisible()
      })
    })

    test.describe('F11 gate', () => {
      test('F11#1: first contact warns, once, with the region rule; #20 short line after', async ({ page }) => {
        await mockApi(page)
        await open(page)
        await pressContact(page)
        const warning = page.getByTestId('warning-sheet')
        await expect(warning).toContainText('قبل از تماس')
        await expect(warning).toContainText('در انتاریو فقط ودیعه‌ی ماه آخر مجاز است.')
        await expect(warning.getByRole('button', { name: 'بستن' })).toHaveCount(0)
        await shot(page, 'F11-01', size)
        await warning.getByRole('button', { name: 'متوجه شدم، ادامه' }).click()
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
        await expect(page.getByTestId('short-warning')).toHaveCount(0)
        await page.getByTestId('contact-sheet').getByRole('button', { name: 'بستن' }).click()
        await pressContact(page)
        await expect(page.getByTestId('short-warning')).toContainText('قبل از دیدن خانه پولی نفرستید.')
        await shot(page, 'F11-20', size)
      })

      test('F11#21: Quebec listing warns with Quebec text', async ({ page }) => {
        const qc = snapshot('montreal').listings[0]
        await mockApi(page)
        await page.goto(APP(`/app/v2/listing/${qc.id}`))
        await pressContact(page)
        await expect(page.getByTestId('warning-sheet')).toContainText('در کبک مالک نمی‌تواند ودیعه بگیرد.')
        await shot(page, 'F11-21', size)
      })

      test('F11#2, #4, #6: not a member, join, come back, see the contact', async ({ page }) => {
        await WARNED(page)
        let member = false
        await mockApi(page)
        await page.route(`**/api/v1/me/listings/${ID}/contact/`, (route) => route.fulfill(member ? { json: CONTACT } : { status: 403, json: NOT_MEMBER.body }))
        await open(page)
        await pressContact(page)
        const gate = page.getByTestId('gate-sheet')
        await expect(gate).toContainText('عضو کانال تورنتو شوید')
        await expect(page.getByTestId('masked')).toContainText('تلگرام')
        await expect(page.getByTestId('masked')).toContainText('شماره')
        await expect(gate).not.toContainText('416')
        await expect(gate).not.toContainText('username')
        await shot(page, 'F11-02', size)
        await gate.getByRole('button', { name: 'عضویت در کانال' }).click()
        const join = page.getByTestId('join-sheet')
        await expect(join.getByRole('button', { name: 'لغو' })).toBeVisible()
        await shot(page, 'F11-04', size)
        await join.getByRole('button', { name: 'پیوستن به کانال' }).click()
        expect(await opened(page)).toContain('https://t.me/+join')
        member = true
        await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
        await expect(page.getByTestId('member-line')).toContainText('کانال چمدون - خانه · تورنتو')
        await expect(page.getByTestId('contact-telegram')).toContainText('@username')
        await expect(page.getByTestId('contact-phone')).toContainText('+1 416 555 0100')
        await shot(page, 'F11-06', size)
      })

      test('F11#3: the same gate from «دیدن پست اصلی», which then opens the post', async ({ page }) => {
        await WARNED(page)
        let member = false
        await mockApi(page)
        await page.route(`**/api/v1/me/listings/${ID}/contact/`, (route) => route.fulfill(member ? { json: CONTACT } : { status: 403, json: NOT_MEMBER.body }))
        await open(page)
        await page.getByTestId('about').getByRole('button', { name: 'دیدن پست اصلی' }).click()
        const gate = page.getByTestId('gate-sheet')
        await expect(gate.getByRole('button', { name: 'عضو شدم، دیدن پست اصلی' })).toBeVisible()
        await shot(page, 'F11-03', size)
        member = true
        await gate.getByRole('button', { name: 'عضو شدم، دیدن پست اصلی' }).click()
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
        expect(await opened(page)).toContain(CONTACT.source_url)
      })

      test('F11#5, #11: tapped «عضو شدم» while not a member', async ({ page }) => {
        await WARNED(page)
        let hold: (() => void) | null = null
        await mockApi(page)
        let calls = 0
        await page.route(`**/api/v1/me/listings/${ID}/contact/`, async (route) => {
          calls += 1
          if (calls === 2) await new Promise<void>((resolve) => (hold = resolve))
          return route.fulfill({ status: 403, json: NOT_MEMBER.body })
        })
        await open(page)
        await pressContact(page)
        const gate = page.getByTestId('gate-sheet')
        await gate.getByRole('button', { name: 'عضو شدم، نمایش تماس' }).click()
        await expect(gate.getByRole('button', { name: 'عضو شدم، نمایش تماس' })).toBeDisabled()
        await expect.poll(() => calls).toBe(2)
        await shot(page, 'F11-11', size)
        hold!()
        await expect(page.getByTestId('still-out')).toContainText('هنوز عضو کانال نشده‌اید.')
        await shot(page, 'F11-05', size)
      })

      test('F11#7: number copied', async ({ page, context }) => {
        await context.grantPermissions(['clipboard-read', 'clipboard-write'])
        await WARNED(page)
        await mockApi(page)
        await open(page)
        await pressContact(page)
        await page.getByTestId('contact-phone').getByRole('button', { name: 'کپی' }).click()
        await expect(page.getByTestId('toast')).toContainText('شماره کپی شد')
        await expect(page.getByTestId('contact-phone').getByRole('button', { name: 'کپی شد' })).toBeVisible()
        await shot(page, 'F11-07', size)
      })

      test('F11#22: copy denied makes the number selectable', async ({ page }) => {
        await WARNED(page)
        await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) } }))
        await mockApi(page)
        await open(page)
        await pressContact(page)
        await page.getByTestId('contact-phone').getByRole('button', { name: 'کپی' }).click()
        await expect(page.getByTestId('toast')).toContainText('کپی نشد')
        await expect(page.getByTestId('contact-phone').getByRole('textbox')).toHaveValue('+1 416 555 0100')
        await shot(page, 'F11-22', size)
      })

      test('F11#8: no handle or phone, only the source post', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: OK({ ...CONTACT, telegram: '', telegram_url: '', phone: '' }) })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('contact-sheet')).toContainText('آیدی یا شماره‌ای در این آگهی نیست.')
        await expect(page.getByTestId('contact-sheet').getByRole('button', { name: 'دیدن پست اصلی' })).toBeVisible()
        await shot(page, 'F11-08', size)
      })

      test('F11#9: own listing has edit, not contact', async ({ page }) => {
        await mockApi(page, {
          '/api/v1/public/meta/': OK({ ...META, miniapp_v2: false }),
          [VIEWER]: OK({ is_owner: true, review_state: 'live', listing: listing(CARDS[0]) }),
        })
        await open(page)
        await expect(page.getByTestId('detail')).toContainText('آگهی خودتان')
        await expect(page.locator('[data-testid="owner-actions"]:visible').getByRole('link', { name: 'ویرایش آگهی' })).toHaveAttribute('href', /\/app\/home\/mine\//)
        await expect(page.getByRole('button', { name: 'تماس' })).toHaveCount(0)
        await shot(page, 'F11-09', size)
      })

      test('F11#23: own listing waiting for review', async ({ page }) => {
        await mockApi(page, {
          '/api/v1/public/meta/': OK({ ...META, miniapp_v2: false }),
          [VIEWER]: OK({ is_owner: true, review_state: 'in_review', listing: listing(CARDS[0]) }),
        })
        await open(page)
        await expect(page.getByTestId('detail')).toContainText('در انتظار بررسی')
        await expect(page.getByTestId('detail')).toContainText('دیده نمی‌شود')
        await shot(page, 'F11-23', size)
      })

      test('F11#10: closed since the page was cached', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(409, 'listing_closed') })
        await open(page)
        await pressContact(page)
        const sheet = page.getByTestId('gate-closed')
        await expect(sheet).toContainText('این آگهی بسته شد.')
        await shot(page, 'F11-10', size)
        await sheet.getByRole('button', { name: 'دیدن آگهی‌های مشابه' }).click()
        expect(page.url()).toContain('types=apartment')
      })

      test('F11#10: closing the sheet shows the closed detail, and «تماس» is gone', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(409, 'listing_closed') })
        await open(page)
        await pressContact(page)
        await page.getByTestId('gate-closed').getByRole('button', { name: 'بستن' }).click()
        await expect(page.getByTestId('closed-note')).toBeVisible()
        await expect(page.getByRole('button', { name: 'تماس' })).toHaveCount(0)
      })

      test('F11#8: a source-only listing opens its post from either entry', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { has_telegram: false, has_phone: false, has_source: true })), [CONTACT_URL]: OK({ ...CONTACT, telegram: '', telegram_url: '', phone: '' }) })
        await open(page)
        await page.getByTestId('about').getByRole('button', { name: 'دیدن پست اصلی' }).click()
        await expect(page.getByTestId('contact-sheet')).toContainText('آیدی یا شماره‌ای در این آگهی نیست.')
        expect(await opened(page)).toContain(CONTACT.source_url)
        await page.getByTestId('contact-sheet').getByRole('button', { name: 'دیدن پست اصلی' }).click()
        expect((await opened(page)).filter((url) => url === CONTACT.source_url)).toHaveLength(2)
      })

      test('F02#4: gone since the page was cached', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(404, 'listing_gone') })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('listing-gone')).toBeVisible()
      })

      test('F11#12: daily cap', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(429, 'reveal_cap') })
        await open(page)
        await pressContact(page)
        const sheet = page.getByTestId('gate-cap')
        await expect(sheet).toContainText('امروز به سقف دیدن راه تماس رسیدید. فردا دوباره امتحان کنید.')
        await shot(page, 'F11-12', size)
        await sheet.getByRole('button', { name: 'بستن' }).click()
        await expect(sheet).toBeHidden()
      })

      for (const [name, forced] of [
        ['5xx', coded(500, 'server_error')],
        ['platform page', { status: 502, body: '<html>x</html>', html: true }],
        ['not served yet', coded(501, 'not_served_yet')],
        ['network failure', 'abort'],
      ] as const) {
        test(`F11#13: ${name} offers retry, and retry works`, async ({ page }) => {
          await WARNED(page)
          await mockApi(page, { [CONTACT_URL]: forced })
          await open(page)
          await pressContact(page)
          const sheet = page.getByTestId('gate-failed')
          await expect(sheet).toContainText('راه تماس باز نشد.')
          if (name === '5xx') await shot(page, 'F11-13', size)
          await page.route(`**/api/v1/me/listings/${ID}/contact/`, (route) => route.fulfill({ json: CONTACT }))
          await sheet.getByRole('button', { name: 'تلاش مجدد' }).click()
          await expect(page.getByTestId('contact-sheet')).toBeVisible()
        })
      }

      test('F11#14: rate limited', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: { ...coded(429, 'rate_limited'), headers: { 'Retry-After': '30' } } })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-rate')).toContainText('چند لحظه‌ی دیگر دوباره امتحان کنید.')
        await shot(page, 'F11-14', size)
      })

      test('F11#15: banned from the channel has no join button', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(403, 'gate_banned') })
        await open(page)
        await pressContact(page)
        const sheet = page.getByTestId('gate-banned')
        await expect(sheet).toContainText('شما نمی‌توانید عضو این کانال شوید.')
        await expect(sheet.getByRole('button', { name: 'عضویت در کانال' })).toHaveCount(0)
        await shot(page, 'F11-15', size)
      })

      test('F11#16: no city channel asks for the country channel', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(403, 'not_member', { join_url: 'https://t.me/+ca', channel: { ...CANADA_CHANNEL } }) })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-sheet')).toContainText('عضو کانال کانادا شوید')
        await shot(page, 'F11-16', size)
      })

      test('F11#17: nothing at all needs no call', async ({ page }) => {
        await WARNED(page)
        let called = false
        await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { has_telegram: false, has_phone: false, has_source: false })) })
        page.on('request', (r) => {
          if (r.url().includes('/contact/')) called = true
        })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-nothing')).toContainText('راه تماسی در این آگهی نیست.')
        expect(called).toBe(false)
        await shot(page, 'F11-17', size)
      })

      test('F11#17: server says nothing to show', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: OK({ ...CONTACT, telegram: '', telegram_url: '', phone: '', source_url: '', has_any: false }) })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-nothing')).toBeVisible()
      })

      test('F11#17: no channel to ask for keeps the contact closed, with no retry', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(409, 'no_channel') })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-nothing')).toContainText('راه تماسی در این آگهی نیست.')
      })

      test('F11#18: contact with no source post', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: OK({ ...CONTACT, source_url: '' }) })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('contact-phone')).toBeVisible()
        await expect(page.getByTestId('contact-sheet').getByRole('button', { name: 'دیدن پست اصلی' })).toHaveCount(0)
        await shot(page, 'F11-18', size)
      })

      for (const gate of ['failed_open', 'gate_off'] as const) {
        test(`F11#19: ${gate} reveals with no member line`, async ({ page }) => {
          await WARNED(page)
          await mockApi(page, { [CONTACT_URL]: OK({ ...CONTACT, gate, membership_checked: false }) })
          await open(page)
          await pressContact(page)
          await expect(page.getByTestId('contact-phone')).toBeVisible()
          await expect(page.getByTestId('member-line')).toHaveCount(0)
          if (gate === 'failed_open') await shot(page, 'F11-19', size)
        })
      }

      test('double tap sends one request', async ({ page }) => {
        await WARNED(page)
        let calls = 0
        let hold: (() => void) | null = null
        await mockApi(page)
        await page.route(`**/api/v1/me/listings/${ID}/contact/`, async (route) => {
          calls += 1
          await new Promise<void>((resolve) => (hold = resolve))
          return route.fulfill({ json: CONTACT })
        })
        await open(page)
        const button = page.locator('[data-testid="actions"]:visible').getByRole('button').first()
        await button.dblclick()
        await expect(button).toBeDisabled()
        await expect.poll(() => calls).toBe(1)
        hold!()
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
        expect(calls).toBe(1)
      })

      test('F25#7: offline does not call, and says why', async ({ page, context }) => {
        await WARNED(page)
        await mockApi(page)
        await open(page)
        await expect(page.getByTestId('detail')).toBeVisible()
        await context.setOffline(true)
        await pressContact(page)
        await expect(page.getByTestId('toast')).toContainText('برای این کار به اینترنت نیاز دارید')
        await shot(page, 'F25-07', size)
        await context.setOffline(false)
      })

      test('session over at the gate replaces the app, F02#7', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { 'POST /api/v1/auth/exchange/': coded(401, 'session_expired') })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('session-over')).toBeVisible()
      })

      test('blocked at the gate replaces the app, F02#9', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: coded(403, 'user_blocked') })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('blocked-screen')).toBeVisible()
      })

      test('an expired token exchanges again and the reveal still happens', async ({ page }) => {
        await WARNED(page)
        let first = true
        await mockApi(page)
        await page.route(`**/api/v1/me/listings/${ID}/contact/`, (route) => {
          if (first) {
            first = false
            return route.fulfill({ status: 401, json: { code: 'session_expired', detail: 'x' } })
          }
          return route.fulfill({ json: CONTACT })
        })
        await page.route('**/api/v1/auth/exchange/', (route) => route.fulfill({ json: EXCHANGE }))
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
      })

      test('Back closes the gate sheet, not the page', async ({ page }) => {
        await WARNED(page)
        await mockApi(page, { [CONTACT_URL]: NOT_MEMBER })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('gate-sheet')).toBeVisible()
        await pressBack(page)
        await expect(page.getByTestId('gate-sheet')).toBeHidden()
        await expect(page.getByTestId('detail')).toBeVisible()
      })

      test('public calls never carry the JWT', async ({ page }) => {
        await WARNED(page)
        const seen: Array<string | undefined> = []
        await mockApi(page)
        page.on('request', (request) => {
          if (request.url().includes('/public/')) seen.push(request.headers().authorization)
        })
        await open(page)
        await pressContact(page)
        await expect(page.getByTestId('contact-sheet')).toBeVisible()
        expect(seen.length).toBeGreaterThan(0)
        expect(seen.every((value) => value === undefined)).toBe(true)
      })
    })
  })
}
