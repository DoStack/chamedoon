import { expect, test, type Page } from '@playwright/test'
import { p4 } from '../src/mocks/posting'
import { coded, VIEWPORTS } from './api-mock'
import { check, NO_USERNAME, open, P, parseWith, pressBack, requests, seed, TEXT, WANTED_TEXT } from './post-helpers'

const BODY = 'POST /api/v1/me/listings/'
const READY = { text: WANTED_TEXT, wantedTypes: ['room', 'studio'] as ('room' | 'studio')[], price: '1600', areas: ['نورث یورک', 'ریچموند هیل'], household: 'one' as const, availableFrom: '2026-12-01' }
const next = (page: Page) => page.getByTestId('post-next')

for (const [size, viewport] of VIEWPORTS) {
  test.describe(`F19 ${size}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
    })

    test('#1 from the choice screen, filling every part of the form', async ({ page }) => {
      await open(page, P)
      await page.getByTestId('kind-wanted').click()
      await expect(page.getByTestId('wanted-form')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await page.locator('#wanted-budget').fill('۱٬۶۰۰')
      await page.getByTestId('wanted-area-add').click()
      await page.getByTestId('wanted-areas').getByRole('button', { name: 'نورث یورک' }).click()
      await expect(page.getByTestId('wanted-areas-row')).toContainText('نورث یورک')
      await page.getByRole('group', { name: 'نوع خانه' }).getByRole('button', { name: 'اتاق' }).click()
      await page.getByRole('group', { name: 'نوع خانه' }).getByRole('button', { name: 'استودیو' }).click()
      await page.getByRole('group', { name: 'چه کسی' }).getByRole('button', { name: 'یک نفر' }).click()
      await page.locator('#wanted-about').fill(WANTED_TEXT)
      await expect(next(page)).toBeEnabled()
      await expect(next(page)).toHaveText('ادامه: پیش‌نمایش')
      await check(page, 'F19-01', size)
      await next(page).click()
      await expect(page.getByTestId('wanted-preview')).toBeVisible()
    })

    test('#2 the preview holds no contact, and a post sends the types', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2, phone: '+1 416 555 0100' })
      await open(page, `${P}/wanted`)
      const post = page.getByTestId('channel-preview')
      await expect(post).toContainText('بودجه تا ۱٬۶۰۰')
      await expect(post).toContainText('اتاق یا استودیو')
      await expect(post).toContainText('نورث یورک، ریچموند هیل')
      expect(await page.locator('main').innerText()).not.toContain('416')
      expect(await page.locator('main').innerText()).not.toContain('@test')
      await expect(page.getByText('کارت در فهرست')).toHaveCount(0)
      await check(page, 'F19-02', size)
      const sent = page.waitForRequest((request) => request.url().includes('/me/listings/') && request.method() === 'POST')
      await page.getByTestId('post-publish').click()
      const request = await sent
      const body = request.postDataJSON()
      expect(body.kind).toBe('wanted')
      expect(body.wanted_types).toEqual(['room', 'studio'])
      expect(body.property_type).toBe('room')
      expect(body.price).toBe(1600)
      expect(body.photo_ids).toEqual([])
      expect(body.area).toBe('نورث یورک، ریچموند هیل')
      expect(request.headers()['idempotency-key']).toBeTruthy()
    })

    test('#3 held by review', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      await open(page, `${P}/wanted`, { [BODY]: { status: 201, body: p4('post_held') } })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-done')).toContainText('آگهی شما ثبت شد')
      await expect(page.getByTestId('held-badge')).toBeVisible()
      await expect(page.getByTestId('done-search')).toHaveText('جستجوی خانه')
      await check(page, 'F19-03', size)
    })

    test('#4 no budget', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, price: '' })
      await open(page, `${P}/wanted`)
      await page.locator('#wanted-budget').focus()
      await page.locator('#wanted-budget').blur()
      await expect(page.getByText('بودجه را بنویسید')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F19-04', size)
    })

    test('#5 no username: the phone is needed', async ({ page }) => {
      await seed(page, 'wanted', READY)
      await open(page, `${P}/wanted`, { 'POST /api/v1/auth/exchange/': NO_USERNAME })
      await expect(page.getByTestId('phone-share')).toBeVisible()
      await expect(page.getByText('یک راه تماس لازم است. شماره تلفن بگذارید.')).toBeVisible()
      await expect(next(page)).toBeDisabled()
      await check(page, 'F19-05', size)
      await page.getByTestId('phone-input').fill('+1 416 555 0100')
      await expect(next(page)).toBeEnabled()
    })

    test('#6 a short description', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, text: '' })
      await open(page, `${P}/wanted`)
      await page.locator('#wanted-about').fill('دانشجو هستم')
      await page.locator('#wanted-about').blur()
      await expect(page.getByTestId('wanted-about')).toContainText('توضیحات کوتاه است. حداقل ۲۰ حرف بنویسید.')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F19-06', size)
    })

    test('#7 a number in the description, and the way to take it out', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, text: '' })
      await open(page, `${P}/wanted`)
      await page.locator('#wanted-about').fill(`${WANTED_TEXT} 0176 1234567`)
      await expect(page.getByTestId('wanted-about')).toContainText('شماره یا آیدی را در متن ننویسید.')
      await expect(next(page)).toBeDisabled()
      await check(page, 'F19-07', size)
      await page.getByTestId('strip-contact').click()
      await expect(next(page)).toBeEnabled()
    })

    test('#7 the server finds one the phone did not', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      await open(page, `${P}/wanted`, { [BODY]: coded(400, 'validation_failed', { fields: { description: ['contact_in_text'] } }) })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('wanted-about')).toContainText('شماره یا آیدی را در متن ننویسید.')
    })

    test('#8 publishing holds both buttons', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      await open(page, `${P}/wanted`, { [BODY]: 'hang' })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-publish')).toBeDisabled()
      await expect(page.getByTestId('post-edit')).toBeDisabled()
      await check(page, 'F19-08', size)
    })

    test('#9 a failure keeps the text, and a retry sends the same key', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      const sent = requests(page, 'POST', '/me/listings/')
      await open(page, `${P}/wanted`, { [BODY]: coded(500, 'server_error') })
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-failed')).toHaveText('آگهی ثبت نشد. متن شما ذخیره است.')
      await check(page, 'F19-09', size)
      await page.getByTestId('post-publish').click()
      await expect.poll(() => sent.length).toBe(2)
      expect(sent[1].headers()['idempotency-key']).toBe(sent[0].headers()['idempotency-key'])
      await page.getByTestId('post-edit').click()
      await expect(page.locator('#wanted-about')).toHaveValue(WANTED_TEXT)
    })

    test('#10 published at once, and the draft is gone', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      await open(page, `${P}/wanted`)
      await page.getByTestId('post-publish').click()
      await expect(page.getByTestId('post-done')).toContainText('آگهی شما منتشر شد')
      await expect(page.getByTestId('held-badge')).toHaveCount(0)
      await check(page, 'F19-10', size)
      expect(await page.evaluate(() => localStorage.getItem('chamedoon.post.wanted'))).toBeNull()
      await page.getByTestId('done-search').click()
      await expect(page.getByTestId('main-nav')).toBeVisible()
    })

    test('#11 the draft comes back', async ({ page }) => {
      await seed(page, 'wanted', READY)
      await open(page, `${P}/wanted`)
      await expect(page.getByTestId('toast')).toContainText('پیش‌نویس شما برگشت.')
      await expect(page.locator('#wanted-about')).toHaveValue(WANTED_TEXT)
      await check(page, 'F19-11', size)
    })

    test('F18#26 sends the text here', async ({ page }) => {
      await open(page, `${P}/offer`, parseWith('parse_wanted'))
      await page.locator('#post-text').fill(TEXT)
      await page.getByTestId('post-next').click()
      await page.getByTestId('to-wanted').click()
      await expect(page).toHaveURL(/post\/wanted/)
      await expect(page.locator('#wanted-about')).toHaveValue(TEXT)
    })

    test('Back from the preview goes to the form, then asks before leaving', async ({ page }) => {
      await seed(page, 'wanted', { ...READY, step: 2 })
      await open(page, `${P}/wanted`)
      await pressBack(page)
      await expect(page.getByTestId('wanted-form')).toBeVisible()
      await pressBack(page)
      await expect(page.getByTestId('save-draft')).toBeVisible()
      await page.getByTestId('draft-save').click()
      await expect(page.getByTestId('post-choose')).toBeVisible()
    })
  })
}
