import { expect, test, type Page } from '@playwright/test'
import { EXCHANGE, TORONTO as CARDS, listing, snapshot } from '../src/mocks/fixtures'
import { APP, mockApi, p3Meta, PHONE, savedCity, shot, TORONTO } from './api-mock'

const ID = CARDS[0].id
const DETAIL = `/api/v1/public/listings/${ID}/`
const OK = (body: unknown) => ({ status: 200, body })
const SVG = (hue: number) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 3"><rect width="4" height="3" fill="hsl(${hue},45%,70%)"/></svg>`

// Real addresses, so a test can count what the browser fetched. `bad` in the name fails.
async function servePhotos(page: Page) {
  const asked: string[] = []
  await page.route('**/ph/**', (route) => {
    const { pathname } = new URL(route.request().url())
    asked.push(pathname)
    if (pathname.includes('bad')) return route.abort('failed')
    return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: SVG(Number(pathname.match(/(\d+)\.svg/)?.[1] ?? 0) * 47) })
  })
  return asked
}
const url = (name: string | number) => `/ph/${name}.svg`
const photos = (count: number) => Array.from({ length: count }, (_, i) => ({ url: url(i + 1), thumb: url(`t${i + 1}`) }))

// A finger on the screen: the browser decides by itself whether it scrolls, and whether a click follows.
async function drag(page: Page, from: { x: number; y: number }, dx: number, dy: number) {
  const cdp = await page.context().newCDPSession(page)
  const send = (type: string, points: Array<{ x: number; y: number }>) => cdp.send('Input.dispatchTouchEvent', { type: type as 'touchStart', touchPoints: points })
  await send('touchStart', [from])
  for (let step = 1; step <= 12; step++) {
    await send('touchMove', [{ x: from.x + (dx * step) / 12, y: from.y + (dy * step) / 12 }])
    await page.waitForTimeout(16)
  }
  // Rest before lifting. Headless Chromium draws no frames for a flick, so a snap after one can stall, also on a bare CSS page.
  await page.waitForTimeout(200)
  await send('touchEnd', [])
  await cdp.detach()
}

const middle = async (page: Page, testId: string, at = 0) => {
  await page.getByTestId(testId).nth(at).scrollIntoViewIfNeeded()
  const box = await page.getByTestId(testId).nth(at).boundingBox()
  if (!box) throw new Error(`${testId} has no box`)
  return { x: box.x + box.width / 2, y: box.y + box.height / 2, width: box.width }
}
// In RTL the next photo lies to the left, so the finger moves right; in LTR the other way.
const swipeNext = async (page: Page, testId: string, rtl = true, at = 0) => {
  const point = await middle(page, testId, at)
  await drag(page, point, (rtl ? 1 : -1) * point.width * 0.7, 0)
}
const slideInView = (page: Page, testId: string, at = 0) =>
  page.getByTestId(testId).nth(at).evaluate((box) => Math.round(Math.abs(box.scrollLeft) / box.clientWidth))
const settled = (page: Page, testId: string, index: number, at = 0) =>
  expect.poll(() => page.getByTestId(testId).nth(at).evaluate((box) => Math.abs(box.scrollLeft) / box.clientWidth)).toBeCloseTo(index, 2)

function events(page: Page) {
  const seen: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('photo_')) seen.push(message.text())
  })
  return seen
}

test.use({ hasTouch: true, viewport: PHONE })

test.beforeEach(async ({ page }) => {
  await savedCity(page, TORONTO)
})

const CITY = { ...snapshot('toronto'), listings: CARDS.map((card) => ({ ...card, thumb: url(`${card.id}-1`), thumbs: [1, 2, 3, 4].map((n) => url(`${card.id}-${n}`)) })) }
const WITH_ACCOUNT = { '/api/v1/public/meta/': p3Meta(), '/api/v1/auth/exchange/': OK(EXCHANGE) }
const openList = async (page: Page, extra = '') => {
  await mockApi(page, { '/feeds/toronto.v1.json': OK(CITY), ...WITH_ACCOUNT })
  await page.goto(APP('/app/v2/', extra))
  await expect(page.getByTestId('listing-card').first()).toBeVisible()
}

