import { expect, test, type Page } from '@playwright/test'
import { APP, coded, mockApi, PHONE, VIEWPORTS } from './api-mock'
import { listing, TORONTO as CARDS } from '../src/mocks/fixtures'
import { p4 } from '../src/mocks/posting'
import { BERLIN, check, MANUAL, NO_USERNAME, ON, open, P, parseWith, PNG, pressBack, READY_OFFER, requests, seed, stubStorage, TEXT, upload } from './post-helpers'

const BODY = 'POST /api/v1/me/listings/'
const next = (page: Page) => page.getByTestId('post-next')
const PREVIEW = { ...READY_OFFER, step: 5 }

async function writeAndRead(page: Page, text = TEXT) {
  await page.locator('#post-text').fill(text)
  await next(page).click()
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`F18 ${size}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
    })

    test('#1 choose the kind, with my listings next to it', async ({ page }) => {
      await open(page, P)
      await expect(page.getByRole('heading', { name: 'چه آگهی‌ای ثبت می‌کنید؟' })).toBeVisible()
      await expect(page.getByTestId('post-city')).toContainText('تورنتو')
      await expect(page.getByTestId('kind-offer')).toContainText('خانه دارم')
      await expect(page.getByTestId('to-mine')).toContainText('۱ منتشر شده')
      await check(page, 'F18-01', size)
      await page.getByTestId('kind-offer').click()
      await expect(page.getByTestId('offer-wizard')).toBeVisible()
    })

    test('#19 no city', async ({ page }) => {
      await page.addInitScript(() => localStorage.removeItem('chamedoon.city'))
      await mockApi(page, ON)
      await page.goto(APP(P))
      await page.getByTestId('kind-offer').click()
      await expect(page.getByTestId('city-error')).toHaveText('کشور را انتخاب کنید.')
      await expect(page.getByTestId('offer-wizard')).toHaveCount(0)
      await check(page, 'F18-19', size)
    })

    test('#2 the text, counted, with «ادامه» waiting for 20 letters', async ({ page }) => {
      await open(page, `${P}/offer`)
      await expect(next(page)).toBeDisabled()
      await page.locator('#post-text').fill(TEXT)
      await expect(page.getByTestId('post-text')).toContainText(`/ ۱٬۵۰۰`)
      await expect(next(page)).toBeEnabled()
      await expect(next(page)).toHaveText('ادامه')
      await check(page, 'F18-02', size)
    })

    test('#18 short text', async ({ page }) => {
      await open(page, `${P}/offer`)
      await page.locator('#post-text').fill('اتاق خالی')
      await page.locator('#post-text').blur()
      await expect(page.getByTestId('post-text')).toContainText('توضیحات کوتاه است. حداقل ۲۰ حرف بنویسید.')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-18', size)
    })

    test('#17 a phone in the text, and the way to take it out', async ({ page }) => {
      const reads = requests(page, 'POST', '/me/posts/parse/')
      await open(page, `${P}/offer`)
      await page.locator('#post-text').fill(`${TEXT} 0416 555 0100`)
      await expect(page.getByTestId('post-text')).toContainText('شماره یا آیدی را در متن ننویسید. راه تماس را در قدم بعد وارد کنید.')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-17', size)
      await page.getByTestId('strip-contact').click()
      await expect(page.locator('#post-text')).not.toHaveValue(/0416/)
      await expect(next(page)).toBeEnabled()
      expect(reads).toHaveLength(0)
    })

    test('#17 the server finds a handle the phone did not', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_contact'))
      await writeAndRead(page)
      await expect(page.getByTestId('post-text')).toContainText('شماره یا آیدی را در متن ننویسید.')
      await expect(page.getByTestId('parsed-card')).toHaveCount(0)
      await expect(next(page)).toBeDisabled()
    })

    test('#25 reading the text', async ({ page }) => {
      await open(page, `${P}/offer`, { 'POST /api/v1/me/posts/parse/': 'hang' })
      await writeAndRead(page)
      await expect(page.getByTestId('parsing')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-25', size)
    })

    test('#3 what was read, sent with the chosen city', async ({ page }) => {
      await open(page, `${P}/offer`)
      const asked = page.waitForRequest((request) => request.url().includes('/me/posts/parse/'))
      await writeAndRead(page)
      const sent = (await asked).postDataJSON()
      expect(sent).toEqual({ text: TEXT, kind: 'offer', country: 'CA', city: 'Toronto' })
      const card = page.getByTestId('parsed-card')
      await expect(card).toContainText('۱ خوابه')
      await expect(card).toContainText('بلندمدت')
      await expect(card).toContainText('نورث یورک')
      await expect(page.getByTestId('chip-price')).toContainText('۱٬۸۵۰')
      await expect(card).toContainText('در ماه')
      await expect(card).toContainText('داخل اجاره')
      await expect(card).toContainText('حداقل ۱۲ ماه')
      await expect(next(page)).toHaveText('ادامه: عکس‌ها')
      await expect(next(page)).toBeEnabled()
      await check(page, 'F18-03', size)
    })

    test('#4 a sheet fixes one row, and only «ذخیره» keeps it', async ({ page }) => {
      await open(page, `${P}/offer`)
      await writeAndRead(page)
      await page.getByTestId('chip-type').click()
      const sheet = page.getByTestId('section-sheet')
      await expect(sheet).toBeVisible()
      await sheet.getByRole('button', { name: '۲ خوابه' }).click()
      await check(page, 'F18-04', size)
      await sheet.getByRole('button', { name: 'بستن' }).click()
      await expect(page.getByTestId('chip-type')).toContainText('۱ خوابه')
      await page.getByTestId('chip-type').click()
      await page.getByTestId('section-sheet').getByRole('button', { name: '۲ خوابه' }).click()
      await page.getByTestId('section-save').click()
      await expect(page.getByTestId('chip-type')).toContainText('۲ خوابه')
    })

    test('#11 price and area missing', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_incomplete'))
      await writeAndRead(page)
      await expect(page.getByTestId('parsed-incomplete')).toHaveText('قیمت و محله را کامل کنید.')
      await expect(page.getByTestId('chip-price')).toHaveText('افزودن قیمت')
      await expect(page.getByTestId('chip-area')).toHaveText('انتخاب محله')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-11', size)
      await page.getByTestId('chip-price').click()
      await page.locator('#sheet-price').fill('۱٬۸۵۰')
      await page.getByTestId('section-save').click()
      await expect(page.getByTestId('parsed-incomplete')).toHaveText('محله را کامل کنید.')
    })

    test('#26 the text looks like a wanted post', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_wanted'))
      await writeAndRead(page)
      await expect(page.getByTestId('kind-mismatch')).toContainText('این متن شبیه دنبال خانه است.')
      await check(page, 'F18-26', size)
      await page.getByTestId('to-wanted').click()
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toBeNull()
      expect(JSON.parse((await page.evaluate(() => localStorage.getItem('chamedoon.post.wanted')))!).text).toBe(TEXT)
    })

    test('#26 a saved wanted draft is not replaced without asking', async ({ page }) => {
      const wanted = JSON.stringify({ kind: 'wanted', stage: 'manual', step: 2, text: 'پیش‌نویس قبلی من برای دنبال خانه', city: BERLIN, savedAt: 1 })
      await page.addInitScript((value) => {
        if (!sessionStorage.getItem('seeded')) localStorage.setItem('chamedoon.post.wanted', value)
        sessionStorage.setItem('seeded', '1')
      }, wanted)
      await open(page, `${P}/offer`, parseWith('parse_wanted'))
      await writeAndRead(page)
      const keptWanted = () => page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.post.wanted')!).text)
      const offerText = () => page.evaluate(() => JSON.parse(localStorage.getItem('chamedoon.post.offer') ?? '{}').text)

      await page.getByTestId('to-wanted').click()
      await expect(page.getByTestId('wanted-waiting')).toContainText('پیش‌نویس قبلی من')
      await check(page, 'F18-26b', size)
      expect(await keptWanted()).toBe('پیش‌نویس قبلی من برای دنبال خانه')
      await page.getByTestId('wanted-stay').click()
      await expect(page.getByTestId('wanted-waiting')).toHaveCount(0)
      expect(await offerText()).toBe(TEXT)

      await page.getByTestId('to-wanted').click()
      await page.getByTestId('wanted-open-saved').click()
      await expect(page).toHaveURL(/post\/wanted/)
      expect(await keptWanted()).toBe('پیش‌نویس قبلی من برای دنبال خانه')
      expect(await offerText()).toBe(TEXT)
    })

    test('#26 replacing is a choice, and moves the text', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.post.wanted', JSON.stringify({ kind: 'wanted', stage: 'manual', step: 1, text: 'پیش‌نویس قبلی من برای دنبال خانه', savedAt: 1 })))
      await open(page, `${P}/offer`, parseWith('parse_wanted'))
      await writeAndRead(page)
      await page.getByTestId('to-wanted').click()
      await page.getByTestId('wanted-replace').click()
      await expect(page).toHaveURL(/post\/wanted/)
      expect(JSON.parse((await page.evaluate(() => localStorage.getItem('chamedoon.post.wanted')))!).text).toBe(TEXT)
    })

    test('#27 the text names another city', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_other_city'))
      await writeAndRead(page)
      await expect(page.getByTestId('city-mismatch')).toContainText('این متن از مونترال می‌گوید.')
      await check(page, 'F18-27', size)
      await page.getByTestId('change-city').click()
      await page.getByTestId('city-sheet').getByRole('radio', { name: 'مونترال' }).click()
      await expect(page.getByTestId('city-mismatch')).toHaveCount(0)
    })

    test('#28 sublet, weekly, with an end date', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_sublet'))
      await writeAndRead(page)
      const card = page.getByTestId('parsed-card')
      await expect(card).toContainText('اتاق')
      await expect(card).toContainText('سابلت')
      await expect(card).toContainText('در هفته')
      await expect(card).toContainText('۳۱ ژانویه')
      await check(page, 'F18-28', size)
    })

    test('#29 Germany: warm or cold, Meldung, deposit', async ({ page }) => {
      await seed(page, 'offer', { text: TEXT }, BERLIN)
      await open(page, `${P}/offer`, parseWith('parse_german'))
      await page.getByTestId('post-next').click()
      const card = page.getByTestId('parsed-card')
      await expect(card).toContainText('اتاق WG')
      await expect(card).toContainText('موقت (Zwischenmiete)')
      await expect(card).toContainText('سرد')
      await expect(card).toContainText('ممکن است')
      await expect(card).toContainText('۱٬۳۶۰')
      await expect(card).toContainText('€')
      await check(page, 'F18-29', size)
    })

    for (const [name, answer] of [
      ['parse_cap', coded(429, 'parse_cap')],
      ['parse_unavailable', coded(503, 'parse_unavailable')],
      ['a server error', coded(500, 'server_error')],
      ['nothing understood', { status: 200, body: p4('parse_nothing') }],
    ] as const) {
      test(`#10 ${name} opens the form with the text kept`, async ({ page }) => {
        await open(page, `${P}/offer`, { 'POST /api/v1/me/posts/parse/': answer })
        await writeAndRead(page)
        await expect(page.getByTestId('manual-form')).toBeVisible()
        await expect(page.getByTestId('manual-note')).toHaveText('خواندن خودکار متن الان در دسترس نیست. فرم را پر کنید.')
        await expect(page.locator('#post-text-manual')).toHaveValue(TEXT)
        if (name === 'parse_cap') await check(page, 'F18-10', size)
      })
    }

    test('smart_posting off starts on the form, reads nothing, says nothing', async ({ page }) => {
      const reads = requests(page, 'POST', '/me/posts/parse/')
      await open(page, `${P}/offer`, MANUAL)
      await expect(page.getByTestId('manual-form')).toBeVisible()
      await expect(page.getByTestId('manual-note')).toHaveCount(0)
      await page.getByRole('button', { name: '۱ خوابه' }).click()
      await page.locator('#post-price').fill('1850')
      await page.locator('#post-text-manual').fill(TEXT)
      await next(page).click()
      await expect(page.getByTestId('post-steps')).toContainText('قدم ۲ از ۵')
      expect(reads).toHaveLength(0)
    })

    test('#20 the form says what is missing', async ({ page }) => {
      await open(page, `${P}/offer`, MANUAL)
      await page.locator('#post-price').fill('1850')
      const form = page.getByTestId('manual-type')
      await expect(form).toContainText('نوع ملک را انتخاب کنید.')
      await expect(page.getByTestId('manual-text')).toContainText('توضیحات کوتاه است.')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-20', size)
    })

    test('#20 a city the server refused is asked again', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: coded(400, 'validation_failed', { fields: { city: ['required'] } }) })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('manual-form')).toContainText('شهر را بنویسید.')
      await expect(next(page)).toBeDisabled()
      await page.getByTestId('city-input').fill('Toronto')
      await expect(next(page)).toBeEnabled()
    })

    test('#5 a photo on its way holds «ادامه», then it is the main photo', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 2 })
      let release = () => {}
      const gate = new Promise<void>((resolve) => (release = resolve))
      await open(page, `${P}/offer`)
      await page.unroute('https://storage.test/**')
      await page.route('https://storage.test/**', async (route) => {
        const headers = { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'PUT, OPTIONS', 'access-control-allow-headers': '*' }
        if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers })
        await gate
        return route.fulfill({ status: 200, headers })
      })
      const signed = page.waitForRequest((request) => request.url().includes('/me/photos/') && request.method() === 'POST')
      const put = page.waitForRequest((request) => request.method() === 'PUT')
      await upload(page)
      const ticket = (await signed).postDataJSON()
      expect(ticket).toEqual({ content_type: 'image/png', size: PNG.length })
      await expect(page.locator('[data-testid="photo-tile"][data-status="uploading"]')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-05', size)
      release()
      expect((await put).headers()['authorization']).toBeUndefined()
      const ready = page.locator('[data-testid="photo-tile"][data-status="ready"]')
      await expect(ready).toContainText('عکس اصلی')
      await expect(next(page)).toBeEnabled()
      await expect(page.getByTestId('photo-count')).toHaveText('(۱ از ۶)')
    })

    test('#12 a file over the limit', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 2 })
      await open(page, `${P}/offer`, { 'POST /api/v1/me/photos/': coded(413, 'too_big') })
      await upload(page)
      await expect(page.getByTestId('toast')).toContainText('حجم یک عکس بیشتر از ۳ مگابایت است')
      await expect(page.locator('[data-testid="photo-tile"][data-status="failed"]')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-12', size)
      await page.getByRole('button', { name: 'حذف عکس' }).click()
      await expect(next(page)).toBeEnabled()
    })

    test('#30 not a photo, a failed send and a seventh photo, each on its own tile', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 2 })
      await open(page, `${P}/offer`)
      await page.getByTestId('photo-input').setInputFiles({ name: 'a.pdf', mimeType: 'application/pdf', buffer: PNG })
      await expect(page.locator('[data-testid="photo-tile"][data-status="bad"]')).toContainText('این فایل عکس نیست')
      await page.unroute('https://storage.test/**')
      await stubStorage(page, 403)
      await upload(page)
      await expect(page.locator('[data-testid="photo-tile"][data-status="failed"]')).toContainText('عکس ارسال نشد')
      await page.unroute('https://storage.test/**')
      await stubStorage(page, 200)
      await page.getByTestId('photo-input').setInputFiles(Array.from({ length: 5 }, (_, index) => ({ name: `p${index}.png`, mimeType: 'image/png', buffer: PNG })))
      await expect(page.getByTestId('toast')).toContainText('حداکثر ۶ عکس')
      await expect(page.locator('[data-testid="photo-tile"]')).toHaveCount(6)
      await expect(page.getByTestId('photo-add')).toHaveCount(0)
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-30', size)
      await page.locator('[data-testid="photo-tile"][data-status="failed"]').getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(page.locator('[data-testid="photo-tile"][data-status="failed"]')).toHaveCount(0)
      await page.locator('[data-testid="photo-tile"][data-status="bad"]').getByRole('button', { name: 'حذف عکس' }).click()
      await expect(next(page)).toBeEnabled()
    })

    test('a confirm that says the file never arrived fails only its tile', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 2 })
      await open(page, `${P}/offer`)
      await page.route(/\/me\/photos\/[0-9a-f-]+\/$/, (route) => route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ code: 'upload_failed', detail: 'x' }) }))
      await upload(page)
      await expect(page.locator('[data-testid="photo-tile"][data-status="failed"]')).toContainText('عکس ارسال نشد')
    })

    test('#6 area from the list, and the address when it is switched on', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, area: '', address: '', step: 3 })
      await open(page, `${P}/offer`)
      await expect(next(page)).toBeDisabled()
      await page.getByTestId('area-select').selectOption({ label: 'نورث یورک' })
      await page.getByTestId('address-input').fill('5000 Yonge St')
      await expect(next(page)).toBeEnabled()
      await expect(next(page)).toHaveText('ادامه: راه تماس')
      await check(page, 'F18-06', size)
    })

    test('#6 a failed area list leaves a text field', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, area: '', step: 3 })
      await open(page, `${P}/offer`, { '/api/v1/public/areas/': coded(500, 'server_error') })
      await page.getByTestId('area-input').fill('نورث یورک')
      await expect(next(page)).toBeEnabled()
    })

    test('#7 the contact: the username shows, the phone is optional', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 4 })
      await open(page, `${P}/offer`)
      await expect(page.getByTestId('contact-username')).toContainText('@test')
      await expect(next(page)).toBeEnabled()
      await page.getByTestId('phone-input').fill('+1 416 555 0100')
      await expect(page.getByRole('switch', { name: 'نمایش شماره در آگهی' })).toBeChecked()
      await check(page, 'F18-07', size)
    })

    test('#13 no username: the phone is needed', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, step: 4 })
      await open(page, `${P}/offer`, { 'POST /api/v1/auth/exchange/': NO_USERNAME })
      await expect(page.getByTestId('no-username')).toContainText('نام کاربری تلگرام ندارید')
      await expect(page.getByText('شماره تلفن لازم است')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F18-13', size)
      await page.getByTestId('phone-input').fill('+1 416 555 0100')
      await expect(next(page)).toBeEnabled()
    })

    test('#8 the preview holds no contact, and the phone goes in its field alone', async ({ page }) => {
      await seed(page, 'offer', { ...PREVIEW, phone: '+1 416 555 0100' })
      await open(page, `${P}/offer`)
      const post = page.getByTestId('channel-preview')
      await expect(post).toContainText('۱٬۸۵۰')
      await expect(post).toContainText('5000 Yonge St')
      const shown = await page.locator('main').innerText()
      expect(shown).not.toContain('416')
      expect(shown).not.toContain('@test')
      expect(shown).not.toContain('username')
      await check(page, 'F18-08', size)
      const sent = page.waitForRequest((request) => request.url().includes('/me/listings/') && request.method() === 'POST')
      await page.getByTestId('post-publish').click()
      const body = (await sent).postDataJSON()
      expect(body.phone).toBe('+1 416 555 0100')
      const { phone: _phone, ...rest } = body
      expect(JSON.stringify(rest)).not.toContain('416')
      expect(body.area).toBe('نورث یورک · 5000 Yonge St')
      expect(body.force_new).toBe(false)
      await expect(page.getByTestId('post-live')).toBeVisible()
    })

    test('the switches keep a phone and an address out of the post', async ({ page }) => {
      await seed(page, 'offer', { ...PREVIEW, phone: '+1 416 555 0100', showPhone: false, showAddress: false })
      await open(page, `${P}/offer`)
      await expect(page.getByTestId('channel-preview')).not.toContainText('5000 Yonge St')
      const sent = page.waitForRequest((request) => request.url().includes('/me/listings/') && request.method() === 'POST')
      await page.getByTestId('post-publish').click()
      const body = (await sent).postDataJSON()
      expect(body.phone).toBe('')
      expect(body.area).toBe('نورث یورک')
    })

    test('join gate: not a member of the city chats holds the post, «عضو شدم» sends it', async ({ page }) => {
      let member = false
      const chats = [
        { kind: 'channel', title: 'چمدون - خانه | تورنتو', join_url: 'https://t.me/chamedoon_toronto_khane', photo_url: '' },
        { kind: 'group', title: 'چمدون - خانه | تورنتو · گفت‌وگو', join_url: 'https://t.me/+group', photo_url: '' },
      ]
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`)
      await page.route('**/api/v1/me/join-check/', (route) => route.fulfill({ json: member ? { chats: [], gate: 'member' } : { chats, gate: 'not_member' } }))
      const sent = requests(page, 'POST', '/me/listings/')
      await page.getByTestId('post-publish').click()
      const sheet = page.getByTestId('post-join-sheet')
      await expect(sheet).toContainText('لطفاً اول عضو کانال و گروه چمدون تورنتو شوید')
      await expect(sheet).toContainText('آگهی شما در همین کانال منتشر می‌شود.')
      await expect(sheet).not.toContainText('t.me')
      await check(page, 'F18-join', size)
      expect(sent).toHaveLength(0)
      member = true
      await sheet.getByRole('button', { name: 'عضو شدم، ثبت آگهی' }).click()
      await expect(page.getByTestId('post-live')).toBeVisible()
      expect(sent).toHaveLength(1)
    })

    test('join gate: a failed check never holds the post', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { 'POST /api/v1/me/join-check/': 'abort' })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-live')).toBeVisible()
    })

    test('#22 publishing holds both buttons', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: 'hang' })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-publish')).toBeDisabled()
      await expect(page.getByTestId('post-edit')).toBeDisabled()
      await check(page, 'F18-22', size)
    })

    test('#23 a failure keeps the text, and a retry sends the same key', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      const sent = requests(page, 'POST', '/me/listings/')
      await open(page, `${P}/offer`, { [BODY]: coded(500, 'server_error') })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-failed')).toHaveText('آگهی ثبت نشد. متن شما ذخیره است.')
      await expect(page.getByTestId('post-publish')).toHaveText('تلاش مجدد')
      await check(page, 'F18-23', size)
      await page.getByTestId('post-publish').click()
      await expect.poll(() => sent.length).toBe(2)
      const keys = sent.map((request) => request.headers()['idempotency-key'])
      expect(keys[0]).toBeTruthy()
      expect(keys[1]).toBe(keys[0])
    })

    test('the retry on a rate limit sends the same key', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      const sent = requests(page, 'POST', '/me/listings/')
      await open(page, `${P}/offer`)
      let first = true
      await page.route(/\/me\/listings\/$/, async (route) => {
        if (route.request().method() !== 'POST') return route.fallback()
        const refused = first
        first = false
        return route.fulfill({ status: refused ? 429 : 201, contentType: 'application/json', body: JSON.stringify(refused ? { code: 'rate_limited', detail: 'x' } : p4('post_live')) })
      })
      await page.getByTestId('post-publish').click()
      await page.getByTestId('toast').getByRole('button', { name: 'تلاش مجدد' }).click()
      await expect(page.getByTestId('post-live')).toBeVisible()
      expect(sent).toHaveLength(2)
      expect(sent[0].headers()['idempotency-key']).toBeTruthy()
      expect(sent[1].headers()['idempotency-key']).toBe(sent[0].headers()['idempotency-key'])
    })

    test('a draft that already has a key keeps it', async ({ page }) => {
      await seed(page, 'offer', { ...PREVIEW, idempotencyKey: 'key-from-before-the-reload' })
      const sent = page.waitForRequest((request) => request.url().includes('/me/listings/') && request.method() === 'POST')
      await open(page, `${P}/offer`)
      await page.getByTestId('post-publish').click()
      expect((await sent).headers()['idempotency-key']).toBe('key-from-before-the-reload')
    })

    test('a double tap makes one call', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      const sent = requests(page, 'POST', '/me/listings/')
      await open(page, `${P}/offer`)
      await page.getByTestId('post-publish').dblclick({ force: true })
      await expect(page.getByTestId('post-live')).toBeVisible()
      expect(sent).toHaveLength(1)
    })

    test('#32 published at once, and the draft is gone', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`)
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-done')).toContainText('آگهی شما منتشر شد')
      await expect(page.getByTestId('held-badge')).toHaveCount(0)
      await check(page, 'F18-32', size)
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toBeNull()
      await expect(page.getByTestId('done-mine')).toBeVisible()
      await expect(page.getByTestId('done-view')).toBeVisible()
    })

    test('#9 held by review', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: { status: 201, body: p4('post_held') } })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-done')).toContainText('آگهی شما ثبت شد')
      await expect(page.getByTestId('held-badge')).toHaveText('در انتظار بررسی')
      await check(page, 'F18-09', size)
    })

    test('#16 my own duplicate: update it, see it, or post anyway', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      const id = CARDS[0].id
      const edits = requests(page, 'PATCH', `/me/listings/${id}/`)
      await open(page, `${P}/offer`, {
        [BODY]: { status: 409, body: { ...p4('error_duplicate_self'), duplicate_of: id } },
        [`GET /api/v1/me/listings/${id}/`]: { status: 200, body: { is_owner: true, review_state: 'live', listing: listing(CARDS[0]) } },
      })
      await page.getByTestId('post-publish').click()
      const sheet = page.getByTestId('dup-own')
      await expect(sheet).toContainText('این آگهی قبلاً ثبت شده است')
      await expect(sheet.getByTestId('static-card')).toContainText('منتشر شده')
      await check(page, 'F18-16', size)
      await page.getByTestId('dup-update').click()
      await expect(page.getByTestId('post-live')).toBeVisible()
      expect(edits).toHaveLength(1)
      expect(edits[0].postDataJSON().base_updated_at).toBe(p4('form_offer').updated_at)
    })

    test('#21 someone else’s: post anyway with force_new, showing no contact', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      const id = CARDS[0].id
      const sent = requests(page, 'POST', '/me/listings/')
      let first = true
      await open(page, `${P}/offer`, { [`GET /api/v1/me/listings/${id}/`]: { status: 200, body: { is_owner: false, review_state: 'live', listing: listing(CARDS[0]) } } })
      await page.route(/\/me\/listings\/$/, async (route) => {
        if (route.request().method() !== 'POST') return route.fallback()
        const body = first ? { ...p4('error_duplicate_other'), duplicate_of: id } : p4('post_live')
        const status = first ? 409 : 201
        first = false
        return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      })
      await page.getByTestId('post-publish').click()
      const sheet = page.getByTestId('dup-other')
      await expect(sheet).toContainText('آگهی مشابهی در چمدون هست')
      expect(await sheet.innerText()).not.toContain('@')
      await expect(sheet.getByTestId('dup-update')).toHaveCount(0)
      await check(page, 'F18-21', size)
      await page.getByTestId('dup-anyway').click()
      await expect(page.getByTestId('post-live')).toBeVisible()
      expect(sent[1].postDataJSON().force_new).toBe(true)
      expect(sent[1].headers()['idempotency-key']).toBe(sent[0].headers()['idempotency-key'])
    })

    test('a fixed field sends the person to its step', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: coded(400, 'validation_failed', { fields: { phone: ['no_contact'] } }) })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-steps')).toContainText('قدم ۴ از ۵')
      await expect(page.getByText('شماره تلفن لازم است')).toBeVisible()
    })

    test('the contact the server finds in the text comes back to the text', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: coded(400, 'validation_failed', { fields: { description: ['contact_in_text'] } }) })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-text')).toContainText('شماره یا آیدی را در متن ننویسید.')
    })

    test('post_cap has a way out', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`, { [BODY]: coded(429, 'post_cap') })
      await page.getByTestId('post-publish').click()
      const note = page.getByTestId('post-cap')
      await expect(note).toContainText('امروز به سقف ثبت آگهی رسیدید.')
      await expect(note.getByRole('link', { name: 'آگهی‌های من' })).toHaveAttribute('href', /\/mine$/)
    })

    test('photo_gone goes back to the photos, and asks each one again', async ({ page }) => {
      const photo = { photo_id: '3f2b8c1e-6a51-4c7e-9b0d-1c2d3e4f1234', url: 'https://media.test/f.jpg', thumb: 'https://media.test/t.jpg' }
      await seed(page, 'offer', { ...PREVIEW, photos: [photo] })
      await open(page, `${P}/offer`, { [BODY]: coded(404, 'photo_gone'), [`POST /api/v1/me/photos/${photo.photo_id}/`]: coded(404, 'photo_gone') })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-steps')).toContainText('قدم ۲ از ۵')
      await expect(page.locator('[data-testid="photo-tile"][data-status="bad"]')).toContainText('عکس ارسال نشد')
    })

    test('Back goes one step at a time', async ({ page }) => {
      await seed(page, 'offer', PREVIEW)
      await open(page, `${P}/offer`)
      for (const step of ['۴', '۳', '۲', '۱']) {
        await pressBack(page)
        await expect(page.getByTestId('post-steps')).toContainText(`قدم ${step} از ۵`)
      }
    })

    test('#14 leaving with text asks, and saves', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, stage: 'manual', photos: [] })
      await open(page, `${P}/offer`, MANUAL)
      await pressBack(page)
      const sheet = page.getByTestId('save-draft')
      await expect(sheet).toContainText('پیش‌نویس ذخیره شود؟')
      await expect(sheet).toContainText('آپارتمان ۱ خوابه · نورث یورک')
      await check(page, 'F18-14', size)
      await page.getByTestId('draft-continue').click()
      await expect(sheet).toHaveCount(0)
      await pressBack(page)
      await page.getByTestId('draft-save').click()
      // #15
      await expect(page.getByTestId('draft-home')).toContainText('پیش‌نویس شما')
      await expect(page.getByTestId('draft-home')).toContainText('پیش‌نویس')
      await expect(page.getByTestId('toast')).toHaveCount(0)
      await check(page, 'F18-15', size)
      await page.getByTestId('draft-delete').click()
      await expect(page.getByTestId('draft-home')).toHaveCount(0)
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toBeNull()
    })

    test('#14 «حذف و خروج» clears it', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, stage: 'manual' })
      await open(page, `${P}/offer`, MANUAL)
      await pressBack(page)
      await page.getByTestId('draft-discard').click()
      await expect(page.getByTestId('post-choose')).toBeVisible()
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toBeNull()
    })

    test.describe('a new post beside a saved draft', () => {
      const KEY = 'chamedoon.post.offer'
      const stored = (page: Page, key = KEY) => page.evaluate((name) => localStorage.getItem(name), key)
      const saved = { ...READY_OFFER, text: 'پیش‌نویس قدیمی که نباید از بین برود، با متن کافی.' }

      async function startNew(page: Page) {
        await seed(page, 'offer', saved)
        await open(page, P)
        await expect(page.getByTestId('draft-home')).toBeVisible()
        const before = await stored(page)
        await page.getByTestId('kind-offer').click()
        await expect(page.locator('#post-text')).toHaveValue('')
        return before
      }

      test('Back with no text leaves it', async ({ page }) => {
        const before = await startNew(page)
        await pressBack(page)
        await expect(page.getByTestId('draft-resume')).toBeVisible()
        expect(await stored(page)).toBe(before)
      })

      test('typing leaves it, a reload keeps what was typed, and «حذف و خروج» takes only the new one', async ({ page }) => {
        const before = await startNew(page)
        await page.locator('#post-text').fill(TEXT)
        await expect.poll(() => stored(page, `${KEY}.fresh`)).toContain('Yonge')
        expect(await stored(page)).toBe(before)
        // Navigating dropped the mock's query, and a reload needs it. The history entry, and its `fresh`, stay.
        await page.evaluate(() => history.replaceState(history.state, '', `${location.pathname}?tgmock=ios&tginit=signed`))
        await page.reload()
        await expect(page.locator('#post-text')).toHaveValue(TEXT)
        expect(await stored(page)).toBe(before)
        await pressBack(page)
        await page.getByTestId('draft-discard').click()
        await expect(page.getByTestId('draft-resume')).toBeVisible()
        expect(await stored(page)).toBe(before)
        expect(await stored(page, `${KEY}.fresh`)).toBeNull()
      })

      test('left without an answer, it waits beside the saved one', async ({ page }) => {
        const before = await startNew(page)
        await page.locator('#post-text').fill(TEXT)
        await expect.poll(() => stored(page, `${KEY}.fresh`)).toContain('Yonge')
        await page.goto(APP(P))
        await expect(page.getByTestId('unsaved-resume')).toBeVisible()
        await expect(page.getByTestId('draft-resume')).toBeVisible()
        expect(await stored(page)).toBe(before)
        await page.getByTestId('unsaved-resume').click()
        await expect(page.locator('#post-text')).toHaveValue(TEXT)
      })

      test('publishing it leaves the saved one', async ({ page }) => {
        const before = await startNew(page)
        await page.locator('#post-text').fill(TEXT)
        await next(page).click()
        for (let step = 0; step < 4; step += 1) await next(page).click()
        await page.getByTestId('post-publish').click()
        await expect(page.getByTestId('post-live')).toBeVisible()
        expect(await stored(page)).toBe(before)
        expect(await stored(page, `${KEY}.fresh`)).toBeNull()
      })

      test('sending the text to a wanted post leaves it', async ({ page }) => {
        await seed(page, 'offer', saved)
        await open(page, P, parseWith('parse_wanted'))
        const before = await stored(page)
        await page.getByTestId('kind-offer').click()
        await page.locator('#post-text').fill(TEXT)
        await next(page).click()
        await page.getByTestId('to-wanted').click()
        expect(await stored(page)).toBe(before)
      })

      test('only «ذخیره پیش‌نویس» replaces it', async ({ page }) => {
        const before = await startNew(page)
        await page.locator('#post-text').fill(TEXT)
        await pressBack(page)
        expect(await stored(page)).toBe(before)
        await page.getByTestId('draft-save').click()
        await expect(page.getByTestId('draft-resume')).toBeVisible()
        expect(JSON.parse((await stored(page))!).text).toBe(TEXT)
        expect(await stored(page, `${KEY}.fresh`)).toBeNull()
      })
    })

    test('#24 the draft comes back after a reopen', async ({ page }) => {
      await seed(page, 'offer', READY_OFFER)
      await open(page, P)
      await expect(page.getByTestId('toast')).toContainText('پیش‌نویس شما برگشت.')
      await expect(page.getByTestId('draft-home')).toBeVisible()
      await check(page, 'F18-24', size)
      await page.getByTestId('draft-resume').click()
      await expect(page.getByTestId('parsed-card')).toBeVisible()
    })

    test('#24 a draft survives a reload with what was typed', async ({ page }) => {
      await open(page, `${P}/offer`)
      await page.locator('#post-text').fill(TEXT)
      await page.reload()
      await expect(page.locator('#post-text')).toHaveValue(TEXT)
    })

    test('#31 a draft that could not be saved', async ({ page }) => {
      await seed(page, 'offer', { ...READY_OFFER, stage: 'manual' })
      await open(page, `${P}/offer`, MANUAL)
      await page.evaluate(() => {
        Storage.prototype.setItem = () => {
          throw new Error('full')
        }
      })
      await pressBack(page)
      await page.getByTestId('draft-save').click()
      await expect(page.getByTestId('draft-failed')).toHaveText('پیش‌نویس ذخیره نشد')
      await expect(page.getByTestId('draft-save')).toHaveText('تلاش مجدد')
      await check(page, 'F18-31', size)
    })
  })
}

test('the post tab opens F18 when the flag is on', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await open(page, '/app/v2/')
  await page.getByTestId('main-nav').getByRole('tab', { name: 'ثبت آگهی' }).click()
  await expect(page.getByTestId('post-choose')).toBeVisible()
  await expect(page).toHaveURL(/\/app\/v2\/post/)
})

test('feature_off mid-flow closes the entry for the session, and the draft stays', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await seed(page, 'offer', PREVIEW)
  await open(page, `${P}/offer`, { [BODY]: coded(403, 'feature_off') })
  await page.getByTestId('post-publish').click()
  await expect(page).toHaveURL(/\/app\/v2\/?(\?|$)/)
  expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toContain('Toronto')
})

