import { expect, test, type Page } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

type Alerts = ReturnType<typeof fx<'alerts'>>
const alertsOf = (count: number): Alerts => ({ ...fx('alerts'), alerts: Array.from({ length: count }, (_, index) => ({ ...fx('alerts').alerts[0], id: 400 + index })) }) as Alerts
const openMenu = async (page: Page) => {
  await page.getByTestId('alert-menu').first().click()
}
const MINE = 'GET me/requests/201/'
const mine = (changes: object) => ({ status: 200, body: { ...fx('mineActive'), ...changes } })
const MATCH = 'GET me/matches/9/'
const matchWith = (changes: object, rating: object = {}) => ({ status: 200, body: { ...fx('match'), ...changes, rating: { ...fx('match').rating, ...rating } } })
const empty = { incoming: [], outgoing: [], accepted: [], closed: [], matches: [] }

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C11 on ${size}`, () => {
    test.use({ viewport })

    test('C11.1 list, on and off, with the count', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('alerts'))
      await expect(page.getByTestId('alerts-count')).toHaveText('۲ از ۵')
      await expect(page.getByTestId('alert-row')).toHaveCount(2)
      await expect(page.getByTestId('alert-off')).toHaveCount(1)
      await expect(page.getByTestId('alert-row').first()).toContainText('تهران ← تورنتو')
      expect(await noSidewaysScroll(page)).toBe(true)
    })

    test('C11.2 menu', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await expect(page.getByTestId('alert-edit-open')).toContainText('ویرایش')
      await expect(page.getByTestId('alert-toggle')).toContainText('خاموش')
      await expect(page.getByTestId('alert-delete-open')).toContainText('حذف')
    })

    test('C11.3 edit sends route, month and size; no route blocks', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-edit-open').click()
      const sheet = page.getByTestId('alert-edit')
      await sheet.getByTestId('alert-edit-sizes').getByRole('radio').first().click()
      await sheet.getByTestId('alert-edit-save').click()
      await expect(sheet).toHaveCount(0)
      const sent = calls.find((call) => call.method === 'PATCH')!
      expect(sent.path).toBe('me/alerts/302/')
      expect(sent.body).toMatchObject({ origin: 'IR:tehran', destination: 'CA:toronto', size: 'doc' })
    })

    test('C11.3 edit equals another alert', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/alerts/302/': coded(409, 'alert_exists') })
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-edit-open').click()
      await page.getByTestId('alert-edit-save').click()
      await expect(page.getByTestId('alert-conflict')).toContainText('این مسیر را قبلاً گوش به زنگ کرده‌اید.')
    })

    test('C11.4 paused, with undo', async ({ page }) => {
      const calls = await mockCourier(page, { 'PATCH me/alerts/302/': { status: 200, body: fx('alertSaved') } })
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-toggle').click()
      await expect(page.getByTestId('toast')).toContainText('خاموش شد')
      await page.getByTestId('toast').getByRole('button', { name: 'برگرداندن' }).click()
      await expect.poll(() => calls.filter((call) => call.method === 'PATCH').map((call) => call.body)).toEqual([{ active: false }, { active: true }])
    })

    test('C11.5 delete asks first', async ({ page }) => {
      const calls = await mockCourier(page, { 'DELETE me/alerts/302/': { status: 204, body: null } })
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-delete-open').click()
      await expect(page.getByTestId('alert-delete')).toContainText('این گوش به زنگ حذف شود؟')
      expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
      await page.getByTestId('alert-delete-confirm').click()
      await expect.poll(() => calls.some((call) => call.method === 'DELETE')).toBe(true)
    })

    test('C11.6 sending off', async ({ page }) => {
      await mockCourier(page, { 'GET me/alerts/': { status: 200, body: { ...fx('alerts'), sending_on: false } } })
      await page.goto(COURIER('alerts'))
      await expect(page.getByTestId('alerts-sending-off')).toContainText('فرستادن خبرها به‌زودی روشن می‌شود')
    })

    test('C11.7 five of five hides new', async ({ page }) => {
      await mockCourier(page, { 'GET me/alerts/': { status: 200, body: alertsOf(5) } })
      await page.goto(COURIER('alerts'))
      await expect(page.getByTestId('alerts-count')).toHaveText('۵ از ۵')
      await expect(page.getByTestId('alerts-new')).toHaveCount(0)
    })

    test('C11.8 empty', async ({ page }) => {
      await mockCourier(page, { 'GET me/alerts/': { status: 200, body: { ...fx('alerts'), alerts: [] } } })
      await page.goto(COURIER('alerts'))
      await expect(page.getByTestId('alerts-empty')).toContainText('هنوز گوش به زنگی ندارید')
    })

    test('C11.9 pause fails: the card stays on, with a retry', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/alerts/302/': coded(500, 'server_error') })
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-toggle').click()
      await expect(page.getByTestId('toast')).toContainText('خاموش نشد')
      await expect(page.getByTestId('alert-off')).toHaveCount(1)
    })

    test('C11 deleted in the bot: says so and refreshes', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/alerts/302/': coded(404, 'alert_not_found') })
      await page.goto(COURIER('alerts'))
      await openMenu(page)
      await page.getByTestId('alert-toggle').click()
      await expect(page.getByTestId('toast')).toContainText('این گوش به زنگ حذف شده است.')
    })

    test('C11 list error', async ({ page }) => {
      await mockCourier(page, { 'GET me/alerts/': coded(429, 'rate_limited') })
      await page.goto(COURIER('alerts'))
      await expect(page.getByTestId('rate-limited')).toBeVisible()
    })
  })

  test.describe(`C12 on ${size}`, () => {
    test.use({ viewport })

    test('C12.1 active: status and suggestion count, and no contact button', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/requests'))
      await expect(page.getByTestId('mine')).toContainText('درخواست‌ها و سفرهای من')
      await expect(page.getByTestId('status-badge').first()).toHaveText('فعال')
      await expect(page.getByTestId('request-card').first()).toContainText('۲ پیشنهاد')
      await expect(page.getByTestId('mine')).toContainText('فعال (۱)')
      expect(await noSidewaysScroll(page)).toBe(true)
    })

    test('C12.2 archive reads each status', async ({ page }) => {
      await mockCourier(page, { 'GET me/requests/': (route) => (new URL(route.request().url()).searchParams.get('state') === 'archive' ? { status: 200, body: { items: ['COMPLETED', 'CANCELLED', 'EXPIRED', 'CLOSED'].map((status, index) => ({ ...fx('mineActive'), id: 700 + index, status, editable: false })) } } : { status: 200, body: { items: [] } }) })
      await page.goto(COURIER('me/requests'))
      await page.getByRole('tab', { name: /بایگانی/ }).click()
      for (const word of ['تمام‌شده', 'لغو شده', 'منقضی', 'بسته شد']) await expect(page.getByTestId('mine')).toContainText(word)
    })

    test('C12.3 none: post either', async ({ page }) => {
      await mockCourier(page, { 'GET me/requests/': { status: 200, body: { items: [] } } })
      await page.goto(COURIER('me/requests'))
      await expect(page.getByTestId('mine-empty')).toContainText('هنوز درخواست فعالی ندارید')
      await page.getByTestId('mine-post-trip').click()
      await page.waitForURL(/\/post\/trip/)
    })

    test('C12.4 detail with suggestions and no contact', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/requests/201'))
      await expect(page.getByTestId('own-side')).toContainText('تورنتو ← تهران')
      await expect(page.getByTestId('mine-suggestions').getByTestId('request-card')).toHaveCount(2)
      await expect(page.getByTestId('mine-edit')).toBeVisible()
      await expect(page.getByTestId('intro-ask')).toHaveCount(0)
    })

    test('C12.5 no match yet: alert', async ({ page }) => {
      await mockCourier(page, { 'GET me/requests/201/counterparts/': { status: 200, body: { items: [] } } })
      await page.goto(COURIER('me/requests/201'))
      await expect(page.getByTestId('mine-no-match')).toContainText('هنوز تطبیقی نیست')
      await page.getByTestId('mine-alert').click()
      await expect(page.getByTestId('alert-sheet')).toBeVisible()
    })

    test('C12.6 edit saves on one screen, with the type fixed', async ({ page }) => {
      const calls = await mockCourier(page, { 'PATCH me/requests/201/': { status: 200, body: fx('mineActive') } })
      await page.goto(COURIER('me/requests/201'))
      await page.getByTestId('mine-edit').click()
      await expect(page.getByTestId('mine-edit-form')).toContainText('ویرایش درخواست')
      await page.getByTestId('edit-size').getByRole('radio').nth(1).click()
      await page.getByTestId('edit-note').fill('تازه')
      await page.getByTestId('edit-save').click()
      await page.waitForURL(/\/me\/requests\/201$|\/me\/requests\/201\?/)
      const sent = calls.find((call) => call.method === 'PATCH')!.body as Record<string, unknown>
      expect(sent).toMatchObject({ size: '1', description: 'تازه' })
      expect(sent.type).toBeUndefined()
    })

    test('C12.6 a bad date and a rejected edit keep the form', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/requests/201/': coded(400, 'server_rejected') })
      await page.goto(COURIER('me/requests/201/edit'))
      await page.getByTestId('edit-when').getByRole('radio', { name: 'تاریخ دقیق' }).click()
      await page.getByTestId('edit-date').fill('۳۲ دسامبر')
      await expect(page.getByTestId('edit-date-error')).toBeVisible()
      await expect(page.getByTestId('edit-save')).toBeDisabled()
      await page.getByTestId('edit-date').fill('۱۲ دسامبر')
      await page.getByTestId('edit-save').click()
      await expect(page.getByTestId('edit-failed')).toContainText('ثبت نشد. دوباره تلاش کنید.')
      await expect(page.getByTestId('edit-date')).toHaveValue('۱۲ دسامبر')
    })

    test('C12.7 and C12.8 close asks, then reads closed with post again', async ({ page }) => {
      let closed = false
      const calls = await mockCourier(page, {
        [MINE]: () => mine(closed ? { status: 'CANCELLED', editable: false } : {}),
        'POST me/requests/201/close/': () => {
          closed = true
          return mine({ status: 'CANCELLED', editable: false })
        },
      })
      await page.goto(COURIER('me/requests/201'))
      await page.getByTestId('mine-close').click()
      await expect(page.getByTestId('mine-close-confirm')).toContainText('پس از بستن، این درخواست دیگر فعال نخواهد بود.')
      expect(calls.some((call) => call.path.endsWith('close/'))).toBe(false)
      await page.getByTestId('mine-close-go').click()
      await expect(page.getByTestId('mine-closed').getByTestId('status-badge')).toHaveText('لغو شده')
      await page.getByTestId('mine-post-again').click()
      await page.waitForURL(/\/post\/package\?from=CA%3Atoronto&to=IR%3Atehran/)
    })

    test('C12.9 closed elsewhere while editing', async ({ page }) => {
      let closed = false
      await mockCourier(page, {
        [MINE]: () => mine(closed ? { status: 'CLOSED', editable: false } : {}),
        'PATCH me/requests/201/': () => {
          closed = true
          return coded(409, 'not_editable')
        },
      })
      await page.goto(COURIER('me/requests/201/edit'))
      await page.getByTestId('edit-when').getByRole('radio', { name: 'عجله ندارم' }).click()
      await page.getByTestId('edit-size').getByRole('radio').first().click()
      await page.getByTestId('edit-save').click()
      await expect(page.getByTestId('mine-not-editable')).toContainText('این درخواست دیگر فعال نیست و تغییر نمی‌کند.')
    })

    test('C12.10 own trip date passed', async ({ page }) => {
      await mockCourier(page, { [MINE]: mine({ ...fx('cardTrip'), status: 'EXPIRED', date_passed: true, editable: false }) })
      await page.goto(COURIER('me/requests/201'))
      await expect(page.getByTestId('mine-date-passed')).toContainText('تاریخ این سفر گذشته است.')
      await expect(page.getByTestId('mine-post-again')).toContainText('ثبت سفر تازه')
    })

    test('C18.8 connection lost on close: a read tells', async ({ page }) => {
      let closed = false
      await mockCourier(page, {
        [MINE]: () => mine(closed ? { status: 'CANCELLED', editable: false } : {}),
        'POST me/requests/201/close/': () => {
          closed = true
          return 'abort'
        },
      })
      await page.goto(COURIER('me/requests/201'))
      await page.getByTestId('mine-close').click()
      await page.getByTestId('mine-close-go').click()
      await expect(page.getByTestId('mine-closed')).toBeVisible()
    })

    test('C12 not mine is not found', async ({ page }) => {
      await mockCourier(page, { [MINE]: coded(404, 'not_found') })
      await page.goto(COURIER('me/requests/201'))
      await expect(page.getByTestId('not-found')).toBeVisible()
    })
  })

  test.describe(`C13 on ${size}`, () => {
    test.use({ viewport })

    test('C13.1 active: each row goes where it should', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/matches'))
      const rows = page.getByTestId('inbox-row')
      await expect(rows).toHaveCount(3)
      await expect(rows.nth(0)).toContainText('منتظر پاسخ شما')
      await expect(rows.nth(0)).toContainText('علی')
      await expect(rows.nth(1)).toContainText('در انتظار پاسخ')
      await expect(rows.nth(2)).toContainText('وصل شدید')
      await expect(page.getByTestId('inbox')).toContainText('فعال (۳)')
      await rows.nth(2).click()
      await page.waitForURL(/\/me\/matches\/9/)
    })

    test('C13.1 incoming opens the answer screen', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/matches'))
      await page.getByTestId('inbox-row').first().click()
      await expect(page.getByTestId('intro-received')).toBeVisible()
    })

    test('C13.1 an accepted intro with its match shows once', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/': (route) => (new URL(route.request().url()).searchParams.get('state') === 'archive' ? { status: 200, body: empty } : { status: 200, body: { ...empty, accepted: [fx('introAccepted')], matches: [fx('match')] } }) })
      await page.goto(COURIER('me/matches'))
      await expect(page.getByTestId('inbox-row')).toHaveCount(1)
    })

    test('C13.2 archive with declined and expired', async ({ page }) => {
      await mockCourier(page, {
        'GET me/matches/': (route) =>
          new URL(route.request().url()).searchParams.get('state') === 'archive'
            ? { status: 200, body: { ...empty, closed: [{ ...fx('introTarget'), id: 1, status: 'DECLINED' }, { ...fx('introTarget'), id: 2, status: 'EXPIRED' }], matches: [{ ...fx('match'), status: 'COMPLETED' }] } }
            : { status: 200, body: empty },
      })
      await page.goto(COURIER('me/matches'))
      await page.getByRole('tab', { name: /بایگانی/ }).click()
      for (const word of ['رد شد', 'منقضی', 'تمام‌شده']) await expect(page.getByTestId('inbox')).toContainText(word)
    })

    test('C13.3 empty', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/': { status: 200, body: empty } })
      await page.goto(COURIER('me/matches'))
      await expect(page.getByTestId('inbox-empty')).toContainText('هنوز تطبیقی نیست')
      await page.getByTestId('inbox-search').click()
      await expect(page.getByTestId('role-choice').or(page.getByTestId('routes'))).toBeVisible()
    })
  })

  test.describe(`C14 on ${size}`, () => {
    test.use({ viewport })

    test('C14.1 finish asks, then the rate form', async ({ page }) => {
      const calls = await mockCourier(page, { 'POST me/matches/9/complete/': matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }) })
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('finish').click()
      await expect(page.getByTestId('finish-confirm')).toContainText('سفارش تمام شود؟')
      expect(calls.some((call) => call.path.endsWith('complete/'))).toBe(false)
      await page.getByTestId('finish-go').click()
      await expect(page.getByTestId('rate-form')).toContainText('امتیاز به این سفارش')
    })

    test('C14.2 rate form says the channel will show it', async ({ page }) => {
      const calls = await mockCourier(page, {
        [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }),
        'POST me/matches/9/rate/': { status: 201, body: matchWith({ status: 'COMPLETED' }, { can_complete: false, my_score: 4 }).body },
      })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('rate-disclosure')).toContainText('امتیاز ۳ به بالا با نظر شما در کانال منتشر می‌شود.')
      await page.getByTestId('star-4').click()
      await page.getByTestId('rate-comment').fill('تحویل سر وقت بود.')
      await page.getByTestId('rate-send').click()
      await expect(page.getByTestId('toast')).toContainText('امتیاز شما ثبت شد')
      await expect(page.getByTestId('rated').getByTestId('rate-waiting')).toContainText('منتظر امتیاز طرف مقابل.')
      expect(calls.find((call) => call.path.endsWith('rate/'))?.body).toEqual({ score: 4, comment: 'تحویل سر وقت بود.' })
    })

    test('C14.4 both rated', async ({ page }) => {
      await mockCourier(page, { [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, my_score: 4, their_score: 5 }) })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('my-score')).toBeVisible()
      await expect(page.getByTestId('their-score')).toBeVisible()
      await page.getByTestId('thanks').click()
      expect(await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)).toContain(fx('match').contact.url)
    })

    test('C14.5 no star and a long comment', async ({ page }) => {
      const calls = await mockCourier(page, { [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }) })
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('rate-send').click()
      await expect(page.getByTestId('rate-no-score')).toContainText('امتیازی از ۱ تا ۵ انتخاب کنید.')
      await page.getByTestId('star-3').click()
      await page.getByTestId('rate-comment').fill('x'.repeat(501))
      await expect(page.getByTestId('rate-too-long')).toContainText('نظر باید کمتر از ۵۰۰ نویسه باشد.')
      await page.getByTestId('rate-send').click()
      expect(calls.some((call) => call.path.endsWith('rate/'))).toBe(false)
    })

    test('C14.6 already rated', async ({ page }) => {
      let rated = false
      await mockCourier(page, {
        [MATCH]: () => matchWith({ status: 'COMPLETED' }, rated ? { can_complete: false, my_score: 4 } : { can_complete: false, can_rate: true }),
        'POST me/matches/9/rate/': () => {
          rated = true
          return coded(409, 'already_rated')
        },
      })
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('star-4').click()
      await page.getByTestId('rate-send').click()
      await expect(page.getByTestId('already-rated')).toContainText('قبلاً برای این سفارش امتیاز داده‌اید.')
    })

    test('C14.7 no longer open', async ({ page }) => {
      await mockCourier(page, { 'POST me/matches/9/complete/': coded(409, 'match_not_open') })
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('finish').click()
      await page.getByTestId('finish-go').click()
      await expect(page.getByTestId('match-not-open')).toContainText('فقط هماهنگی باز را می‌شود تمام کرد.')
      await page.getByTestId('to-inbox').click()
      await page.waitForURL(/\/me\/matches$|\/me\/matches\?/)
    })

    test('C19.4 to C14.1 an accepted intro finishes, then the rate form', async ({ page }) => {
      const calls = await mockCourier(page, {
        'GET me/intros/7/': { status: 200, body: fx('introAccepted') },
        'POST me/intros/7/complete/': matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }),
        [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }),
      })
      await page.goto(COURIER('me/intros/7'))
      await expect(page.getByTestId('contact-card')).toBeVisible()
      await page.getByTestId('finish').click()
      await expect(page.getByTestId('finish-confirm')).toContainText('سفارش تمام شود؟')
      expect(calls.some((call) => call.path.endsWith('complete/'))).toBe(false)
      await page.getByTestId('finish-go').click()
      await page.waitForURL(/\/me\/matches\/9/)
      await expect(page.getByTestId('rate-form')).toContainText('امتیاز به این سفارش')
      expect(calls.some((call) => call.method === 'POST' && call.path === 'me/intros/7/complete/')).toBe(true)
      expect(await noSidewaysScroll(page)).toBe(true)
    })

    test('C14 an intro finished elsewhere opens the match', async ({ page }) => {
      await mockCourier(page, {
        'GET me/intros/7/': { status: 200, body: { ...fx('introAccepted'), finish_state: 'finished' } },
        [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true }),
      })
      await page.goto(COURIER('me/intros/7'))
      await expect(page.getByTestId('rate-form')).toBeVisible()
    })

    test('C14.7 an intro no longer open', async ({ page }) => {
      await mockCourier(page, { 'GET me/intros/7/': { status: 200, body: fx('introAccepted') }, 'POST me/intros/7/complete/': coded(409, 'match_not_open') })
      await page.goto(COURIER('me/intros/7'))
      await page.getByTestId('finish').click()
      await page.getByTestId('finish-go').click()
      await expect(page.getByTestId('match-not-open')).toContainText('فقط هماهنگی باز را می‌شود تمام کرد.')
    })

    test('C19.4 an intro that cannot finish shows the contact alone', async ({ page }) => {
      await mockCourier(page, { 'GET me/intros/7/': { status: 200, body: { ...fx('introAccepted'), finish_state: 'closed', match_id: null } } })
      await page.goto(COURIER('me/intros/7'))
      await expect(page.getByTestId('contact-card')).toBeVisible()
      await expect(page.getByTestId('finish')).toHaveCount(0)
    })

    test('C14 the other side finished first: rate', async ({ page }) => {
      await mockCourier(page, { [MATCH]: matchWith({ status: 'COMPLETED' }, { can_complete: false, can_rate: true, their_score: 5 }) })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('rate-form')).toBeVisible()
    })
  })
}