test.describe('card', () => {
  test('a swipe moves the photo in place and does not open the listing', async ({ page }) => {
    const logged = events(page)
    await servePhotos(page)
    await openList(page)
    await swipeNext(page, 'photo-strip')
    await settled(page, 'photo-strip', 1)
    await page.waitForTimeout(300)
    expect(new URL(page.url()).pathname).not.toContain('/listing/')
    await expect(page.getByTestId('listing-card').first()).toBeVisible()
    expect(logged.join()).toContain('photo_swipe')
    expect(logged.join()).toContain('index: 1')
    // An event holds the place in the row, never an address.
    expect(logged.join()).not.toContain('/ph/')
    await shot(page, 'photo-swipe-card', 'phone')
  })

  test('a tap opens the listing, on the first photo and after a swipe', async ({ page }) => {
    await servePhotos(page)
    await openList(page)
    await page.getByTestId('photo-strip').first().tap()
    await expect(page.getByTestId('detail')).toBeVisible()
    await page.goBack()
    await swipeNext(page, 'photo-strip', true, 1)
    await settled(page, 'photo-strip', 1, 1)
    await page.getByTestId('photo-strip').nth(1).tap()
    await expect(page.getByTestId('detail')).toBeVisible()
  })

  test('a vertical drag from a photo scrolls the list', async ({ page }) => {
    await servePhotos(page)
    await openList(page)
    const before = await middle(page, 'photo-strip')
    await drag(page, before, 0, -250)
    await expect.poll(async () => { const box = await page.getByTestId('photo-strip').first().boundingBox(); return box ? box.y + box.height / 2 : 0 }).toBeLessThan(before.y - 100)
    expect(await slideInView(page, 'photo-strip')).toBe(0)
    expect(new URL(page.url()).pathname).not.toContain('/listing/')
  })

  test('opening the list fetches the first photos and the next, no further', async ({ page }) => {
    const asked = await servePhotos(page)
    await openList(page)
    await expect.poll(() => asked.some((path) => path.endsWith(`${CARDS[0].id}-2.svg`))).toBe(true)
    expect(asked.filter((path) => /-[34]\.svg$/.test(path))).toEqual([])
    await swipeNext(page, 'photo-strip')
    await settled(page, 'photo-strip', 1)
    await expect.poll(() => asked.includes(url(`${CARDS[0].id}-3`))).toBe(true)
    expect(asked).not.toContain(url(`${CARDS[0].id}-4`))
  })

  test('a card without the new field shows its one photo', async ({ page }) => {
    await servePhotos(page)
    await mockApi(page, { '/feeds/toronto.v1.json': OK({ ...snapshot('toronto'), listings: CARDS.map((card) => ({ ...card, thumb: url(1), thumbs: undefined })) }) })
    await page.goto(APP())
    const strip = page.getByTestId('photo-strip').first()
    await expect(strip.locator('img')).toHaveCount(1)
    await expect(strip.getByTestId('photo')).toBeVisible()
    expect(await strip.evaluate((box) => box.scrollWidth <= box.clientWidth)).toBe(true)
  })

  test('a photo that fails shows the placeholder, and the next still comes', async ({ page }) => {
    await servePhotos(page)
    await mockApi(page, { '/feeds/toronto.v1.json': OK({ ...snapshot('toronto'), listings: CARDS.map((card) => ({ ...card, thumb: url('bad'), thumbs: [url('bad'), url(2)] })) }) })
    await page.goto(APP())
    const first = page.getByTestId('listing-card').first()
    await expect(first.getByTestId('photo-placeholder')).toBeVisible()
    await swipeNext(page, 'photo-strip')
    await settled(page, 'photo-strip', 1)
    await expect(first.getByTestId('photo')).toBeVisible()
  })
})

