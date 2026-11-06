import { expect, test, type Page } from '@playwright/test'
import { ALERTS, CONTACT, META_P3, TORONTO as CARDS, listing, snapshot } from '../src/mocks/fixtures'
import { APP, mockApi, p3Meta, savedCity, TORONTO } from './api-mock'

const STYLE = 'https://tiles.openfreemap.org/styles/positron'
const PLAIN = { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#e8eaed' } }] }
const WITH_MAP = { status: 200, body: { ...META_P3, map_style_url: STYLE } }
const ID = CARDS[0].id
const DETAIL = `/api/v1/public/listings/${ID}/`
const OK = (body: unknown) => ({ status: 200, body })
const FILE = '/feeds/toronto.v1.json'
const PHONE = { width: 390, height: 844 }
const NARROW = { width: 320, height: 740 }

test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } })

// A list long enough to scroll well past its header.
const LONG = () => {
  const file = snapshot('toronto')
  return { status: 200, body: { ...file, listings: Array.from({ length: 24 }, (_, i) => ({ ...file.listings[i % file.listings.length], id: 1000 + i })) } }
}

async function openList(page: Page, extra = '') {
  await page.setViewportSize(PHONE)
  await savedCity(page, TORONTO)
  await page.route(`${STYLE}*`, (route) => route.fulfill({ json: PLAIN }))
  await mockApi(page, { [FILE]: LONG(), '/api/v1/public/meta/': WITH_MAP })
  await page.goto(APP('/app/v2/', `&tgmap=1${extra}`))
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
  return page.getByTestId('list-scroll')
}

test.describe('logo', () => {
  test('in the middle of the band Telegram keeps for its controls, under the system bar', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page)
    await page.goto(APP())
    const box = (await page.getByTestId('app-logo').boundingBox())!
    expect(Math.abs(box.x + box.width / 2 - PHONE.width / 2)).toBeLessThan(1)
    // The iOS mock: system bar 47, controls band 46.
    expect(box.y).toBeGreaterThanOrEqual(47)
    expect(box.y + box.height).toBeLessThanOrEqual(47 + 46)
    expect(Math.abs(box.y + box.height / 2 - (47 + 23))).toBeLessThan(1)
    const main = (await page.locator('.app-main').boundingBox())!
    expect(main.y).toBeLessThanOrEqual(47 + 46 + 1)
  })

  test('leaves room for Telegram controls at 320 wide', async ({ page }) => {
    await page.setViewportSize(NARROW)
    await savedCity(page, TORONTO)
    await mockApi(page)
    await page.goto(APP())
    const box = (await page.getByTestId('app-logo').boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(85)
    expect(box.x + box.width).toBeLessThanOrEqual(NARROW.width - 85)
  })
})

