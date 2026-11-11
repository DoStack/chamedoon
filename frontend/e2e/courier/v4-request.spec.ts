import { expect, test } from '@playwright/test'
import { COURIER, fx, mockCourier, PHONE } from './mock'

const meta = (features: Record<string, boolean>) => ({ status: 200, body: { ...fx('meta'), features } })
const inHours = (hours: number) => new Date(Date.now() + hours * 3_600_000 - 60_000).toISOString()

test.describe('V4 request page', () => {
  test.use({ viewport: PHONE })

  test('hero, carries chips, anonymous person, no report link while its switch is off', async ({ page }) => {
    await mockCourier(page)
    await page.goto(COURIER('request/101'))
    await expect(page.getByTestId('request-hero')).toBeVisible()
    await expect(page.getByTestId('carry')).toBeVisible()
    await expect(page.getByTestId('who')).not.toContainText('Sara')
    await expect(page.getByTestId('report-open')).toHaveCount(0)
  })

  test('report: reason and text go to the report endpoint', async ({ page }) => {
    const calls = await mockCourier(page, { 'meta/': meta({ v4_report: true }), 'POST requests/101/report/': { status: 201, body: fx('ticketThread') } })
    await page.goto(COURIER('request/101'))
    await page.getByTestId('report-open').click()
    await page.getByRole('radio', { name: 'تبلیغ' }).click()
    await page.getByTestId('report-text').fill('آگهی تکراری')
    await page.getByTestId('report-send').click()
    await expect(page.getByTestId('toast')).toContainText('گزارش فرستاده شد')
    const sent = calls.find((call) => call.method === 'POST' && call.path === 'requests/101/report/')
    expect(sent?.body).toEqual({ reason: 'SPAM', text: 'آگهی تکراری' })
  })

  test('pending intro shows the hours left', async ({ page }) => {
    await mockCourier(page, {
      'requests/101/': { status: 200, body: { ...fx('requestDetail'), intro_id: 9, intro_status: 'PENDING' } },
      'me/intros/9/': { status: 200, body: { ...fx('introTarget'), expires_at: inHours(68) } },
    })
    await page.goto(COURIER('request/101'))
    await expect(page.getByTestId('intro-waiting').filter({ visible: true })).toContainText('۶۸ ساعت')
  })
})
