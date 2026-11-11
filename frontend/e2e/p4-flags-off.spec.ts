import { expect, test } from '@playwright/test'
import { META, TORONTO as CARDS, listing } from '../src/mocks/fixtures'
import { p4 } from '../src/mocks/posting'
import { APP, mockApi, savedCity, TORONTO, VIEWPORTS } from './api-mock'

// Every other spec already runs on a meta with no `features`, so the whole suite holds the old behaviour as well.
const OFF = {
  'features missing': META,
  'posting false': p4('meta_off'),
}
const P4_CALLS = /\/api\/v1\/(me\/listings\/?(\?|$)|me\/photos|me\/posts\/parse|public\/areas)|\/me\/listings\/\d+\/(form|status)/
const P4_CHUNKS = /screens\/post\/|\/(choose|offer|wanted|mine|parts|preview|use-publish|post-photos)-[\w-]+\.js/

for (const [flag, meta] of Object.entries(OFF)) {
  for (const [size, viewport] of VIEWPORTS) {
    test.describe(`${flag}, ${size}`, () => {
      test.beforeEach(async ({ page }) => {
        await page.setViewportSize(viewport)
        await savedCity(page, TORONTO)
        await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: meta } })
      })

      test('the tabs are the same four, and post and account still go to the template pages', async ({ page }) => {
        const asked: string[] = []
        page.on('request', (request) => asked.push(request.url()))
        await page.goto(APP())
        const nav = page.getByTestId('main-nav')
        await expect(nav.getByRole('tab')).toHaveText(['جستجو', 'نشان‌شده‌ها', 'ثبت آگهی', 'حساب کاربری'])
        await nav.getByRole('tab', { name: 'ثبت آگهی' }).click()
        await expect(page).toHaveURL(/\/app\/home\/new\//)
        await page.goBack()
        await page.getByTestId('main-nav').getByRole('tab', { name: 'حساب کاربری' }).click()
        await expect(page).toHaveURL(/\/app\/home\/mine\//)
        expect(asked.filter((url) => P4_CALLS.test(url))).toEqual([])
        expect(asked.filter((url) => P4_CHUNKS.test(url))).toEqual([])
      })

      for (const path of ['post', 'post/offer', 'post/wanted', 'mine', 'mine/50', 'mine/50/edit']) {
        test(`/${path} acts like any unknown path, and fetches nothing of P4`, async ({ page }) => {
          const asked: string[] = []
          page.on('request', (request) => asked.push(request.url()))
          await page.goto(APP(`/app/home/${path}`))
          await expect(page.getByTestId('main-nav')).toBeVisible()
          await expect(page).toHaveURL(/\/app\/home\/?(\?|$)/)
          await expect(page.locator('[data-testid="listing-card"]').first()).toBeVisible()
          expect(asked.filter((url) => P4_CALLS.test(url))).toEqual([])
          expect(asked.filter((url) => P4_CHUNKS.test(url))).toEqual([])
        })
      }

      test('an owner on a listing still edits on the template page', async ({ page }) => {
        const id = CARDS[0].id
        await mockApi(page, {
          '/api/v1/public/meta/': { status: 200, body: { ...meta, miniapp_v2: false } },
          [`GET /api/v1/me/listings/${id}/`]: { status: 200, body: { is_owner: true, review_state: 'live', listing: listing(CARDS[0]) } },
        })
        await page.goto(APP(`/app/home/listing/${id}`))
        const edit = page.locator('[data-testid="owner-actions"]:visible').getByRole('link', { name: 'ویرایش آگهی' })
        await expect(edit).toHaveAttribute('href', /\/app\/home\/mine\//)
      })
    })
  }
}

test('smart_posting alone opens nothing', async ({ page }) => {
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': { status: 200, body: { ...META, features: { alerts: false, account: false, posting: false, smart_posting: true } } } })
  await page.goto(APP('/app/home/post/offer'))
  await expect(page).toHaveURL(/\/app\/home\/?(\?|$)/)
})