test.describe('list header', () => {
  test('opens on the compact row, scrolling up brings all rows back, and scrolling down takes them away first', async ({ page }) => {
    const scroll = await openList(page)
    const compact = page.getByTestId('compact-header')
    await expect(compact).toBeVisible()
    await expect(page.getByTestId('count-bar')).toBeInViewport()
    await expect(page.getByTestId('listing-card').first()).toBeInViewport()
    // The full rows are scrolled away, under the compact row.
    const under = (await compact.boundingBox())!
    expect((await page.getByTestId('filter-row').boundingBox())!.y + 30).toBeLessThanOrEqual(under.y + under.height)
    const cardTop = async () => (await page.getByTestId('listing-card').first().boundingBox())!.y
    expect(await cardTop()).toBeLessThan(230)
    await scroll.evaluate((el) => (el.scrollTop = 0))
    await expect(compact).toHaveCount(0)
    await expect(page.getByTestId('filter-row')).toBeInViewport()
    await scroll.evaluate((el) => (el.scrollTop = 600))
    await expect(compact).toBeVisible()
    await expect(compact.getByTestId('compact-city')).toContainText('تورنتو')
    await expect(compact.getByTestId('compact-search')).toBeVisible()
    await expect(compact.getByTestId('compact-filter')).toBeVisible()
    await expect(compact.getByTestId('compact-map')).toBeVisible()
    expect((await compact.boundingBox())!.height).toBeLessThanOrEqual(62)
    await scroll.evaluate((el) => (el.scrollTop = 0))
    await expect(compact).toHaveCount(0)
    await expect(page.getByTestId('count-bar')).toBeInViewport()
  })

  test('the list does not move when the row appears, and a slow scroll does not flicker it', async ({ page }) => {
    const scroll = await openList(page)
    const samples = await scroll.evaluate(async (el) => {
      const card = el.querySelector('[data-testid="listing-card"]')!
      const docY = () => card.getBoundingClientRect().top + el.scrollTop
      const start = docY()
      const seen: boolean[] = []
      const drift: number[] = []
      const frame = () => new Promise((done) => requestAnimationFrame(done))
      // One pixel at a time down through the point, a little back and forth, as a thumb does.
      for (let i = 0; i < 90; i++) {
        el.scrollTop = i
        await frame()
        await frame()
        seen.push(Boolean(document.querySelector('[data-testid="compact-header"]')))
        drift.push(Math.abs(docY() - start))
      }
      const at = seen.indexOf(true)
      for (const delta of [-1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6]) {
        el.scrollTop = at + delta
        await frame()
        await frame()
        seen.push(Boolean(document.querySelector('[data-testid="compact-header"]')))
      }
      return { flips: seen.filter((on, i) => i > 0 && on !== seen[i - 1]).length, drift: Math.max(...drift), at }
    })
    expect(samples.at).toBeGreaterThan(0)
    expect(samples.drift).toBeLessThan(1)
    expect(samples.flips).toBe(1)
  })

  test('the sticky row works: count, filters and map', async ({ page }) => {
    const scroll = await openList(page, '&max=2000')
    await scroll.evaluate((el) => (el.scrollTop = 400))
    const compact = page.getByTestId('compact-header')
    await expect(compact).toContainText('۱')
    await compact.getByTestId('compact-filter').click()
    await expect(page.getByTestId('filter-sheet')).toBeVisible()
  })

  test('the map icon opens the map, and the list button brings back the same place', async ({ page }) => {
    const scroll = await openList(page)
    await scroll.evaluate((el) => (el.scrollTop = 600))
    await page.getByTestId('compact-map').click()
    await expect(page).toHaveURL(/\/map/)
    await page.getByTestId('map-to-list').click()
    await expect(page.getByTestId('list-scroll')).toBeVisible()
    await expect.poll(() => page.getByTestId('list-scroll').evaluate((el) => el.scrollTop)).toBeGreaterThan(500)
    await expect(page.getByTestId('compact-header')).toBeVisible()
  })

  test('with the map off there is no map icon', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [FILE]: LONG(), '/api/v1/public/meta/': { status: 200, body: { ...META_P3, map_style_url: null } } })
    await page.goto(APP())
    await expect(page.getByTestId('listing-card').first()).toBeVisible()
    await page.getByTestId('list-scroll').evaluate((el) => (el.scrollTop = 600))
    await expect(page.getByTestId('compact-header')).toBeVisible()
    await expect(page.getByTestId('compact-map')).toHaveCount(0)
  })

  test('new results start from the top, under the compact row', async ({ page }) => {
    const scroll = await openList(page)
    await scroll.evaluate((el) => (el.scrollTop = 600))
    await page.getByTestId('compact-filter').click()
    await page.getByTestId('filter-sheet').getByRole('button', { name: 'سابلت' }).first().click()
    await page.getByTestId('filter-sheet').locator('[data-slot="button"]').last().click()
    await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBeLessThan(150)
    await expect(page.getByTestId('compact-header')).toBeVisible()
    await expect(page.getByTestId('count-bar')).toBeInViewport()
  })
})

test.describe('alert bell', () => {
  test('an icon alone, and its sheet says in one line what it does', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { '/api/v1/public/meta/': p3Meta(), 'GET /api/v1/me/alerts/': OK({ ...ALERTS, results: [], active_count: 0 }) })
    await page.goto(APP())
    const bell = page.getByTestId('alert-chip')
    await expect(bell).toHaveText('')
    await expect(bell).toHaveAttribute('aria-label', 'گوش به زنگ')
    await bell.click()
    await expect(page.getByTestId('alert-explain')).toHaveText('وقتی آگهی مطابقی پیدا شد، به شما پیام می‌دهیم.')
  })
})

