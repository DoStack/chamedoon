import { expect, test, type Page } from '@playwright/test'
import { snapshot, TORONTO as CARDS } from '../src/mocks/fixtures'
import { APP, mockApi, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const cards = (page: Page) => page.getByTestId('listing-card')
const sheet = (page: Page) => page.getByTestId('filter-sheet')
const openSheet = async (page: Page, url = '/app/home/') => {
  await mockApi(page)
  await page.goto(APP(url))
  await page.getByTestId('filter-button').click()
  await expect(sheet(page)).toBeVisible()
}
const showButton = (page: Page) => sheet(page).getByRole('button', { name: /^نمایش/ })

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
    })

    test('F05#1: sheet opens with every section and the total', async ({ page }) => {
      await openSheet(page)
      await expect(showButton(page)).toContainText('نمایش ۱۰ آگهی')
      await expect(page.getByTestId('type-tiles').getByRole('button')).toHaveCount(9)
      await expect(sheet(page)).toContainText('بودجه‌ی ماهانه')
      await expect(page.getByTestId('histogram')).toBeVisible()
      await expect(page.getByRole('switch', { name: 'آگهی‌های بدون قیمت هم بیاید' })).toHaveCount(0)
      await expect(page.getByTestId('area-chips')).toContainText('همه‌جا')
      await shot(page, 'F05-01', size)
    })

    test('F05#2 to #4: choices update the count, and each section counts from the others', async ({ page }) => {
      await openSheet(page)
      await page.getByTestId('type-tiles').getByRole('button', { name: '۱ خوابه' }).click()
      await expect(showButton(page)).toContainText('نمایش ۲ آگهی')
      // Its own section keeps its counts.
      await expect(page.getByTestId('type-tiles').getByRole('button', { name: 'اتاق', exact: true })).toContainText('۳')
      await shot(page, 'F05-02', size)
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('1900')
      await expect(showButton(page)).toContainText('نمایش ۱ آگهی')
      await shot(page, 'F05-03', size)
      await page.getByTestId('area-chips').getByRole('button', { name: /نورث یورک/ }).click()
      await expect(showButton(page)).toContainText('نمایش ۱ آگهی')
      await shot(page, 'F05-04', size)
    })

    test('a count of zero disables the option unless selected', async ({ page }) => {
      await openSheet(page)
      await expect(page.getByTestId('type-tiles').getByRole('button', { name: 'خانه‌ی کامل' })).toBeDisabled()
    })

    test('F05#6, #8: applying shows chips, and clearing can be undone', async ({ page }) => {
      await openSheet(page)
      await page.getByTestId('type-tiles').getByRole('button', { name: '۱ خوابه' }).click()
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('2000')
      await showButton(page).click()
      await expect(sheet(page)).toBeHidden()
      await expect(page.getByTestId('filter-chip')).toHaveCount(2)
      await expect(page.getByTestId('filter-button')).toContainText('۲')
      await expect(cards(page)).toHaveCount(2)
      expect(page.url()).toContain('types=bed1')
      await shot(page, 'F05-06', size)
      await page.getByTestId('filter-row').getByRole('button', { name: 'پاک کردن همه' }).click()
      await expect(page.getByTestId('toast')).toContainText('همه‌ی فیلترها پاک شد')
      await shot(page, 'F05-08', size)
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
      await page.getByTestId('toast').getByRole('button', { name: 'برگردان' }).click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(2)
    })

    test('F05#5: no result disables the button and offers ways out by real count', async ({ page }) => {
      await openSheet(page, '/app/home/?types=bed2&max=1000')
      await expect(showButton(page)).toBeDisabled()
      await expect(showButton(page)).toContainText('نمایش ۰ آگهی')
      const ways = sheet(page).getByTestId('ways-out')
      await expect(ways).toContainText('تا ۲٬۲۵۰: ۱ آگهی')
      await shot(page, 'F05-05', size)
      await ways.getByRole('button', { name: /^تا/ }).click()
      await expect(showButton(page)).toBeEnabled()
    })

    test('F05#7: empty list keeps the ways out and the clear button', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/home/?types=bed2&max=1000'))
      await expect(page.getByTestId('filter-empty')).toContainText('آگهی‌ای با این فیلترها نیست')
      await expect(page.getByTestId('filter-empty').getByTestId('ways-out')).toBeVisible()
      await expect(page.getByTestId('count-bar')).toContainText('۰ آگهی')
      await shot(page, 'F05-07', size)
    })

    test('F05#9, F06#3: min above max errors and blocks showing', async ({ page }) => {
      await openSheet(page)
      await sheet(page).getByRole('textbox', { name: 'از', exact: true }).fill('2500')
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('1800')
      await expect(sheet(page)).toContainText('حداقل از حداکثر بیشتر است')
      await expect(showButton(page)).toBeDisabled()
      await expect(showButton(page)).toHaveText('نمایش آگهی‌ها')
      await shot(page, 'F05-09', size)
    })

    test('Persian digits work in the budget', async ({ page }) => {
      await openSheet(page)
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('۱۰۰۰')
      await expect(sheet(page)).toContainText('تا ۱٬۰۰۰')
    })

    test('F05#10, F06#5: Berlin has its own options', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' })))
      await openSheet(page)
      await expect(page.getByTestId('type-tiles')).toContainText('اتاق WG')
      await expect(page.getByTestId('type-tiles')).toContainText('۲ اتاقه')
      await expect(page.getByTestId('type-tiles')).not.toContainText('بیسمنت')
      await expect(sheet(page)).toContainText('اجاره‌ی سرد نباشد')
      await shot(page, 'F05-10', size)
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('800')
      await expect(sheet(page)).toContainText('€')
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('')
      await expect(page.getByTestId('mode-chips')).toContainText('موقت (Zwischenmiete)')
      await expect(page.getByTestId('mode-chips')).toContainText('Nachmieter')
      await expect(sheet(page)).toContainText('ملد (Anmeldung) ممکن باشد')
      await expect(sheet(page)).not.toContainText('قبوض داخل قیمت')
      await page.getByRole('switch', { name: 'اجاره‌ی سرد نباشد' }).click()
      await expect(showButton(page)).toContainText('نمایش ۲ آگهی')
      await page.getByText('Nachmieter').scrollIntoViewIfNeeded()
      await shot(page, 'F06-05', size)
    })

    test('a German city shows «ملد» in the row', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' })))
      await mockApi(page)
      await page.goto(APP())
      await expect(page.getByTestId('filter-row').getByRole('button', { name: 'ملد' })).toBeVisible()
      await expect(cards(page).first()).toBeVisible()
    })

    test('F05#11: Quebec has one apartment tile, which takes null rooms', async ({ page }) => {
      const quebec = snapshot('montreal')
      await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'CA', city: 'Montreal', slug: 'montreal', name_fa: 'مونترال' })))
      await mockApi(page, { '/feeds/montreal.v1.json': { status: 200, body: { ...quebec, listings: quebec.listings.map((card, i) => (i ? card : { ...card, rooms: null })) } } })
      await page.goto(APP())
      await page.getByTestId('filter-button').click()
      await expect(page.getByTestId('type-tiles').getByRole('button', { name: 'آپارتمان' })).toContainText('۱')
      await expect(page.getByTestId('type-tiles')).not.toContainText('۱ خوابه')
      await shot(page, 'F05-11', size)
    })

    test('F05#12, F04#14: «دنبال خانه» lists wanted posts', async ({ page }) => {
      await openSheet(page)
      await page.getByTestId('type-tiles').getByRole('button', { name: 'دنبال خانه' }).click()
      await expect(showButton(page)).toContainText('نمایش ۲ آگهی')
      await expect(sheet(page)).not.toContainText('نوع اجاره')
      await expect(sheet(page)).not.toContainText('بودجه‌ی ماهانه')
      await shot(page, 'F05-12', size)
      await showButton(page).click()
      await expect(page.getByTestId('wanted-card')).toHaveCount(2)
    })

    test('row chips open the sheet at their section', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP())
      await page.getByTestId('filter-row').getByRole('button', { name: 'نوع اجاره' }).click()
      await expect(sheet(page).getByText('نوع اجاره', { exact: true })).toBeInViewport()
    })

    test('F06#1, #2: lower sections filter with live counts', async ({ page }) => {
      await openSheet(page)
      await shot(page, 'F06-01', size)
      await page.getByRole('switch', { name: 'فقط مالک' }).click()
      await expect(showButton(page)).toContainText('نمایش ۹ آگهی')
      await page.getByTestId('mode-chips').getByRole('button', { name: /سابلت/ }).click()
      await expect(showButton(page)).toContainText('نمایش ۱ آگهی')
      await page.getByTestId('mode-chips').getByRole('button', { name: /سابلت/ }).click()
      await expect(page.getByRole('switch', { name: 'مبله' })).toBeVisible()
      await page.getByRole('textbox', { name: 'تاریخ ورود' }).fill('2026-12-01')
      await expect(page.getByTestId('window-chips')).toContainText('± ۷ روز')
      await page.getByTestId('gender-chips').scrollIntoViewIfNeeded()
      await shot(page, 'F06-02', size)
      await showButton(page).click()
      await expect(page.getByTestId('filter-row')).toContainText('فقط مالک')
      await expect(page.getByTestId('filter-row')).toContainText('از ۱ دسامبر')
    })

    test('F06#3, #4: the same error and no-result states with lower filters on', async ({ page }) => {
      await openSheet(page, '/app/home/?gender=female&move=2030-01-01')
      await expect(showButton(page)).toContainText('نمایش ۰ آگهی')
      await expect(showButton(page)).toBeDisabled()
      await shot(page, 'F06-04', size)
      await sheet(page).getByRole('textbox', { name: 'از', exact: true }).fill('2500')
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('1800')
      await shot(page, 'F06-03', size)
      await expect(sheet(page)).toContainText('حداقل از حداکثر بیشتر است')
    })

    test('the zero rule holds for switches, area search, checkboxes and the area footer', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' })))
      await openSheet(page)
      // No Berlin listing states a registration.
      await expect(page.getByRole('switch', { name: 'ملد (Anmeldung) ممکن باشد' })).toBeDisabled()
      await sheet(page).getByRole('button', { name: 'بستن' }).click()
      await expect(sheet(page)).toBeHidden()
      await page.goto(APP('/app/home/?term=short'))
      await page.getByTestId('filter-chip').waitFor()
      await page.getByTestId('filter-row').getByRole('button', { name: 'فیلتر' }).click()
      await expect(sheet(page).getByTestId('area-chips').getByRole('button', { name: /نویکلن/ })).toBeDisabled()
    })

    test('area zero rule: search hits, checkboxes and footer', async ({ page }) => {
      const cards_ = [{ ...CARDS[0], id: 960, area: 'الف · A', furnished: 'no' as const }, { ...CARDS[0], id: 961, area: 'ب · B', furnished: 'yes' as const }, ...Array.from({ length: 9 }, (_, i) => ({ ...CARDS[0], id: 970 + i, area: `محله ${i + 1}`, furnished: 'yes' as const }))]
      await mockApi(page, { '/feeds/toronto.v1.json': { status: 200, body: { ...snapshot('toronto'), listings: cards_ } } })
      await page.goto(APP('/app/home/?furnished=1&areas=الف'))
      await expect(page.getByTestId('filter-empty')).toBeVisible()
      await page.getByTestId('filter-button').click()
      await page.getByTestId('area-chips').getByRole('button', { name: 'بیشتر' }).click()
      const area = page.getByTestId('area-sheet')
      await expect(area.getByRole('button', { name: /^نمایش/ })).toBeDisabled()
      await area.getByRole('textbox').fill('الف')
      await expect(page.getByTestId('area-hits').getByRole('button').first()).toBeEnabled() // Selected, so it stays open.
      await area.getByRole('textbox').fill('')
      await page.getByTestId('top-areas').getByRole('button', { name: /الف/ }).click()
      await area.getByRole('button', { name: 'پاک کردن همه' }).waitFor()
      await expect(page.getByTestId('top-areas').getByRole('button', { name: /^الف/ })).toBeDisabled()
      await area.getByRole('textbox').fill('الف')
      await expect(page.getByTestId('area-hits').getByRole('button').first()).toBeDisabled()
      await area.getByRole('textbox').fill('')
      await page.getByTestId('top-areas').getByRole('button', { name: 'بیشتر' }).click()
      await expect(page.getByRole('checkbox', { name: 'الف' })).toBeDisabled()
    })

    test('the warm count ignores this section\'s own budget', async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('chamedoon.city', JSON.stringify({ country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' })))
      await openSheet(page)
      const warm = sheet(page).locator('label', { hasText: 'اجاره‌ی سرد نباشد' })
      await expect(warm).toContainText('۲')
      await sheet(page).getByRole('textbox', { name: 'تا', exact: true }).fill('100')
      await expect(warm).toContainText('۲')
      await expect(page.getByRole('switch', { name: 'اجاره‌ی سرد نباشد' })).toBeEnabled()
    })

    test('clearing ends a date sort, on the URL too', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/home/?move=2026-12-01&window=30&sort=date'))
      await page.getByTestId('filter-row').getByRole('button', { name: 'پاک کردن همه' }).click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
      expect(page.url()).not.toContain('sort=')
      await expect(page.getByTestId('count-bar')).toContainText('جدیدترین')
    })

    test.describe('fallback feed', () => {
      const NO_SNAPSHOT = { '/feeds/manifest.json': { status: 500, body: { code: 'server_error', detail: 'x' } } }
      const pages = async (page: Page, cursorFails = false) => {
        await page.route('**/api/v1/public/feed/**', (route) => {
          const cursor = new URL(route.request().url()).searchParams.get('cursor')
          if (!cursor) return route.fulfill({ json: { generated_at: new Date().toISOString(), results: CARDS.filter((card) => card.property_type !== 'studio'), next_cursor: 'p2' } })
          if (cursorFails) return route.fulfill({ status: 500, json: { code: 'server_error', detail: 'x' } })
          return route.fulfill({ json: { generated_at: new Date().toISOString(), results: CARDS.filter((card) => card.property_type === 'studio'), next_cursor: null } })
        })
      }

      test('empty waits for the last page, and the match on it shows', async ({ page }) => {
        await mockApi(page, NO_SNAPSHOT)
        await pages(page)
        await page.goto(APP('/app/home/?types=studio'))
        await expect(cards(page)).toHaveCount(1)
        await expect(page.getByTestId('filter-empty')).toHaveCount(0)
      })

      test('empty shows only after the last page', async ({ page }) => {
        await mockApi(page, NO_SNAPSHOT)
        await pages(page)
        await page.goto(APP('/app/home/?types=house'))
        await expect(page.getByTestId('filter-empty')).toBeVisible()
      })

      test('a failed page while looking offers retry, not empty', async ({ page }) => {
        await mockApi(page, NO_SNAPSHOT)
        await pages(page, true)
        await page.goto(APP('/app/home/?types=house'))
        await expect(page.getByTestId('more-failed')).toBeVisible()
        await expect(page.getByTestId('filter-empty')).toHaveCount(0)
      })

      test('counts are hidden while pages remain, and the total reads as a floor', async ({ page }) => {
        await mockApi(page, NO_SNAPSHOT)
        await page.route('**/api/v1/public/feed/**', (route) => {
          if (new URL(route.request().url()).searchParams.get('cursor')) return
          return route.fulfill({ json: { generated_at: new Date().toISOString(), results: CARDS, next_cursor: 'p2' } })
        })
        await page.goto(APP())
        await expect(page.getByTestId('count-bar')).toContainText('۶+ آگهی')
        await page.getByTestId('filter-button').click()
        await expect(showButton(page)).toHaveText('نمایش آگهی‌ها')
        await expect(showButton(page)).toBeEnabled()
        await expect(page.getByTestId('type-tiles').getByRole('button', { name: 'خانه‌ی کامل' })).toBeEnabled()
        await expect(page.getByTestId('histogram')).toHaveCount(0)
      })
    })

    test('date sort turns on once a date is set', async ({ page }) => {
      await mockApi(page)
      await page.goto(APP('/app/home/?move=2026-12-01&window=30'))
      await page.getByTestId('count-bar').getByRole('button', { name: 'جدیدترین' }).click()
      await expect(page.getByTestId('sort-sheet').getByRole('radio', { name: 'نزدیک‌ترین به تاریخ ورود' })).toBeEnabled()
    })

    test('closing the sheet drops the draft', async ({ page }) => {
      await openSheet(page)
      await page.getByTestId('type-tiles').getByRole('button', { name: '۱ خوابه' }).click()
      await sheet(page).getByRole('button', { name: 'بستن' }).click()
      await expect(sheet(page)).toBeHidden()
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
      await page.getByTestId('filter-button').click()
      await expect(showButton(page)).toContainText('نمایش ۱۰ آگهی')
    })

    test('Back closes the sheet, not the list', async ({ page }) => {
      await openSheet(page)
      await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
      await expect(sheet(page)).toBeHidden()
      await expect(page.getByTestId('count-bar')).toBeVisible()
    })

    test('44 px on phone and 36 px on desktop for the row', async ({ page }) => {
      await openSheet(page)
      await sheet(page).getByRole('button', { name: 'بستن' }).click()
      await expect(page.getByTestId('filter-button')).toHaveCSS('height', size === 'phone' ? '44px' : '36px')
    })

    test.describe('F07', () => {
      const many = Array.from({ length: 12 }, (_, i) => ({ ...CARDS[0], id: 900 + i, area: `محله ${i + 1} · خیابان ${i}` }))
      const manyAreas = { '/feeds/toronto.v1.json': { status: 200, body: { ...snapshot('toronto'), listings: [...many, { ...CARDS[0], id: 950, area: 'نورث یورک · Yonge و Sheppard' }, { ...CARDS[0], id: 951, area: 'نورث یورک · Bayview و Sheppard' }] } } }
      const area = (page: Page) => page.getByTestId('area-sheet')
      const openAreas = async (page: Page, api = {}) => {
        await mockApi(page, api)
        await page.goto(APP())
        await page.getByTestId('filter-row').getByRole('button', { name: 'محله' }).click()
        await expect(area(page)).toBeVisible()
      }

      test('F07#1, #6: top areas, several at once, and «همه‌جا» clears', async ({ page }) => {
        await openAreas(page)
        await shot(page, 'F07-01', size)
        await page.getByTestId('top-areas').getByRole('button', { name: /وان/ }).click()
        await page.getByTestId('top-areas').getByRole('button', { name: /نورث یورک/ }).click()
        await expect(area(page).getByRole('button', { name: /^نمایش/ })).toContainText('نمایش ۵ آگهی')
        await shot(page, 'F07-06', size)
        await page.getByTestId('top-areas').getByRole('button', { name: 'همه‌جا' }).click()
        await expect(area(page).getByRole('button', { name: /^نمایش/ })).toContainText('نمایش ۱۰ آگهی')
        await page.getByTestId('top-areas').getByRole('button', { name: /وان/ }).click()
        await area(page).getByRole('button', { name: /^نمایش/ }).click()
        await expect(cards(page)).toHaveCount(2)
        await expect(page.getByTestId('filter-chip')).toContainText('وان')
      })

      test('F07#2: «بیشتر» lists every area, and its back returns', async ({ page }) => {
        await openAreas(page, manyAreas)
        await page.getByTestId('top-areas').getByRole('button', { name: 'بیشتر' }).click()
        await expect(page.getByTestId('all-areas').getByRole('checkbox')).toHaveCount(13)
        await shot(page, 'F07-02', size)
        await area(page).getByRole('button', { name: 'برگشت' }).click()
        await expect(page.getByTestId('top-areas')).toBeVisible()
      })

      test('F07#3: search finds a neighbourhood by its street', async ({ page }) => {
        await openAreas(page, manyAreas)
        await area(page).getByRole('textbox').fill('Sheppard')
        await expect(page.getByTestId('area-hits')).toContainText('نورث یورک')
        await expect(page.getByTestId('area-hits')).toContainText('Yonge و Sheppard')
        await shot(page, 'F07-03', size)
        await page.getByTestId('area-hits').getByRole('button').first().click()
        await expect(page.getByTestId('filter-chip')).toContainText('نورث یورک')
      })

      test('F07#4: a city with that name offers to change city', async ({ page }) => {
        await openAreas(page)
        await area(page).getByRole('textbox').fill('مونترال')
        await expect(page.getByTestId('area-other-cities')).toContainText('تغییر شهر')
        await shot(page, 'F07-04', size)
        await page.getByTestId('area-other-cities').getByRole('button').click()
        await expect(page.getByTestId('city-button')).toContainText('مونترال')
      })

      test('F07#5: no hit searches the same text', async ({ page }) => {
        await openAreas(page)
        await area(page).getByRole('textbox').fill('لیک‌ساید')
        await expect(page.getByTestId('area-none')).toContainText('محله‌ای با این نام پیدا نشد')
        await shot(page, 'F07-05', size)
        await page.getByRole('button', { name: 'همین را جستجو کنید' }).click()
        expect(decodeURIComponent(page.url())).toContain('q=لیک')
      })

      test('F07#7: a seventh area is refused', async ({ page }) => {
        await openAreas(page, manyAreas)
        const chips = page.getByTestId('top-areas')
        for (const name of [/نورث یورک/, /^محله 1\b/, /^محله 2\b/, /^محله 3\b/, /^محله 4\b/, /^محله 5\b/]) await chips.getByRole('button', { name }).click()
        await chips.getByRole('button', { name: /^محله 6\b/ }).click()
        await expect(page.getByTestId('toast')).toContainText('حداکثر ۶ محله')
        await expect(chips.locator('[data-state=on]')).toHaveCount(6)
        await shot(page, 'F07-07', size)
      })

      test('«بیشتر» in the filter sheet feeds its draft', async ({ page }) => {
        await mockApi(page, manyAreas)
        await page.goto(APP())
        await page.getByTestId('filter-button').click()
        await page.getByTestId('area-chips').getByRole('button', { name: 'بیشتر' }).click()
        await page.getByTestId('top-areas').getByRole('button', { name: /^محله 7\b/ }).click()
        await area(page).getByRole('button', { name: /^نمایش/ }).click()
        await expect(sheet(page)).toBeVisible()
        await expect(showButton(page)).toContainText('نمایش ۱ آگهی')
      })
    })
  })
}
