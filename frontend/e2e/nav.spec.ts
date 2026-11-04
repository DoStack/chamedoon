import { expect, test, type Page } from '@playwright/test'
import { TORONTO as CARDS } from '../src/mocks/fixtures'
import { APP, coded, DESKTOP, mockApi, p3Meta, PHONE, savedCity, shot, TORONTO, VIEWPORTS } from './api-mock'

const nav = (page: Page) => page.getByTestId('main-nav')

for (const [size, viewport] of VIEWPORTS) {
  test.describe(size, () => {
    test.beforeEach(async ({ page }) => page.setViewportSize(viewport))

    test('the list has the tabs', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP())
      await expect(nav(page)).toBeVisible()
      await expect(nav(page).getByRole('tab')).toHaveText(['جستجو', 'نشان‌شده‌ها', 'ثبت آگهی', 'حساب کاربری'])
      await expect(nav(page).getByRole('tab', { name: 'جستجو' })).toHaveAttribute('data-state', 'active')
      await shot(page, 'NAV', size)
    })

    for (const [name, path] of [
      ['ثبت آگهی', '/app/home/new/'],
      ['حساب کاربری', '/app/home/mine/'],
    ]) {
      test(`«${name}» goes to the old page, keeping the launch data`, async ({ page }) => {
        await savedCity(page, TORONTO)
        await mockApi(page)
        await page.goto(APP())
        await nav(page).getByRole('tab', { name }).click()
        await page.waitForURL(`**${path}?*`)
        expect(page.url()).toContain('tginit=signed')
      })
    }

    test('«جستجو» stays on the list and keeps the filters', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP('/app/v2/?types=room'))
      await nav(page).getByRole('tab', { name: 'جستجو' }).click()
      await expect(page).toHaveURL(/types=room/)
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
    })

    test('it never covers the list, and the last card can scroll clear of it', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP())
      const list = page.getByTestId('list-scroll')
      await expect(list).toBeVisible()
      const listBox = (await list.boundingBox())!
      const navBox = (await nav(page).boundingBox())!
      if (size === 'phone') expect(listBox.y + listBox.height).toBeLessThanOrEqual(navBox.y)
      else expect(navBox.y + navBox.height).toBeLessThanOrEqual(listBox.y)
      await list.evaluate((element) => element.scrollTo(0, 1_000_000))
      const end = (await page.getByTestId('list-end').boundingBox())!
      if (size === 'phone') expect(end.y + end.height).toBeLessThanOrEqual(navBox.y)
    })

    test('the list states keep it: loading and failed', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page, { '/feeds/manifest.json': coded(500, 'server_error'), '/api/v1/public/feed/': coded(503, 'feed_failed') })
      await page.goto(APP())
      await expect(page.getByTestId('list-failed')).toBeVisible()
      await expect(nav(page)).toBeVisible()
    })

    for (const [name, url, screen] of [
      ['country', '/app/v2/', 'country-screen'],
      ['city', '/app/v2/choose?country=CA', 'city-screen'],
    ]) {
      test(`hidden on the first-run ${name} step (F01)`, async ({ page }) => {
        await mockApi(page)
        await page.goto(APP(url))
        await expect(page.getByTestId(screen)).toBeVisible()
        await expect(nav(page)).toHaveCount(0)
      })
    }

    test('hidden on a listing (F10)', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page)
      await page.goto(APP(`/app/v2/listing/${CARDS[0].id}`))
      await expect(page.getByTestId('detail')).toBeVisible()
      await expect(nav(page)).toHaveCount(0)
    })

    test('hidden on a listing that is gone (F02#4)', async ({ page }) => {
      await savedCity(page, TORONTO)
      await mockApi(page, { [`/api/v1/public/listings/${CARDS[0].id}/`]: coded(404, 'listing_gone') })
      await page.goto(APP(`/app/v2/listing/${CARDS[0].id}`))
      await expect(page.getByTestId('listing-gone')).toBeVisible()
      await expect(nav(page)).toHaveCount(0)
    })

    test('hidden at maintenance (F25#3)', async ({ page }) => {
      await mockApi(page, { '/api/v1/public/meta/': coded(503, 'maintenance') })
      await page.goto(APP())
      await expect(page.getByText('در حال به‌روزرسانی هستیم')).toBeVisible()
      await expect(nav(page)).toHaveCount(0)
    })
  })
}

test('phone: the bar sits above the safe area, at the board height', async ({ page }) => {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
  await mockApi(page)
  await page.goto(APP())
  await expect(nav(page)).toHaveCSS('height', '76px')
  const box = (await nav(page).boundingBox())!
  // The iOS mock: 700 of height, with 34 + 0 of safe area at the bottom.
  expect(box.y + box.height).toBe(700 - 34)
  for (const tab of await nav(page).getByRole('tab').all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
})

test('desktop: the bar is above the list, at the board height, with 36 px targets', async ({ page }) => {
  await page.setViewportSize(DESKTOP)
  await savedCity(page, TORONTO)
  await mockApi(page)
  await page.goto(APP())
  await expect(nav(page)).toHaveCSS('height', '48px')
  for (const tab of await nav(page).getByRole('tab').all()) expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(36)
})

test('F25#1: a screen that loads on demand, tapped offline, says no connection rather than a crash of ours', async ({ page, context }) => {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
  await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
  await page.goto(APP())
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
  await context.setOffline(true)
  await page.getByTestId('main-nav').getByRole('tab', { name: 'حساب کاربری' }).click()
  await expect(page.getByTestId('chunk-offline')).toContainText('اتصال برقرار نشد')
  await context.setOffline(false)
})
