import { expect, test, type Page } from '@playwright/test'
import { listing, TORONTO as CARDS } from '../src/mocks/fixtures'
import { FORM, p4, ROW } from '../src/mocks/posting'
import { coded, PHONE, VIEWPORTS } from './api-mock'
import { check, open, READY_OFFER, requests, seed } from './post-helpers'

const M = '/app/home/mine'
const LIST = 'GET /api/v1/me/listings/'
const rows = (...names: Parameters<typeof ROW>[0][]) => ({ [LIST]: { status: 200, body: { results: names.map(ROW), total: names.length, next_cursor: null } } })
const row = (page: Page, state: string) => page.locator(`[data-testid="mine-row"][data-state="${state}"]`)
const ALL = rows('mine_live', 'mine_review', 'mine_rejected_price', 'mine_closed')

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`F20 ${size}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
    })

    test('the delete question keeps the price readable', async ({ page }) => {
      await open(page, M, ALL)
      await page.getByTestId('row-menu').first().click()
      await page.getByTestId('menu-delete').click()
      await expect(page.getByTestId('delete-confirm').locator('bdi').first()).toHaveAttribute('dir', 'ltr')
    })

    test('#1 the four states, with no bookmark on any', async ({ page }) => {
      await open(page, M, ALL)
      await expect(row(page, 'live')).toContainText('منتشر شده')
      await expect(row(page, 'in_review')).toContainText('در انتظار بررسی')
      await expect(row(page, 'rejected')).toContainText('رد شد')
      await expect(row(page, 'rejected')).toContainText('قیمت ندارد')
      await expect(row(page, 'rejected')).toContainText('بدون قیمت')
      await expect(row(page, 'closed')).toContainText('بسته شد')
      await expect(page.getByRole('button', { name: /نشان/ })).toHaveCount(0)
      await expect(page.getByTestId('mine-new')).toHaveText('ثبت آگهی تازه')
      await check(page, 'F20-01', size)
    })

    test('#2 edit asks first, then opens the form with what is up now', async ({ page }) => {
      await open(page, M, ALL)
      await row(page, 'live').getByTestId('row-edit').click()
      const dialog = page.getByTestId('edit-confirm')
      await expect(dialog).toContainText('ویرایش آگهی؟')
      await expect(dialog).toContainText('آگهی شما تا پایان ویرایش همین‌طور می‌ماند.')
      await check(page, 'F20-02', size)
      await page.getByTestId('edit-go').click()
      await expect(page).toHaveURL(/mine\/50\/edit/)
      await expect(page.locator('#post-text-manual')).toHaveValue(FORM('form_offer').description!)
      await expect(page.getByTestId('manual-note')).toHaveCount(0)
    })

    test('an edit sends the whole form with the time it opened, and no parse', async ({ page }) => {
      const reads = requests(page, 'POST', '/me/posts/parse/')
      await open(page, `${M}/50/edit`)
      await page.locator('#post-price').fill('1900')
      await page.getByTestId('post-next').click()
      await expect(page.locator('[data-testid="photo-tile"][data-status="ready"]')).toHaveCount(1)
      await page.getByTestId('post-next').click()
      await page.getByTestId('post-next').click()
      await expect(page.getByTestId('phone-input')).toHaveValue('+1 416 555 0100')
      await page.getByTestId('post-next').click()
      const sent = page.waitForRequest((request) => request.method() === 'PATCH')
      await page.getByTestId('post-publish').click()
      const body = (await sent).postDataJSON()
      expect(body.price).toBe(1900)
      expect(body.base_updated_at).toBe(FORM('form_offer').updated_at)
      expect(body.photo_ids).toEqual([FORM('form_offer').photos[0].photo_id])
      expect(body.area).toBe('نورث یورک · 5000 Yonge St')
      await expect(page.getByTestId('post-done')).toBeVisible()
      expect(reads).toHaveLength(0)
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.offer'))).toBeNull()
    })

    test('a wanted listing edits on its own form', async ({ page }) => {
      await open(page, `${M}/57/edit`, { 'GET /api/v1/me/listings/57/form/': { status: 200, body: FORM('form_wanted') } })
      await expect(page.getByTestId('wanted-form')).toBeVisible()
      await expect(page.locator('#wanted-budget')).toHaveValue('1600')
      await expect(page.getByTestId('wanted-areas-row')).toContainText('ریچموند هیل')
    })

    test.describe('edit_conflict', () => {
      const MINE = 'متن من که نباید با تازه شدن فرم از بین برود، با طول کافی.'
      const SERVER = 'متن تازه‌ای که روی سرور است، با طول کافی برای این فرم.'
      const OPENED = FORM('form_offer').updated_at
      const NEWER = '2026-11-02T09:00:00.000000Z'
      const json = (status: number, body: unknown) => ({ status, contentType: 'application/json', body: JSON.stringify(body) })

      async function serverHasNewer(page: Page) {
        await page.route(/\/me\/listings\/50\/form\/$/, (route) => route.fulfill(json(200, { ...FORM('form_offer'), description: SERVER, updated_at: NEWER })))
      }

      // Refused once, then taken.
      async function conflictOnce(page: Page) {
        let first = true
        await page.route(/\/me\/listings\/50\/$/, (route) => {
          if (route.request().method() !== 'PATCH') return route.fallback()
          const refused = first
          first = false
          return route.fulfill(refused ? json(409, { code: 'edit_conflict', detail: 'x' }) : json(200, p4('post_live')))
        })
      }

      async function typeAndPublish(page: Page) {
        await page.locator('#post-text-manual').fill(MINE)
        for (let step = 0; step < 4; step += 1) await page.getByTestId('post-next').click()
        await page.getByTestId('post-publish').click()
      }

      test('asks, and keeping the text sends it over the newer version', async ({ page }) => {
        const sent = requests(page, 'PATCH', '/me/listings/50/')
        await open(page, `${M}/50/edit`)
        await conflictOnce(page)
        await typeAndPublish(page)
        await expect(page.getByTestId('edit-conflict')).toContainText('این آگهی تغییر کرده است')
        await check(page, 'F20-conflict', size)
        expect(sent).toHaveLength(1)
        expect(sent[0].postDataJSON().base_updated_at).toBe(OPENED)
        await serverHasNewer(page)
        await page.getByTestId('conflict-keep').click()
        await expect(page.getByTestId('edit-conflict')).toHaveCount(0)
        // Still on the same step, with nothing sent until the person says so.
        await expect(page.getByTestId('post-publish')).toBeVisible()
        expect(sent).toHaveLength(1)
        await page.getByTestId('post-publish').click()
        await expect(page.getByTestId('post-done')).toBeVisible()
        expect(sent[1].postDataJSON().base_updated_at).toBe(NEWER)
        expect(sent[1].postDataJSON().description).toBe(MINE)
      })

      test('the server copy replaces the text only when chosen', async ({ page }) => {
        const sent = requests(page, 'PATCH', '/me/listings/50/')
        await open(page, `${M}/50/edit`)
        await conflictOnce(page)
        await typeAndPublish(page)
        await serverHasNewer(page)
        await page.getByTestId('conflict-server').click()
        await expect(page.locator('#post-text-manual')).toHaveValue(SERVER)
        for (let step = 0; step < 4; step += 1) await page.getByTestId('post-next').click()
        await page.getByTestId('post-publish').click()
        await expect(page.getByTestId('post-done')).toBeVisible()
        expect(sent[1].postDataJSON().base_updated_at).toBe(NEWER)
        expect(sent[1].postDataJSON().description).toBe(SERVER)
      })

      test('a failed look at the server keeps the question and the text', async ({ page }) => {
        await open(page, `${M}/50/edit`)
        await conflictOnce(page)
        await typeAndPublish(page)
        await page.route(/\/me\/listings\/50\/form\/$/, (route) => route.fulfill(json(500, { code: 'server_error', detail: 'x' })))
        await page.getByTestId('conflict-server').click()
        await expect(page.getByTestId('toast')).toContainText('بارگذاری نشد')
        await expect(page.getByTestId('edit-conflict')).toBeVisible()
        await expect(page.getByTestId('post-publish')).toBeVisible()
      })

      test('coming back to the app does not touch the text or the base', async ({ page }) => {
        const sent = requests(page, 'PATCH', '/me/listings/50/')
        await open(page, `${M}/50/edit`)
        await page.locator('#post-text-manual').fill(MINE)
        await serverHasNewer(page)
        const reads = requests(page, 'GET', '/form/')
        await page.evaluate(() => {
          document.dispatchEvent(new Event('visibilitychange'))
          window.dispatchEvent(new Event('focus'))
          window.dispatchEvent(new Event('online'))
        })
        await page.waitForTimeout(300)
        expect(reads).toHaveLength(0)
        await expect(page.locator('#post-text-manual')).toHaveValue(MINE)
        for (let step = 0; step < 4; step += 1) await page.getByTestId('post-next').click()
        await page.getByTestId('post-publish').click()
        await expect(page.getByTestId('post-done')).toBeVisible()
        expect(sent[0].postDataJSON().base_updated_at).toBe(OPENED)
        expect(sent[0].postDataJSON().description).toBe(MINE)
      })

      test('a wanted edit asks too, and keeps the text', async ({ page }) => {
        await open(page, `${M}/57/edit`, { 'GET /api/v1/me/listings/57/form/': { status: 200, body: FORM('form_wanted') }, 'PATCH /api/v1/me/listings/57/': coded(409, 'edit_conflict') })
        await page.locator('#wanted-about').fill(MINE)
        await page.getByTestId('post-next').click()
        await page.getByTestId('post-publish').click()
        await page.getByTestId('conflict-keep').click()
        await expect(page.getByTestId('edit-conflict')).toHaveCount(0)
        await page.getByTestId('post-edit').click()
        await expect(page.locator('#wanted-about')).toHaveValue(MINE)
      })
    })

    test('a listing gone before the form opens has a way back', async ({ page }) => {
      await open(page, `${M}/50/edit`, { 'GET /api/v1/me/listings/50/form/': coded(404, 'listing_gone') })
      await expect(page.getByTestId('edit-gone')).toContainText('این آگهی دیگر نیست')
      await page.getByRole('button', { name: 'آگهی‌های من' }).click()
      await expect(page.getByTestId('mine-screen')).toBeVisible()
    })

    test('#3 closing, and «برگردان» on the toast', async ({ page }) => {
      await open(page, M, { ...rows('mine_live'), 'POST /api/v1/me/listings/50/status/': { status: 200, body: { ...ROW('mine_closed'), id: 50, card: { ...ROW('mine_live').card } } } })
      const sent = page.waitForRequest((request) => request.url().includes('/status/'))
      await page.getByTestId('row-close').click()
      expect((await sent).postDataJSON()).toEqual({ action: 'close' })
      await expect(row(page, 'closed')).toBeVisible()
      const toast = page.getByTestId('toast')
      await expect(toast).toContainText('آگهی بسته شد')
      await check(page, 'F20-03', size)
      const back = page.waitForRequest((request) => request.url().includes('/status/') && request.postDataJSON().action === 'restore')
      await toast.getByRole('button', { name: 'برگردان' }).click()
      await back
    })

    test('a status change that no longer applies reloads the list', async ({ page }) => {
      await open(page, M, { ...rows('mine_live'), 'POST /api/v1/me/listings/50/status/': coded(409, 'status_not_allowed') })
      const again = page.waitForRequest((request) => request.method() === 'GET' && /\/me\/listings\/$/.test(request.url()))
      await page.getByTestId('row-close').click()
      await again
      await expect(page.getByTestId('toast')).toContainText('این آگهی تغییر کرده است')
    })

    test('#4 refused for no price, fixed right here', async ({ page }) => {
      await open(page, M, { ...rows('mine_rejected_price'), 'GET /api/v1/me/listings/52/form/': { status: 200, body: { ...FORM('form_offer'), price: null } } })
      await row(page, 'rejected').getByTestId('mine-link').click()
      await expect(page).toHaveURL(/mine\/52$/)
      await expect(page.getByTestId('rejected-reason')).toHaveText('آگهی شما منتشر نشد، چون قیمت ندارد.')
      await expect(page.getByTestId('rejected-send')).toBeDisabled()
      await check(page, 'F20-04', size)
      await page.locator('#rejected-price').fill('۲٬۲۰۰')
      const sent = page.waitForRequest((request) => request.method() === 'PATCH')
      await page.getByTestId('rejected-send').click()
      const body = (await sent).postDataJSON()
      expect(body.price).toBe(2200)
      expect(body.base_updated_at).toBe(FORM('form_offer').updated_at)
      expect(body.photos).toBeUndefined()
      expect(body.updated_at).toBeUndefined()
      await expect(page.getByTestId('mine-screen')).toBeVisible()
    })

    test('#12 refused with no fix', async ({ page }) => {
      await open(page, M, rows('mine_rejected_other'))
      await row(page, 'rejected').getByTestId('mine-link').click()
      await expect(page.getByTestId('rejected-other')).toBeVisible()
      await expect(page.getByTestId('rejected-reason')).toHaveText('آگهی شما منتشر نشد، چون آگهی خانه نیست.')
      await expect(page.getByTestId('rejected-new')).toHaveText('ثبت آگهی تازه')
      await expect(page.locator('#rejected-price')).toHaveCount(0)
      await check(page, 'F20-12', size)
    })

    test('#5 this week’s question', async ({ page }) => {
      await open(page, M, rows('mine_check', 'mine_review'))
      const ask = page.getByTestId('owner-check')
      await expect(ask).toContainText('این هفته هم موجود است؟')
      await expect(ask.getByTestId('check-yes')).toHaveText('بله، موجود است')
      await expect(ask.getByTestId('check-no')).toHaveText('بسته شد')
      await expect(row(page, 'live').getByTestId('row-close')).toHaveCount(0)
      await check(page, 'F20-05', size)
      const sent = page.waitForRequest((request) => request.url().includes('/availability/'))
      await ask.getByTestId('check-yes').click()
      expect((await sent).postDataJSON()).toEqual({ answer: 'available' })
    })

    test('#6 delete asks first, and the card goes', async ({ page }) => {
      await open(page, M, rows('mine_live', 'mine_review'))
      await row(page, 'live').getByTestId('row-menu').click()
      await page.getByTestId('menu-delete').click()
      const dialog = page.getByTestId('delete-confirm')
      await expect(dialog).toContainText('آگهی حذف شود؟')
      await expect(dialog).toContainText('۱٬۸۵۰')
      await check(page, 'F20-06', size)
      const sent = page.waitForRequest((request) => request.method() === 'DELETE')
      await page.getByTestId('delete-go').click()
      await sent
      await expect(row(page, 'live')).toHaveCount(0)
      await expect(row(page, 'in_review')).toHaveCount(1)
    })

    test('#7 no listings yet', async ({ page }) => {
      await open(page, M, { [LIST]: { status: 200, body: p4('mine_list_empty') } })
      await expect(page.getByTestId('mine-empty')).toContainText('هنوز آگهی‌ای ندارید')
      await check(page, 'F20-07', size)
      await page.getByTestId('mine-post').click()
      await expect(page.getByTestId('post-choose')).toBeVisible()
    })

    test('#8 the menu', async ({ page }) => {
      await open(page, M, rows('mine_live'))
      await page.getByTestId('row-menu').click()
      const menu = page.getByTestId('row-menu-items')
      await expect(menu).toContainText('ویرایش')
      await expect(menu).toContainText('بستن آگهی')
      await expect(menu).toContainText('حذف')
      await check(page, 'F20-08', size)
      await page.getByTestId('menu-close').click()
      await expect(menu).toHaveCount(0)
    })

    test('#9 a wanted post has no photo, and the budget comes first', async ({ page }) => {
      await open(page, M, rows('mine_wanted', 'mine_live'))
      const card = page.locator('[data-testid="mine-row"][data-kind="wanted"]')
      await expect(card).toContainText('دنبال خانه · بودجه تا ۱٬۶۰۰')
      await expect(card).toContainText('نورث یورک، ریچموند هیل')
      await expect(card.getByTestId('photo-placeholder')).toHaveCount(0)
      await expect(card.getByTestId('row-close')).toBeVisible()
      await check(page, 'F20-09', size)
    })

    test('#10 the wanted question', async ({ page }) => {
      await open(page, M, rows('mine_wanted_check', 'mine_live'))
      const ask = page.getByTestId('owner-check')
      await expect(ask).toContainText('هنوز دنبال خانه هستید؟')
      await expect(ask.getByTestId('check-no')).toHaveText('خانه پیدا کردم')
      await check(page, 'F20-10', size)
      const sent = page.waitForRequest((request) => request.url().includes('/availability/'))
      await ask.getByTestId('check-no').click()
      expect((await sent).postDataJSON()).toEqual({ answer: 'taken' })
    })

    test('#11 closed for not being confirmed, with renew', async ({ page }) => {
      await open(page, M, { ...rows('mine_expired'), 'POST /api/v1/me/listings/55/status/': { status: 200, body: { ...ROW('mine_live'), id: 55 } } })
      await expect(page.getByTestId('row-expired')).toContainText('آگهی شما بسته شد، چون تأیید نشد.')
      await check(page, 'F20-11', size)
      const sent = page.waitForRequest((request) => request.url().includes('/status/'))
      await page.getByTestId('row-renew').click()
      expect((await sent).postDataJSON()).toEqual({ action: 'renew' })
      await expect(row(page, 'live')).toBeVisible()
    })

    test('loading, then a failure with a retry', async ({ page }) => {
      await open(page, M, { [LIST]: coded(500, 'server_error') })
      await expect(page.getByTestId('mine-failed')).toContainText('بارگذاری نشد', { timeout: 15_000 })
      await check(page, 'F20-failed', size)
    })

    test('a contact never reaches a card', async ({ page }) => {
      await open(page, M, ALL)
      await expect(row(page, 'live')).toBeVisible()
      const shown = await page.locator('main').innerText()
      expect(shown).not.toContain('@')
      expect(shown).not.toContain('416')
    })
  })
}

test('from a finished post to the list', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await seed(page, 'offer', { ...READY_OFFER, step: 5 })
  await open(page, '/app/home/post/offer')
  await page.getByTestId('post-publish').click()
  await page.getByTestId('done-mine').click()
  await expect(page.getByTestId('mine-screen')).toBeVisible()
})

test('the post cap has a way to the list', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await seed(page, 'offer', { ...READY_OFFER, step: 5 })
  await open(page, '/app/home/post/offer', { 'POST /api/v1/me/listings/': coded(429, 'post_cap') })
  await page.getByTestId('post-publish').click()
  await page.getByTestId('post-cap').getByRole('link', { name: 'آگهی‌های من' }).click()
  await expect(page.getByTestId('mine-screen')).toBeVisible()
})

test('the account tab opens F20 when the flag is on', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await open(page, '/app/home/')
  await page.getByTestId('main-nav').getByRole('tab', { name: 'حساب کاربری' }).click()
  await expect(page.getByTestId('mine-screen')).toBeVisible()
})

test('with features.account on too, the tab is F21 and its row opens F20', async ({ page }) => {
  await page.setViewportSize(PHONE)
  const features = { alerts: false, account: true, posting: true, smart_posting: true }
  await open(page, '/app/home/', { '/api/v1/public/meta/': { status: 200, body: { ...p4('meta_on'), features } } })
  await page.getByTestId('main-nav').getByRole('tab', { name: 'حساب کاربری' }).click()
  await page.getByTestId('row-mine').click()
  await expect(page.getByTestId('mine-screen')).toBeVisible()
})

test('an owner on a listing edits on the new form when the flag is on', async ({ page }) => {
  await page.setViewportSize(PHONE)
  const id = CARDS[0].id
  await open(page, `/app/home/listing/${id}`, {
    '/api/v1/public/meta/': { status: 200, body: { ...p4('meta_on'), miniapp_v2: false } },
    [`GET /api/v1/me/listings/${id}/`]: { status: 200, body: { is_owner: true, review_state: 'live', listing: listing(CARDS[0]) } },
  })
  await page.locator('[data-testid="owner-actions"]:visible').getByRole('link', { name: 'ویرایش آگهی' }).click()
  await expect(page).toHaveURL(new RegExp(`mine/${id}/edit`))
})