test.describe('map', () => {
  async function openMap(page: Page, size = PHONE) {
    await page.setViewportSize(size)
    await savedCity(page, TORONTO)
    await page.route(`${STYLE}*`, (route) => route.fulfill({ json: PLAIN }))
    await mockApi(page, { '/api/v1/public/meta/': WITH_MAP })
    await page.goto(APP('/app/v2/map', '&tgmap=1'))
    await expect(page.getByTestId('map-pin').first()).toBeVisible()
  }

  test('the map has almost the whole height, with one row over it and no list under it', async ({ page }) => {
    await openMap(page)
    const map = (await page.getByTestId('map-area').boundingBox())!
    const main = (await page.locator('.app-main').boundingBox())!
    expect(map.height / main.height).toBeGreaterThan(0.88)
    await expect(page.getByTestId('map-sheet')).toHaveCount(0)
    await expect(page.getByTestId('filter-row')).toHaveCount(0)
    await expect(page.getByTestId('alert-chip')).toHaveCount(0)
    await expect(page.getByTestId('filter-button')).toBeVisible()
  })

  test('«لیست» stays in view with a pin open and over the card, and it goes back to the list', async ({ page }) => {
    await openMap(page, NARROW)
    const list = page.getByTestId('map-to-list')
    await expect(list).toHaveText('لیست')
    await expect(list).toBeInViewport()
    await page.locator('[data-testid="map-pin"][data-area="9001"]').click()
    const card = page.getByTestId('map-card').first()
    await expect(card).toBeVisible()
    await expect(list).toBeInViewport()
    expect((await list.boundingBox())!.y + (await list.boundingBox())!.height).toBeLessThanOrEqual((await card.boundingBox())!.y)
    await list.click()
    await expect(page.getByTestId('list-scroll')).toBeVisible()
  })

  test('a pin shows one card at a time, swiping to the next, and back from it lands on the same pin', async ({ page }) => {
    await openMap(page)
    await page.locator('[data-testid="map-pin"][data-area="9001"]').click()
    const cards = page.getByTestId('map-card')
    await expect(cards).toHaveCount(3)
    const strip = (await page.getByTestId('map-cards').boundingBox())!
    const first = (await cards.first().boundingBox())!
    expect(first.width).toBeGreaterThan(strip.width - 50)
    await cards.first().getByTestId('listing-card').click()
    await expect(page.getByTestId('detail')).toBeVisible()
    await page.goBack()
    await expect(page.getByTestId('map-card').first()).toBeVisible()
    await expect(page.locator('[data-testid="map-pin"][data-area="9001"]')).toHaveAttribute('aria-pressed', 'true')
  })
})

test.describe('detail top', () => {
  const LONGEST = listing(CARDS[0], { price_amount: 12500, price_monthly: 12500, price_period: 'night', price_basis: 'warm', price_currency: 'EUR', country: 'DE', rooms: 2.5, rental_mode: 'sublet', available_from: '2030-12-30', available_to: '2031-09-30', area: 'شارلوتنبورگ-ویلمرسدورف · Kurfürstendamm und Lewishamstraße', has_source: true })

  for (const [name, viewport] of [['320', NARROW], ['390', PHONE]] as const) {
    test(`${name}: price and availability never touch, title and address one line each`, async ({ page }) => {
      await page.setViewportSize(viewport)
      await savedCity(page, TORONTO)
      await mockApi(page, { [DETAIL]: OK(LONGEST) })
      await page.goto(APP(`/app/v2/listing/${ID}`))
      const price = (await page.getByTestId('detail-price').boundingBox())!
      const when = (await page.getByTestId('detail-availability').boundingBox())!
      // RTL: the price is at the right, the availability at the left.
      expect(price.x).toBeGreaterThan(when.x)
      expect(price.x - (when.x + when.width)).toBeGreaterThanOrEqual(12)
      for (const id of ['detail-price', 'detail-availability']) {
        const fits = await page.getByTestId(id).evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
        expect(fits).toBe(true)
      }
      await expect(page.getByTestId('detail-title')).toContainText('آپارتمان')
      await expect(page.getByTestId('detail-title')).toContainText('موقت')
      expect((await page.getByTestId('detail-title').boundingBox())!.height).toBeLessThanOrEqual(30)
      await expect(page.getByTestId('detail-place')).toHaveText('شارلوتنبورگ-ویلمرسدورف')
      expect((await page.getByTestId('detail-place').boundingBox())!.height).toBeLessThanOrEqual(24)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    })
  }

  test('with no date it says available', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { available_from: null, available_to: null })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await expect(page.getByTestId('detail-availability')).toContainText('موجود')
  })

  test('an original-language address stays, on its one line', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { area: 'North York (Yonge and Finch)' })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await expect(page.getByTestId('detail-place')).toContainText('North York (Yonge and Finch)')
    await expect(page.getByTestId('detail-title')).not.toContainText('North York')
  })
})

