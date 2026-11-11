import { expect, test } from '@playwright/test'
import { APP, mockApi, savedCity, TORONTO } from './api-mock'
import { COURIER, mockCourier } from './courier/mock'

const PHONE = { width: 375, height: 812 }

test.describe('service header', () => {
  test('housing: the chevron opens the services, the current one is checked, and courier opens the courier app', async ({ page }) => {
    const events: string[] = []
    page.on('console', (message) => events.push(message.text()))
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page)
    await mockCourier(page)
    await page.goto(APP())
    const header = page.getByTestId('service-header')
    await expect(header.getByRole('button', { name: 'چمدون - خانه' })).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('service-menu')).toHaveCount(0)

    await header.getByRole('button', { name: 'چمدون - خانه' }).click()
    const menu = page.getByTestId('service-menu')
    await expect(menu).toBeVisible()
    await expect(page.getByTestId('service-home')).toHaveAttribute('aria-checked', 'true')
    await expect(page.getByTestId('service-courier')).toHaveAttribute('aria-checked', 'false')
    for (const item of await menu.getByRole('menuitemradio').all()) expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(44)

    // The scrim and Escape close it without leaving.
    await page.keyboard.press('Escape')
    await expect(menu).toHaveCount(0)
    await header.getByRole('button').click()
    await page.getByTestId('service-scrim').click({ position: { x: 20, y: 400 } })
    await expect(menu).toHaveCount(0)

    await header.getByRole('button').click()
    await page.getByTestId('service-courier').click()
    await page.waitForURL(/\/app\/\?tgmock=ios&tginit=signed/)
    expect(events.some((text) => text.startsWith('[chamedoon] service_switch'))).toBe(true)
    await expect(page.getByTestId('service-header').getByRole('button', { name: 'چمدون - باربری' })).toBeVisible()
  })

  test('courier: picking the current service stays, picking housing opens the housing app', async ({ page }) => {
    await page.setViewportSize(PHONE)
    await savedCity(page, TORONTO)
    await mockApi(page)
    await mockCourier(page)
    await page.goto(COURIER())
    const header = page.getByTestId('service-header')
    await header.getByRole('button', { name: 'چمدون - باربری' }).click()
    await expect(page.getByTestId('service-courier')).toHaveAttribute('aria-checked', 'true')
    await page.getByTestId('service-courier').click()
    await expect(page.getByTestId('service-menu')).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe('/app/')

    await header.getByRole('button').click()
    await page.getByTestId('service-home').click()
    await page.waitForURL((url) => url.pathname === '/app/home/')
    await expect(page.getByTestId('service-header').getByRole('button', { name: 'چمدون - خانه' })).toBeVisible()
  })
  // The page itself never scrolls or bounces, so the header stays put at the end of a list (iOS carried the overscroll on
  // to the page and slid the header under Telegram's controls).
  for (const [name, open] of [
    ['housing', async (page: import('@playwright/test').Page) => { await savedCity(page, TORONTO); await mockApi(page); await page.goto(APP()) }],
    ['courier', async (page: import('@playwright/test').Page) => { await mockCourier(page); await page.goto(COURIER('?side=DEMAND&from=CA:toronto&to=IR:tehran')) }],
  ] as const) {
    test(`${name}: the header is fixed, the page never scrolls`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 560 })
      await open(page)
      await expect(page.getByTestId('service-header')).toBeVisible()
      await page.waitForTimeout(500)
      const state = await page.evaluate(() => {
        for (const element of document.querySelectorAll<HTMLElement>('.app-main, .app-main *')) element.scrollTop = element.scrollHeight
        window.scrollTo(0, 10_000)
        const css = (element: Element) => getComputedStyle(element)
        return {
          top: document.querySelector('.app-top')!.getBoundingClientRect().top,
          page: document.scrollingElement!.scrollTop,
          html: [css(document.documentElement).overflowY, css(document.documentElement).overscrollBehaviorY],
          body: [css(document.body).overflowY, css(document.body).overscrollBehaviorY],
          main: css(document.querySelector('.app-main')!).overscrollBehaviorY,
        }
      })
      expect(state).toEqual({ top: 0, page: 0, html: ['hidden', 'none'], body: ['hidden', 'none'], main: 'contain' })
    })
  }
})
