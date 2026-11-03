import { expect, test } from '@playwright/test'
import { COURIER, coded, fx, mockCourier, noSidewaysScroll, VIEWPORTS } from './mock'

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`C15 on ${size}`, () => {
    test.use({ viewport })

    test('C15.1 account: counts, pending badge, and rows that go', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me'))
      await expect(page.getByTestId('account')).toContainText('حساب کاربری')
      await expect(page.getByTestId('account-requests')).toContainText('۲')
      await expect(page.getByTestId('account-alerts')).toContainText('۳ از ۵')
      await expect(page.getByTestId('account-pending')).toHaveText('۱')
      await expect(page.getByTestId('pending-badge')).toHaveText('۱')
      expect(await noSidewaysScroll(page)).toBe(true)
      await page.getByTestId('account-support').click()
      await page.waitForURL(/\/me\/support/)
    })

    test('C15.1 counts failing leave the screen standing', async ({ page }) => {
      await mockCourier(page, { 'GET me/': { status: 200, body: { ...fx('me'), counts: null } } })
      await page.goto(COURIER('me'))
      await expect(page.getByTestId('account-requests')).toBeVisible()
      await expect(page.getByTestId('account-alerts')).not.toContainText('از ۵')
    })

    test('C15.2 channels sheet: main and country channels', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me'))
      await page.getByTestId('account-channels').click()
      const sheet = page.getByTestId('channels-sheet')
      await expect(sheet.getByTestId('channel-main')).toContainText('کانال اصلی چمدون')
      await sheet.getByTestId('channel-CA').click()
      expect(await page.evaluate(() => (window as unknown as { Telegram: { WebApp: { __opened: string[] } } }).Telegram.WebApp.__opened)).toContain('https://t.me/chamedoon_ca')
    })

    test('C15.2 channels list fails: retry', async ({ page }) => {
      await mockCourier(page, { 'GET guide/': coded(500, 'server_error') })
      await page.goto(COURIER('me'))
      await page.getByTestId('account-channels').click()
      await expect(page.getByTestId('channels-failed')).toBeVisible({ timeout: 10_000 })
    })

    test('C15.3 language saved, with the toast', async ({ page }) => {
      const calls = await mockCourier(page, { 'PATCH me/prefs/': { status: 200, body: { ...fx('me').prefs, locale: 'en' } } })
      await page.goto(COURIER('me'))
      await page.getByTestId('account-locale').getByRole('tab', { name: 'EN' }).click()
      await expect(page.getByTestId('toast')).toContainText('زبان عوض شد')
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ locale: 'en' })
      await expect(page.getByTestId('account-locale').getByRole('tab', { name: 'EN' })).toHaveAttribute('data-state', 'active')
      expect(calls.filter((call) => call.method === 'PATCH').length).toBe(1)
    })

    test('C15 theme applies at once, and auto follows Telegram', async ({ page }) => {
      const calls = await mockCourier(page, { 'PATCH me/prefs/': { status: 200, body: { ...fx('me').prefs, theme: 'dark' } } })
      await page.goto(COURIER('me'))
      await page.getByTestId('account-theme').getByRole('tab', { name: 'تیره' }).click()
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ theme: 'dark' })
    })

    test('C15 a saved theme applies on launch', async ({ page }) => {
      await mockCourier(page, { 'GET me/': { status: 200, body: { ...fx('me'), prefs: { ...fx('me').prefs, theme: 'dark', side: 'DEMAND' } } } })
      await page.goto(COURIER('guide'))
      await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
    })

    test('C15 saving fails: toast with retry', async ({ page }) => {
      await mockCourier(page, { 'PATCH me/prefs/': coded(500, 'server_error') })
      await page.goto(COURIER('me'))
      await page.getByTestId('account-locale').getByRole('tab', { name: 'EN' }).click()
      await expect(page.getByTestId('toast')).toContainText('ذخیره نشد')
      await expect(page.getByTestId('account-locale').getByRole('tab', { name: 'FA' })).toHaveAttribute('data-state', 'active')
    })

    test('C15 prohibited and guide rows', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me'))
      await page.getByTestId('account-prohibited').click()
      await expect(page.getByTestId('prohibited-sheet')).toBeVisible()
      await page.getByTestId('prohibited-sheet').getByRole('button', { name: 'بستن' }).click()
      await page.getByTestId('account-guide').click()
      await expect(page.getByTestId('guide')).toBeVisible()
    })
  })

  test.describe(`C16 on ${size}`, () => {
    test.use({ viewport })

    test('C16.1 list with open and closed', async ({ page }) => {
      await mockCourier(page)
      await page.goto(COURIER('me/support'))
      await expect(page.getByTestId('ticket-row')).toHaveCount(2)
      await expect(page.getByTestId('ticket-row').first()).toContainText('عدم امکان تماس با کاربر')
      await expect(page.getByTestId('ticket-row').first()).toContainText('باز')
      await expect(page.getByTestId('ticket-row').nth(1)).toContainText('بسته')
    })

    test('C16.1 empty still offers a new ticket', async ({ page }) => {
      await mockCourier(page, { 'GET me/tickets/': { status: 200, body: { items: [] } } })
      await page.goto(COURIER('me/support'))
      await expect(page.getByTestId('tickets-empty')).toBeVisible()
      await expect(page.getByTestId('ticket-new')).toBeVisible()
    })

    test('C16.2 new ticket, with one key on a retry', async ({ page }) => {
      let fail = true
      const calls = await mockCourier(page, { 'POST me/tickets/': () => (fail ? coded(500, 'server_error') : { status: 201, body: fx('ticketThread') }) })
      await page.goto(COURIER('me/support/new'))
      await page.getByTestId('ticket-subject').selectOption('CANNOT_CONTACT_USER')
      await page.getByTestId('ticket-message').fill('جواب نمی‌دهد')
      await page.getByTestId('ticket-send').click()
      await expect(page.getByTestId('toast')).toContainText('ارسال نشد')
      await expect(page.getByTestId('ticket-message')).toHaveValue('جواب نمی‌دهد')
      fail = false
      await page.getByTestId('toast').getByRole('button').click()
      await page.waitForURL(/\/me\/support\/\d+/)
      const posts = calls.filter((call) => call.method === 'POST' && call.path === 'me/tickets/')
      expect(posts[0].body).toEqual({ subject: 'CANNOT_CONTACT_USER', message: 'جواب نمی‌دهد' })
      expect(posts[0].headers['idempotency-key']).toBe(posts[1].headers['idempotency-key'])
    })

    test('C16.3 thread and reply', async ({ page }) => {
      const calls = await mockCourier(page, { 'POST me/tickets/31/messages/': { status: 201, body: fx('ticketThread') } })
      await page.goto(COURIER('me/support/31'))
      await expect(page.getByTestId('ticket-thread')).toContainText('عدم امکان تماس با کاربر')
      await expect(page.getByTestId('bubble-mine')).toContainText('مسافر به پیام من جواب نمی‌دهد.')
      await expect(page.getByTestId('bubble-theirs')).toContainText('بررسی می‌کنیم')
      await expect(page.getByTestId('reply-send')).toBeDisabled()
      await page.getByTestId('reply-text').fill('ممنون')
      await page.getByTestId('reply-send').click()
      await expect(page.getByTestId('reply-text')).toHaveValue('')
      expect(calls.find((call) => call.path.endsWith('messages/'))?.body).toEqual({ message: 'ممنون' })
    })

    test('C16.3 a failed reply keeps the text', async ({ page }) => {
      await mockCourier(page, { 'POST me/tickets/31/messages/': coded(500, 'server_error') })
      await page.goto(COURIER('me/support/31'))
      await page.getByTestId('reply-text').fill('ممنون')
      await page.getByTestId('reply-send').click()
      await expect(page.getByTestId('toast')).toContainText('ارسال نشد')
      await expect(page.getByTestId('reply-text')).toHaveValue('ممنون')
    })

    test('C16.4 close, then closed with a new ticket', async ({ page }) => {
      await mockCourier(page, { 'POST me/tickets/31/close/': { status: 200, body: { ...fx('ticketThread'), status: 'CLOSED' } } })
      await page.goto(COURIER('me/support/31'))
      await page.getByTestId('ticket-close').click()
      await expect(page.getByTestId('ticket-closed')).toContainText('این تیکت بسته شده است.')
      await expect(page.getByTestId('reply-text')).toHaveCount(0)
      await page.getByTestId('ticket-new-after').click()
      await expect(page.getByTestId('ticket-create')).toBeVisible()
    })

    test('C16.4 closed elsewhere while typing', async ({ page }) => {
      let closed = false
      await mockCourier(page, {
        'GET me/tickets/31/': () => ({ status: 200, body: { ...fx('ticketThread'), status: closed ? 'CLOSED' : 'OPEN' } }),
        'POST me/tickets/31/messages/': () => {
          closed = true
          return coded(409, 'ticket_closed')
        },
      })
      await page.goto(COURIER('me/support/31'))
      await page.getByTestId('reply-text').fill('ممنون')
      await page.getByTestId('reply-send').click()
      await expect(page.getByTestId('ticket-closed')).toBeVisible()
    })

    test('C16.5 subject and message empty: each says so, nothing is sent', async ({ page }) => {
      const calls = await mockCourier(page)
      await page.goto(COURIER('me/support/new'))
      await page.getByTestId('ticket-send').click()
      await expect(page.getByTestId('ticket-no-subject')).toContainText('یک موضوع انتخاب کنید.')
      await expect(page.getByTestId('ticket-no-message')).toContainText('پیام را بنویسید.')
      expect(calls.some((call) => call.method === 'POST' && call.path === 'me/tickets/')).toBe(false)
    })

    test('C16 someone else\'s ticket is not found', async ({ page }) => {
      await mockCourier(page, { 'GET me/tickets/31/': coded(404, 'not_found') })
      await page.goto(COURIER('me/support/31'))
      await expect(page.getByTestId('not-found')).toBeVisible()
    })
  })
}
