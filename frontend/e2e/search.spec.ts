import { expect, test, type Page } from '@playwright/test'
import PARSE from '../../backend/aisearch/testdata/parse_fixtures.json' with { type: 'json' }
import { META } from '../src/mocks/fixtures'
import { APP, coded, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'
import type { Overrides } from '../src/mocks/api'

const PARSE_URL = 'POST /api/v1/me/search/parse/'
const ON = { '/api/v1/public/meta/': { status: 200, body: { ...META, text_search_on: true } } }
const toast = (page: Page) => page.getByTestId('toast')
const cards = (page: Page) => page.getByTestId('listing-card')
const chips = (page: Page) => page.getByTestId('search-chip')

async function open(page: Page, path: string, api: Overrides = {}) {
  await mockApi(page, { ...ON, ...api })
  await page.goto(APP(`/app/home/${path}`))
}

async function results(page: Page, name: keyof typeof PARSE) {
  const row = PARSE[name]
  await open(page, `search?${new URLSearchParams({ text: row.request.text })}`, { [PARSE_URL]: { status: 200, body: row.response } })
  await expect(page.getByTestId('search-results')).toBeVisible()
}

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('F08 opens from the search box of the list', async ({ page }) => {
      await open(page, '')
      await page.getByTestId('list-search').click()
      await expect(page.getByTestId('search-compose')).toBeVisible()
    })

    test('F08#1: suggestions, recents and the filter entry, and it rotates its example', async ({ page }) => {
      await open(page, 'search')
      await expect(page.getByTestId('search-suggestions')).toContainText('اتاق مبله')
      await expect(page.getByTestId('search-recents')).toContainText('۱ خوابه')
      await expect(page.getByTestId('search-with-filter')).toBeVisible()
      const box = page.getByRole('textbox', { name: 'جستجو' })
      await expect(box).toHaveAttribute('placeholder', 'مثلاً: ۱ خوابه نزدیک مترو')
      await expect(box).toHaveAttribute('placeholder', 'مثلاً: اتاق مبله تا ۱٬۰۰۰', { timeout: 5_000 })
      await shot(page, 'F08-01', size)
    })

    test('F08#2: live suggestions while typing, from the rules only', async ({ page }) => {
      const bodies: Array<{ rules_only: boolean }> = []
      await mockApi(page, { ...ON, '/api/v1/public/areas/search/': { status: 200, body: { here: [{ id: 9001, name_fa: 'نورث یورک', matched: 'finch', country: 'CA', city: 'Toronto', city_slug: 'toronto' }], elsewhere: [] } } })
      await page.route('**/api/v1/me/search/parse/', async (route) => {
        bodies.push(route.request().postDataJSON())
        await route.fallback()
      })
      await page.goto(APP('/app/home/search'))
      await page.getByRole('textbox', { name: 'جستجو' }).fill(PARSE.parsed.request.text)
      await expect(page.getByTestId('search-live-echo')).toContainText('فینچ')
      await expect(page.getByTestId('search-live-areas')).toContainText('نورث یورک')
      await expect(page.getByTestId('search-live-counts')).toContainText('آگهی')
      await expect(page.getByTestId('search-live-listings')).toBeVisible()
      expect(bodies.length).toBeGreaterThan(0)
      expect(bodies.every((body) => body.rules_only)).toBe(true)
      await shot(page, 'F08-02', size)
    })

    test('F08#3: while reading the text the filter row stays', async ({ page }) => {
      await open(page, `search?${new URLSearchParams({ text: 'هر چیزی' })}`, { [PARSE_URL]: 'hang' })
      await expect(page.getByTestId('search-parsing')).toBeVisible()
      await expect(page.getByTestId('filter-button')).toBeVisible()
      await shot(page, 'F08-03', size)
    })

    test('F08#4: results with what was understood as chips', async ({ page }) => {
      await results(page, 'parsed')
      await expect(page.getByTestId('search-said')).toContainText('فهمیدم:')
      await expect(chips(page)).toHaveCount(4)
      await expect(page.getByTestId('search-said')).toContainText('فینچ')
      await expect(page.getByTestId('search-said')).toContainText('۵۵ متر')
      await expect(cards(page)).toHaveCount(2)
      await expect(page.getByTestId('count-bar')).toContainText('۲ آگهی')
      await shot(page, 'F08-04', size)
    })

    test('F08#5: a chip goes alone and comes back', async ({ page }) => {
      await results(page, 'parsed')
      await chips(page).filter({ hasText: 'فینچ' }).click()
      await expect(chips(page)).toHaveCount(3)
      await expect(toast(page)).toContainText('فینچ حذف شد')
      await shot(page, 'F08-05', size)
      await toast(page).getByRole('button', { name: 'برگردان' }).click()
      await expect(chips(page)).toHaveCount(4)
    })

    test('F08#6: area not found, with near names', async ({ page }) => {
      await results(page, 'unknown_area')
      const offer = page.getByTestId('search-unknown-area')
      await expect(offer).toContainText('«پیرسون» را پیدا نکردم')
      await expect(offer.getByRole('button')).toHaveCount(4)
      await shot(page, 'F08-06', size)
      await offer.getByRole('button', { name: 'بدون محله' }).click()
      await expect(offer).toHaveCount(0)
    })

    test('F08#6: choosing a near name adds its chip', async ({ page }) => {
      await results(page, 'unknown_area')
      await expect(chips(page).first()).toBeVisible()
      const before = await chips(page).count()
      await page.getByTestId('search-unknown-area').getByRole('button').first().click()
      await expect(chips(page)).toHaveCount(before + 1)
    })

    test('F08#8: a simple search says what was not counted', async ({ page }) => {
      await results(page, 'simple')
      await expect(page.getByTestId('search-said')).toContainText('جستجوی ساده:')
      await expect(page.getByTestId('search-simple')).toContainText('«چیز عجیب» در این جستجو حساب نشد.')
      await shot(page, 'F08-08', size)
      await page.getByTestId('search-simple').getByRole('button', { name: 'افزودن با فیلتر' }).click()
      await expect(page).toHaveURL(/types=room/)
      await expect(page.getByTestId('list-search')).toBeVisible()
    })

    test('F08#9: the daily cap offers the filters', async ({ page }) => {
      await results(page, 'capped')
      await expect(page.getByTestId('search-capped')).toContainText('فردا دوباره فعال می‌شود')
      await expect(page.getByTestId('search-said')).toHaveCount(0)
      await expect(page.getByTestId('filter-row')).toContainText('بودجه')
      await expect(cards(page).first()).toBeVisible()
      await shot(page, 'F08-09', size)
    })

    test('F08#10: no result, with ways to widen by real count', async ({ page }) => {
      await results(page, 'nothing')
      await expect(page.getByTestId('search-empty')).toContainText('آگهی‌ای با این مشخصات نیست')
      const ways = page.getByTestId('ways-out').getByRole('button')
      await expect(ways.first()).toBeVisible()
      await shot(page, 'F08-10', size)
      await page.getByTestId('ways-out').getByRole('button', { name: /^(بدون محله|تا|همه‌ی نوع‌ها)/ }).first().click()
      await expect(page.getByTestId('search-empty')).toHaveCount(0)
    })

    test('F08#11: recents cleared, and the undo brings them back', async ({ page }) => {
      await open(page, 'search')
      await page.getByTestId('search-recents').getByRole('button', { name: 'پاک کردن' }).click()
      await expect(page.getByTestId('search-recents')).toHaveCount(0)
      await expect(toast(page)).toContainText('جستجوهای اخیر پاک شد')
      await shot(page, 'F08-11', size)
      await toast(page).getByRole('button', { name: 'برگردان' }).click()
      await expect(page.getByTestId('search-recents')).toBeVisible()
    })

    test('F08#11: a failed clear brings the list back', async ({ page }) => {
      await open(page, 'search', { 'DELETE /api/v1/me/search/recent/': coded(500, 'server_error') })
      await page.getByTestId('search-recents').getByRole('button', { name: 'پاک کردن' }).click()
      await expect(page.getByTestId('search-recents')).toBeVisible()
    })

    test('F08#12: text not understood offers the filters', async ({ page }) => {
      await results(page, 'not_understood')
      await expect(page.getByTestId('search-not-understood')).toContainText('متن را نفهمیدم')
      await expect(page.getByTestId('search-said')).toHaveCount(0)
      await expect(cards(page).first()).toBeVisible()
      await shot(page, 'F08-12', size)
    })

    test('F08#13: another city in the text is asked about', async ({ page }) => {
      await results(page, 'other_city')
      const note = page.getByTestId('search-other-city')
      await expect(note).toContainText('مونترال را می‌گویید؟')
      await expect(chips(page)).toHaveCount(1)
      await shot(page, 'F08-13', size)
      await note.getByRole('button', { name: 'تغییر شهر' }).click()
      await expect(page.getByTestId('search-city')).toContainText('مونترال')
    })

    test('a failed parse falls to plain text search, with a retry and no raw error', async ({ page }) => {
      await open(page, `search?${new URLSearchParams({ text: 'نورث' })}`, { [PARSE_URL]: coded(500, 'server_error') })
      await expect(page.getByTestId('search-failed')).toContainText('جستجوی متنی الان در دسترس نیست')
      await expect(page.getByTestId('search-failed').getByRole('button', { name: 'تلاش مجدد' })).toBeEnabled()
      await expect(page.locator('body')).not.toContainText('server_error')
    })

    test('with the flag off the route goes to the list and the box filters as before', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/home/search?text=x'))
      const box = page.getByTestId('list-search')
      await expect(box).toBeVisible()
      await box.fill('نورث')
      await expect(page).toHaveURL(/q=/)
    })
  })
}

test.describe('no city', () => {
  test('F08#7: asks which city, then reads the text for it', async ({ page }) => {
    await page.setViewportSize(VIEWPORTS[0][1])
    const row = PARSE.no_city
    await open(page, `search?${new URLSearchParams({ text: row.request.text })}`, { [PARSE_URL]: { status: 200, body: row.response } })
    const sheet = page.getByTestId('search-city-sheet')
    await expect(sheet).toContainText('کدام شهر؟')
    await expect(page.getByTestId('search-city')).toContainText('انتخاب شهر')
    await expect(page.getByTestId('filter-button')).toBeVisible()
    await shot(page, 'F08-07', 'phone')
    await sheet.getByRole('button', { name: 'تورنتو' }).click()
    await expect(page.getByTestId('search-city')).toContainText('تورنتو')
  })
})
