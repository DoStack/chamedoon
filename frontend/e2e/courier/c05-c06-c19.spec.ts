import { expect, test, type Page } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

const R = 'GET requests/101/'
const detail = (changes: object) => ({ status: 200, body: { ...fx('requestDetail'), ...changes } })
const ROUTE = { side: 'DEMAND', origin: 'CA:toronto', destination: 'IR:tehran' }
const intro = (changes: object) => ({ status: 200, body: { ...fx('introTarget'), ...changes } })

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C05 on ${size}`, () => {
    test.use({ viewport })

    test('C05.1 traveler: facts, no name, cost negotiable', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('request/101'))
      const screen = page.getByTestId('request')
      // v4: hero with both places, the date in the middle, facts on glass.
      await expect(page.getByTestId('request-hero')).toContainText('تورنتو')
      await expect(page.getByTestId('request-hero')).toContainText('تهران')
      await expect(screen).toContainText('تاریخ پرواز')
      await expect(screen).toContainText('جای خالی')
      await expect(screen).toContainText('توافقی')
      await expect(screen).toContainText('می‌برد')
      await expect(screen.getByTestId('refused-chip')).toContainText('دارو')
      await expect(screen.getByTestId('who')).toContainText('نام بعد از قبول')
      await expect(page.getByTestId('trust-badge')).toContainText('۲ ارسال موفق')
      await expect(page.getByTestId('intro-ask').filter({ visible: true })).toContainText('درخواست معرفی')
      expect(await noSidewaysScroll(page)).toBe(true)
    })

    test('C05.2 package', async ({ page }) => {
      await mockCourier(page, { [R]: { status: 200, body: fx('requestDetailPackage') } })
      await page.goto(COURIER('request/101'))
      const screen = page.getByTestId('request')
      await expect(screen).toContainText('باید برسد')
      await expect(screen).toContainText('وزن')
      await expect(screen).toContainText('داخل بسته')
    })

    test('C05.3 several stops', async ({ page }) => {
      const stops = [fx('requestDetail').stops[0], { country: 'IR', city: 'mashhad', name_fa: 'مشهد', name_en: 'Mashhad' }]
      await mockCourier(page, { [R]: detail({ stops }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request')).toContainText('مقصدها')
      await expect(page.getByTestId('request')).toContainText('تهران و مشهد')
    })

    test('C05.4 thin data: unknown, and the action still works', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ date: null, date_kind: null, capacity_kg: null, categories: [], refused: [], description: '', excluded_other_text: '', trust_count: 0 }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request')).toContainText('نامشخص')
      await expect(page.getByTestId('trust-badge')).toHaveCount(0)
      await expect(page.getByTestId('request')).not.toContainText('می‌برد')
      await page.getByTestId('intro-ask').filter({ visible: true }).click()
      await expect(page.getByTestId('ask-sheet')).toBeVisible()
    })

    test('C05.5 and C19.10 imported: the button says so before the tap, and nothing is sent', async ({ page }) => {
      const calls = await mockCourier(page, { [R]: detail({ reach: 'published', trust_count: 0 }) })
      await page.goto(COURIER('request/101'))
      await expect(page.locator('body')).not.toContainText('sara_travel')
      await page.getByTestId('published-open').filter({ visible: true }).click()
      await expect(page.getByTestId('imported-note')).toContainText('این آگهی از کانال‌های عمومی آمده')
      await expect(page.getByTestId('published-contact')).toContainText('@sara_travel')
      await page.getByTestId('source-open').filter({ visible: true }).click()
      expect(await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)).toContain(fx('published').source_url)
      expect(calls.some((call) => call.path.endsWith('/intro/'))).toBe(false)
    })

    test('C05.6 closed: same route and alert, with the route from the server', async ({ page }) => {
      await mockCourier(page, { [R]: coded(410, 'request_inactive', { route: ROUTE }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request-inactive')).toContainText('این درخواست دیگر فعال نیست')
      await page.getByTestId('same-route').click()
      await page.waitForURL(/from=CA%3Atoronto/)
    })

    test('C05.7 loading', async ({ page }) => {
      await mockCourier(page, { [R]: 'hang' })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request-loading')).toBeVisible()
    })

    test('C05.8 share copies the link', async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
      await mockCourier(page)
      await page.goto(COURIER('request/101'))
      await page.getByTestId('share').filter({ visible: true }).click()
      await expect(page.getByTestId('toast')).toContainText('لینک کپی شد')
    })

    test('C05.9 date passed', async ({ page }) => {
      await mockCourier(page, { [R]: coded(410, 'request_date_passed', { route: ROUTE }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request-date-passed')).toContainText('تاریخ این پرواز گذشته است')
    })

    test('C05.10 edited while open: fresh data on return, with a note', async ({ page }) => {
      let body: object = fx('requestDetail')
      await mockCourier(page, { [R]: () => ({ status: 200, body }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request')).toBeVisible()
      await expect(page.getByTestId('request')).toContainText('تورنتو')
      body = { ...body, description: 'تازه شد' }
      await page.evaluate(() => {
        window.dispatchEvent(new Event('visibilitychange'))
        document.dispatchEvent(new Event('visibilitychange'))
      })
      await expect(page.getByTestId('toast')).toContainText('این درخواست تازه شد')
      await expect(page.getByTestId('request')).toContainText('تازه شد')
    })

    test('C05 unknown id is not found, own request goes to mine', async ({ page }) => {
      await mockCourier(page, { [R]: coded(404, 'not_found'), 'GET requests/102/': coded(409, 'own_request', { request_id: 55 }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('not-found')).toBeVisible()
      await page.goto(COURIER('request/102'))
      await page.waitForURL(/\/me\/requests\/55/)
    })

    test('C01.4 from a channel post, back is that route', async ({ page }) => {
      await mockCourier(page, { 'POST deeplink/': { status: 200, body: { ...fx('deeplink'), kind: 'request', id: 101, screen: null } } })
      await page.goto(COURIER('', '&tgstart=explore_101'))
      await expect(page.getByTestId('request')).toBeVisible()
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('routes')).toBeVisible()
      await expect(page.getByTestId('route-from')).toContainText('تورنتو')
      await expect(page.getByTestId('route-to')).toContainText('تهران')
    })

    test('C03 tap a card opens it, and back returns to the list', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('?side=DEMAND&from=CA:toronto&to=IR:tehran'))
      await page.getByTestId('request-card').first().click()
      await expect(page.getByTestId('request')).toBeVisible()
      await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(page.getByTestId('route-from')).toContainText('تورنتو')
    })
  })

  test.describe(`C19 on ${size}`, () => {
    test.use({ viewport })

    const ask = async (page: Page) => {
      await page.goto(COURIER('request/101'))
      await page.getByTestId('intro-ask').filter({ visible: true }).click()
      return page.getByTestId('ask-sheet')
    }

    test('C19.1, C19.16, C19.2 ask with an own request, sending, then waiting', async ({ page }) => {
      let body: object = detail({ my_request: fx('packageCard') }).body
      let release: () => void = () => {}
      const held = new Promise<void>((resolve) => (release = resolve))
      await mockCourier(page, { [R]: () => ({ status: 200, body }) })
      await page.route('**/api/v1/courier/requests/101/intro/', async (route) => {
        await held
        body = { ...body, intro_id: 7, intro_status: 'PENDING' }
        await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...fx('introTarget'), role: 'requester', requester_first_name: null, requester_trust_count: null }) })
      })
      const sheet = await ask(page)
      await expect(sheet).toContainText('بسته‌ی شما')
      await expect(sheet).toContainText('آیدی‌ها فقط اگر قبول کنند رد و بدل می‌شود.')
      await sheet.getByTestId('ask-send').click()
      await expect(sheet.getByTestId('ask-send')).toBeDisabled()
      await expect(sheet.getByTestId('ask-send')).toContainText('در حال فرستادن')
      release()
      await expect(page.getByTestId('toast')).toContainText('درخواست معرفی فرستاده شد')
      await expect(page.getByTestId('intro-waiting').filter({ visible: true })).toBeDisabled()
    })

    test('C19.3 the target sees first name and their request, no id', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/intros/7'))
      const screen = page.getByTestId('intro-received')
      await expect(screen).toContainText('علی می‌خواهد با شما در ارتباط باشد')
      await expect(screen.getByTestId('intro-their-request')).toContainText('بسته‌ی ایشان')
      await expect(screen).toContainText('آیدی‌ها فقط اگر قبول کنید رد و بدل می‌شود.')
      await expect(page.getByTestId('contact-card')).toHaveCount(0)
    })

    test('C19.4 accept opens the contact, with the toast', async ({ page }) => {
      const calls = await mockCourier(page, { 'POST me/intros/7/respond/': { status: 200, body: fx('introAccepted') } })
      await page.goto(COURIER('me/intros/7'))
      await page.getByTestId('intro-accept').click()
      await expect(page.getByTestId('toast')).toContainText('آیدی شما برای ایشان فرستاده شد')
      await expect(page.getByTestId('contact-username')).toContainText('@ali_tehran')
      await expect(page.getByTestId('contact-send')).toBeVisible()
      expect(calls.find((call) => call.path.endsWith('respond/'))?.body).toEqual({ accept: true })
    })

    test('C19.5 declined, the requester sees no reason and no id', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ intro_id: 7, intro_status: 'DECLINED' }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('intro-declined')).toContainText('معرفی این بار انجام نشد')
      await expect(page.getByTestId('same-route')).toBeVisible()
      await expect(page.getByTestId('other-alert')).toBeVisible()
    })

    test('C19.6 unanswered at 72 h', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ intro_id: 7, intro_status: 'EXPIRED' }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('intro-unanswered')).toContainText('این درخواست معرفی منقضی شد')
    })

    test('C19.7 own cap', async ({ page }) => {
      await mockCourier(page, { 'POST requests/101/intro/': coded(429, 'intro_cap_self') })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('cap-self')).toContainText('امروز ۵ درخواست معرفی فرستادید')
      await expect(page.getByTestId('intro-ask').filter({ visible: true })).toBeDisabled()
    })

    test('C19.8 target cap', async ({ page }) => {
      await mockCourier(page, { 'POST requests/101/intro/': coded(429, 'intro_cap_target') })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('cap-target')).toContainText('درخواست‌های زیادی برای ایشان رفته است')
    })

    test('C19.9 switch off: no contact path, alert instead', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ reach: 'off' }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('intros-off')).toContainText('معرفی دوطرفه موقتاً خاموش است.')
      await expect(page.getByTestId('intro-ask')).toHaveCount(0)
      await expect(page.getByTestId('request-alert').filter({ visible: true })).toBeVisible()
    })

    test('C19.11 no own request: send anyway or post first', async ({ page }) => {
      await mockCourier(page)
      const sheet = await ask(page)
      await expect(sheet).not.toContainText('بسته‌ی شما')
      await sheet.getByTestId('ask-post-first').click()
      await page.waitForURL(/\/post\/package\?from=CA%3Atoronto&to=IR%3Atehran/)
    })

    test('C19.12 target with no package on the asker side', async ({ page }) => {
      await mockCourier(page, { 'GET me/intros/7/': intro({ requester_request: null, requester_trust_count: 0 }) })
      await page.goto(COURIER('me/intros/7'))
      await expect(page.getByTestId('intro-received')).toBeVisible()
      await expect(page.getByTestId('intro-their-request')).toHaveCount(0)
    })

    test('C19.13 the target declines', async ({ page }) => {
      await mockCourier(page, { 'POST me/intros/7/respond/': intro({ status: 'DECLINED' }) })
      await page.goto(COURIER('me/intros/7'))
      await page.getByTestId('intro-decline').click()
      await expect(page.getByTestId('toast')).toContainText('ثبت شد. آیدی شما به ایشان داده نمی‌شود.')
      await expect(page.getByTestId('intro-i-declined')).toContainText('رد شد')
    })

    test('C19.14 the bot cannot reach the target', async ({ page }) => {
      await mockCourier(page, { 'POST requests/101/intro/': coded(409, 'intro_undelivered') })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('intro-undelivered')).toContainText('پیام به ایشان نرسید')
      await expect(page.getByTestId('same-route')).toBeVisible()
    })

    test('C19.15 imported with no public contact', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ reach: 'none', trust_count: 0 }) })
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('imported-none')).toContainText('راه تماس عمومی هم ندارد')
      await expect(page.getByTestId('request-alert').filter({ visible: true })).toBeVisible()
    })

    test('C19.15 from a stale tap: published turns out to have nothing', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ reach: 'published', trust_count: 0 }), 'requests/101/published/': coded(409, 'intro_no_public_contact') })
      await page.goto(COURIER('request/101'))
      await page.getByTestId('published-open').filter({ visible: true }).click()
      await expect(page.getByTestId('imported-none')).toBeVisible()
    })

    test('C19.17 send fails, retry is the same button', async ({ page }) => {
      let fail = true
      await mockCourier(page, { 'POST requests/101/intro/': () => (fail ? coded(500, 'server_error') : { status: 201, body: { ...fx('introTarget'), role: 'requester' } }) })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(sheet.getByTestId('ask-failed')).toContainText('درخواست فرستاده نشد.')
      await expect(sheet.getByTestId('ask-send')).toContainText('تلاش مجدد')
      fail = false
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('toast')).toContainText('درخواست معرفی فرستاده شد')
    })

    test('C18.8 connection lost on send, and the intro had landed: waiting, one intro', async ({ page }) => {
      let landed = false
      const calls = await mockCourier(page, {
        'POST requests/101/intro/': () => {
          landed = true
          return 'abort'
        },
        [R]: () => detail(landed ? { intro_id: 7, intro_status: 'PENDING' } : {}),
      })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('intro-waiting').filter({ visible: true })).toBeVisible()
      expect(calls.filter((call) => call.path.endsWith('/intro/')).length).toBe(1)
    })

    test('C18.8 connection lost on send, and it did not land: retry in the sheet', async ({ page }) => {
      await mockCourier(page, { 'POST requests/101/intro/': 'abort' })
      const sheet = await ask(page)
      await sheet.getByTestId('ask-send').click()
      await expect(page.getByTestId('ask-failed')).toBeVisible()
    })

    test('C19.18 late answer', async ({ page }) => {
      await mockCourier(page, { 'POST me/intros/7/respond/': coded(409, 'intro_expired') })
      await page.goto(COURIER('me/intros/7'))
      await page.getByTestId('intro-accept').click()
      await expect(page.getByTestId('intro-late')).toContainText('این درخواست معرفی منقضی شده است')
    })

    test('C06.5 target request closed before the answer', async ({ page }) => {
      await mockCourier(page, { 'POST me/intros/7/respond/': coded(410, 'request_inactive', { route: ROUTE }) })
      await page.goto(COURIER('me/intros/7'))
      await page.getByTestId('intro-accept').click()
      await expect(page.getByTestId('intro-target-gone')).toContainText('این سفر بسته شد')
    })

    test('C19 intro of someone else is not found', async ({ page }) => {
      await mockCourier(page, { 'GET me/intros/7/': coded(404, 'not_found') })
      await page.goto(COURIER('me/intros/7'))
      await expect(page.getByTestId('not-found')).toBeVisible()
    })

    test('C19 requester with a pending intro lands on the request', async ({ page }) => {
      await mockCourier(page, { 'GET me/intros/7/': intro({ role: 'requester', requester_first_name: null, requester_trust_count: null }), [R]: detail({ intro_id: 7, intro_status: 'PENDING' }) })
      await page.goto(COURIER('me/intros/7'))
      await page.waitForURL(/\/request\/101/)
    })

    test('no name, username or id anywhere before acceptance', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('request/101'))
      await expect(page.getByTestId('request')).toBeVisible()
      const text = await page.locator('body').innerText()
      for (const word of ['@', 'علی', 'ali_tehran', 'sara_travel']) expect(text).not.toContain(word)
    })
  })

  test.describe(`C06 on ${size}`, () => {
    test.use({ viewport })

    test('C06.1 connected: name, username, draft', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/matches/9'))
      const screen = page.getByTestId('match-connected')
      await expect(screen.getByTestId('match-head')).toContainText('وصل شدید')
      await expect(screen.getByTestId('contact-card')).toContainText('سارا')
      await expect(screen.getByTestId('contact-username')).toContainText('@sara_travel')
      await expect(screen.getByTestId('contact-draft')).not.toHaveValue('')
      await expect(screen.getByTestId('contact-id')).toHaveCount(0)
      await screen.getByTestId('contact-send').click()
      expect(await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)).toContain(fx('match').contact.url)
    })

    test('C06.2 copied', async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
      await mockCourier(page)
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('contact-copy').click()
      await expect(page.getByTestId('toast')).toContainText('پیام کپی شد')
      await expect(page.getByTestId('contact-copy')).toContainText('کپی شد')
    })

    test('C06.3 no username: id and copy only', async ({ page }) => {
      const contact = { ...fx('match').contact, telegram_username: null, telegram_user_id: 123456789, url: '', no_username: true }
      await mockCourier(page, { 'GET me/matches/9/': { status: 200, body: { ...fx('match'), contact } } })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('contact-id')).toContainText('123456789')
      await expect(page.getByTestId('no-username')).toContainText('نام کاربری عمومی ندارد')
      await expect(page.getByTestId('contact-send')).toHaveCount(0)
      await expect(page.getByTestId('contact-copy')).toContainText('کپی پیام')
    })

    test('C06 copy denied: the text stays selectable', async ({ page }) => {
      await mockCourier(page)
      await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: () => Promise.reject(new Error('denied')) } }))
      await page.goto(COURIER('me/matches/9'))
      await page.getByTestId('contact-copy').click()
      await expect(page.getByTestId('toast')).toContainText('پیام کپی نشد')
      await expect(page.getByTestId('contact-draft')).toBeFocused()
    })

    test('C06.4 match expired: contact hidden', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/9/': coded(410, 'match_expired', { route: ROUTE }) })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('match-expired')).toContainText('این هماهنگی منقضی شد')
      await expect(page.getByTestId('contact-card')).toHaveCount(0)
    })

    test('C06.5 taken after accepting', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/9/': coded(410, 'request_inactive', { route: ROUTE }) })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('match-taken')).toContainText('این سفر بسته شد')
    })

    test('C06.6 Telegram does not open: the username is copied', async ({ page, context }) => {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
      await mockCourier(page)
      await page.goto(COURIER('me/matches/9', '&tgversion=6.0'))
      await page.getByTestId('contact-send').click()
      await expect(page.getByTestId('toast')).toContainText('تلگرام باز نشد. نام کاربری کپی شد.')
    })

    test('C06.7 already connected: the request page goes to the contact', async ({ page }) => {
      await mockCourier(page, { [R]: detail({ intro_id: 7, intro_status: 'ACCEPTED' }), 'GET me/intros/7/': { status: 200, body: fx('introAccepted') } })
      await page.goto(COURIER('request/101'))
      await page.getByTestId('intro-connected').filter({ visible: true }).click()
      await expect(page.getByTestId('intro-accepted').getByTestId('contact-username')).toBeVisible()
    })

    test('C06 a direct URL before acceptance shows no contact and continues the intro', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/9/': coded(403, 'contact_not_accepted', { intro_id: 7 }) })
      await page.goto(COURIER('me/matches/9'))
      await page.waitForURL(/\/me\/intros\/7/)
      await expect(page.getByTestId('contact-card')).toHaveCount(0)
    })

    test('C06 a guessed id with no intro is not found', async ({ page }) => {
      await mockCourier(page, { 'GET me/matches/9/': coded(403, 'contact_not_accepted', { intro_id: null }) })
      await page.goto(COURIER('me/matches/9'))
      await expect(page.getByTestId('not-found')).toBeVisible()
    })
  })
}
