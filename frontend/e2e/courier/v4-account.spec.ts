import { expect, test } from '@playwright/test'
import { COURIER, fx, mockCourier, PHONE } from './mock'

const MINE = 'GET me/requests/201/'
const trip = (changes: object = {}) => ({ status: 200, body: { ...fx('mineActive'), ...fx('cardTrip'), id: 201, status: 'ACTIVE', editable: true, ...changes } })
const meta = (features: Record<string, boolean>) => ({ status: 200, body: { ...fx('meta'), features } })

test.describe('V4 account and mine', () => {
  test.use({ viewport: PHONE })

  test('closing a trip asks whether the package went, and sends the answer', async ({ page }) => {
    let closed = false
    const calls = await mockCourier(page, {
      [MINE]: () => (closed ? trip({ status: 'COMPLETED', editable: false }) : trip()),
      'POST me/requests/201/close/': () => ((closed = true), trip({ status: 'COMPLETED', editable: false })),
    })
    await page.goto(COURIER('me/requests/201'))
    await page.getByTestId('mine-close').click()
    await expect(page.getByTestId('mine-close-confirm')).toContainText('بسته ارسال شد؟')
    await page.getByTestId('mine-close-sent').click()
    await expect(page.getByTestId('mine-closed')).toBeVisible()
    expect(calls.find((call) => call.path.endsWith('close/'))?.body).toEqual({ package_sent: true })
  })

  test('post again keeps the details and asks only for a new date', async ({ page }) => {
    await mockCourier(page, { [MINE]: trip({ status: 'CANCELLED', editable: false }) })
    await page.goto(COURIER('me/requests/201'))
    await page.getByTestId('mine-post-again').click()
    await page.waitForURL(/\/post\/trip/)
    await expect(page.getByTestId('post-step-2')).toBeVisible()
  })

  test('both rating comments show', async ({ page }) => {
    const match = fx('match') as { rating: object }
    await mockCourier(page, { 'GET me/matches/9/': { status: 200, body: { ...match, status: 'COMPLETED', rating: { ...match.rating, can_complete: false, my_score: 4, their_score: 5, my_comment: 'خوب بود', their_comment: 'خیلی خوش‌قول' } } } })
    await page.goto(COURIER('me/matches/9'))
    await expect(page.getByTestId('their-comment')).toContainText('خیلی خوش‌قول')
    await expect(page.getByTestId('my-comment')).toContainText('خوب بود')
  })

  test('connections switch renames the page', async ({ page }) => {
    await mockCourier(page, { 'meta/': meta({ v4_connections: true }) })
    await page.goto(COURIER('me'))
    await expect(page.getByTestId('account-matches')).toContainText('ارتباط‌ها')
    await page.goto(COURIER('me/matches'))
    await expect(page.getByTestId('inbox')).toContainText('ارتباط‌ها')
  })
})