test.describe('detail and viewer', () => {
  const openDetail = async (page: Page, count: number, extra = '') => {
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { photos: photos(count), photo_count: count })) })
    await page.goto(APP(`/app/v2/listing/${ID}`, extra))
    await expect(page.getByTestId('detail')).toBeVisible()
  }

  test('a swipe moves the count, and a tap opens the viewer on that photo', async ({ page }) => {
    await servePhotos(page)
    await openDetail(page, 5)
    await expect(page.getByTestId('photo-position')).toHaveText('۱ از ۵')
    await swipeNext(page, 'photo-strip')
    await expect(page.getByTestId('photo-position')).toHaveText('۲ از ۵')
    await expect(page.getByTestId('viewer')).toBeHidden()
    await shot(page, 'photo-swipe-detail', 'phone')
    await settled(page, 'photo-strip', 1)
    await page.getByTestId('photo-strip').tap()
    const viewer = page.getByTestId('viewer')
    await expect(viewer).toContainText('۲ از ۵')
    await settled(page, 'viewer-strip', 1)
    await expect(viewer.getByTestId('viewer-photo')).toHaveAttribute('src', url(2))
  })

  test('the viewer reaches every photo, its thumbnail follows, and Back closes it', async ({ page }) => {
    const logged = events(page)
    await servePhotos(page)
    await openDetail(page, 8)
    await page.getByTestId('photo-strip').tap()
    const viewer = page.getByTestId('viewer')
    await expect(viewer).toContainText('۱ از ۸')
    for (let n = 2; n <= 7; n++) {
      await swipeNext(page, 'viewer-strip')
      await settled(page, 'viewer-strip', n - 1)
    }
    await expect(viewer).toContainText('۷ از ۸')
    await expect(viewer.getByTestId('viewer-photo')).toHaveAttribute('src', url(7))
    const seventh = viewer.getByRole('button', { name: 'عکس ۷' })
    await expect(seventh).toHaveClass(/border-primary/)
    await expect(seventh).toBeInViewport({ ratio: 1 })
    await shot(page, 'photo-swipe-viewer', 'phone')
    expect(logged.filter((line) => line.includes('photo_swipe'))).toHaveLength(6)
    expect(logged.join()).toContain('index: 6')

    await viewer.getByRole('button', { name: 'عکس ۲' }).click()
    await expect(viewer).toContainText('۲ از ۸')
    await settled(page, 'viewer-strip', 1)
    await viewer.getByRole('button', { name: 'عکس ۸' }).click()
    await expect(viewer).toContainText('۸ از ۸')
    await settled(page, 'viewer-strip', 7)

    await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
    await expect(viewer).toBeHidden()
    await expect(page.getByTestId('detail')).toBeVisible()
  })

  test('a far thumbnail does not load what lies between', async ({ page }) => {
    const asked = await servePhotos(page)
    await openDetail(page, 8)
    await page.getByTestId('photo-strip').tap()
    await page.getByTestId('viewer').getByRole('button', { name: 'عکس ۸' }).click()
    await expect(page.getByTestId('viewer-photo')).toHaveAttribute('src', url(8))
    await page.waitForTimeout(200)
    expect(asked).not.toContain(url(5))
  })

  test('one photo has nothing to swipe, and still opens', async ({ page }) => {
    await servePhotos(page)
    await openDetail(page, 1)
    await expect(page.getByTestId('photo-position')).toHaveText('۱ از ۱')
    expect(await page.getByTestId('photo-strip').evaluate((box) => box.scrollWidth <= box.clientWidth)).toBe(true)
    await page.getByTestId('photo-strip').tap()
    await expect(page.getByTestId('viewer')).toContainText('۱ از ۱')
  })

  test('no photo keeps its empty state', async ({ page }) => {
    await openDetail(page, 0)
    await expect(page.getByTestId('photo-placeholder')).toBeVisible()
    await expect(page.getByRole('button', { name: 'نمایش عکس‌ها' })).toHaveCount(0)
  })

  test('a photo that fails in the viewer offers a retry there, and a swipe moves on', async ({ page }) => {
    await servePhotos(page)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { photos: [{ url: url('bad'), thumb: url('t1') }, { url: url(2), thumb: url('t2') }], photo_count: 2 })) })
    await page.goto(APP(`/app/v2/listing/${ID}`))
    await page.getByTestId('photo-strip').tap()
    await expect(page.getByTestId('viewer-failed')).toBeVisible()
    await page.getByTestId('viewer-failed').getByRole('button', { name: 'تلاش مجدد' }).click()
    await expect(page.getByTestId('viewer-failed')).toBeVisible()
    await swipeNext(page, 'viewer-strip')
    await settled(page, 'viewer-strip', 1)
    await expect(page.getByTestId('viewer-photo')).toHaveAttribute('src', url(2))
  })
})

test.describe('direction', () => {
  const firstIsOnThe = (page: Page) =>
    page.getByTestId('photo-strip').first().evaluate((box) => {
      const [a, b] = [...box.children].map((slide) => slide.getBoundingClientRect().left)
      return a > b ? 'right' : 'left'
    })

  test('Persian starts on the right', async ({ page }) => {
    await servePhotos(page)
    await openList(page)
    await expect(page.locator('html')).toHaveAttribute('dir', 'rtl')
    expect(await firstIsOnThe(page)).toBe('right')
    await swipeNext(page, 'photo-strip', true)
    await settled(page, 'photo-strip', 1)
  })

  for (const lang of ['en', 'de']) {
    test(`${lang} starts on the left`, async ({ page }) => {
      await servePhotos(page)
      await openList(page, `&tglang=${lang}`)
      await expect(page.locator('html')).toHaveAttribute('dir', 'ltr')
      expect(await firstIsOnThe(page)).toBe('left')
      await swipeNext(page, 'photo-strip', false)
      await settled(page, 'photo-strip', 1)
      expect(new URL(page.url()).pathname).not.toContain('/listing/')
    })
  }

  test('LTR viewer opens on the swiped photo', async ({ page }) => {
    await servePhotos(page)
    await mockApi(page, { [DETAIL]: OK(listing(CARDS[0], { photos: photos(4), photo_count: 4 })), ...WITH_ACCOUNT })
    await page.goto(APP(`/app/v2/listing/${ID}`, '&tglang=en'))
    await swipeNext(page, 'photo-strip', false)
    await settled(page, 'photo-strip', 1)
    await page.getByTestId('photo-strip').tap()
    await settled(page, 'viewer-strip', 1)
    await expect(page.getByTestId('viewer-photo')).toHaveAttribute('src', url(2))
  })
})