test.describe('what left the detail', () => {
  test('no law lines, no lease warning, no about, and no money line outside the contact sheet', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    for (const patch of [{ region: 'QC' }, { region: 'ON', rental_mode: 'sublet' as const }]) {
      await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], patch)) })
      await page.goto(APP(`/app/v2/listing/${ID}`))
      await expect(page.getByTestId('detail')).toBeVisible()
      const text = await page.getByTestId('detail').innerText()
      for (const gone of ['ودیعه بگیرد', 'در انتاریو', 'به نام مستأجر', 'درباره‌ی این آگهی', 'پولی نفرستید']) expect(text).not.toContain(gone)
    }
  })

  test('the contact sheet opens at once, with no money line, and holds the original post', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { region: 'QC', has_source: true })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
    const sheet = page.getByTestId('contact-sheet')
    await expect(sheet).not.toContainText('پولی نفرستید')
    await expect(sheet).not.toContainText('کبک')
    await expect(sheet.getByRole('button', { name: 'دیدن پست اصلی' })).toBeVisible()
  })

  test('a listing with no contact still opens the sheet and offers the post', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, {
      [DETAIL]: OK(listing(CARDS[0], { has_telegram: false, has_phone: false, has_source: true })),
      [`POST /api/v1/me/listings/${ID}/contact/`]: OK({ ...CONTACT, telegram: '', telegram_url: '', phone: '' }),
    })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await page.locator('[data-testid="actions"]:visible').getByRole('button').first().click()
    const sheet = page.getByTestId('contact-sheet')
    await expect(sheet).toContainText('آیدی یا شماره‌ای در این آگهی نیست.')
    await expect(sheet.getByTestId('short-warning')).toHaveCount(0)
    await expect(sheet.getByRole('button', { name: 'دیدن پست اصلی' })).toBeVisible()
  })

  test('the contact button names the original post only when there is one', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    for (const [hasSource, label] of [[true, 'تماس و دیدن پست اصلی'], [false, 'تماس']] as const) {
      await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { has_source: hasSource })) })
      await page.goto(APP(`/app/v2/listing/${ID}`))
      await expect(page.locator('[data-testid="actions"]:visible').getByRole('button').first()).toHaveText(label)
    }
  })
})

test.describe('naming', () => {
  test('a lease transfer reads «لیز ترنسفر» in Canada', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { rental_mode: 'lease_transfer' })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await expect(page.getByTestId('detail-title')).toContainText('لیز ترنسفر')
    await expect(page.getByTestId('detail')).not.toContainText('واگذاری')
  })
})

test.describe('saved tab, D2', () => {
  const none = OK({ ...ALERTS, results: [], active_count: 0 })
  const tabs = (page: Page) => page.getByTestId('saved-tabs')

  test('no alerts: it opens on the bookmarks, and the alerts tab is still there', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { '/api/v1/public/meta/': p3Meta(), 'GET /api/v1/me/alerts/': none })
    await page.goto(APP('/app/v2/saved'))
    await expect(page.getByTestId('saved-screen')).toBeVisible()
    await tabs(page).getByRole('tab').first().click()
    await expect(page.getByTestId('alerts-screen')).toBeVisible()
  })

  test('with alerts: it opens on the alerts', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
    await page.goto(APP('/app/v2/saved'))
    await expect(page.getByTestId('alerts-screen')).toBeVisible()
  })
})

test.describe('wanted, D7', () => {
  test('no area: the city is shown', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { kind: 'wanted', area: '', area_id: null, price_amount: null, price_max: 1600 })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await expect(page.getByTestId('wanted-detail')).toContainText('شهر')
    await expect(page.getByTestId('facts')).toContainText(/Toronto|تورنتو/)
  })
})

const PRIVACY = 'برای بهتر کردن چمدون، کارهایی که در برنامه انجام می‌دهید ثبت می‌شود و ممکن است ضبط شود. شماره تلفن و نام کاربری شما در ضبط‌ها دیده نمی‌شود.'

test.describe('privacy notice', () => {
  test('no banner on a first open, one line in the account', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page, { '/api/v1/public/meta/': p3Meta() })
    await page.goto(APP())
    await expect(page.getByTestId('listing-card').first()).toBeVisible()
    await expect(page.getByText(PRIVACY)).toHaveCount(0)
    await page.goto(APP('/app/v2/account'))
    await expect(page.getByTestId('account-privacy')).toHaveText(PRIVACY)
  })
})
