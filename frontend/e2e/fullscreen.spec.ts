import { expect, test, type Page } from '@playwright/test'
import { mockApi, PHONE, DESKTOP, savedCity, TORONTO } from './api-mock'

type Mock = { __calls: string[]; __fire(name: string, patch: object, payload?: object): void; isFullscreen?: boolean }
const calls = (page: Page) => page.evaluate(() => (window as never as { Telegram: { WebApp: Mock } }).Telegram.WebApp.__calls)
const cssVar = (page: Page, name: string) => page.evaluate((key) => document.documentElement.style.getPropertyValue(key), name)
const open = async (page: Page, query: string, viewport = PHONE) => {
  await page.setViewportSize(viewport)
  await savedCity(page, TORONTO)
  await mockApi(page)
  await page.goto(`/app/v2/?tginit=signed&${query}`)
  await expect(page.getByTestId('city-button')).toBeVisible()
}

for (const platform of ['ios', 'android']) {
  test(`${platform} at 8.0 asks for fullscreen, after expand`, async ({ page }) => {
    await open(page, `tgmock=${platform}`)
    expect(await calls(page)).toEqual(['expand', 'requestFullscreen'])
  })
}

test('below 8.0 a phone only expands', async ({ page }) => {
  await open(page, 'tgmock=ios&tgversion=7.10')
  expect(await calls(page)).toEqual(['expand'])
})

for (const platform of ['tdesktop', 'macos', 'weba', 'webk']) {
  test(`${platform} stays a window`, async ({ page }) => {
    await open(page, `tgmock=${platform}`, DESKTOP)
    expect(await calls(page)).not.toContain('requestFullscreen')
  })
}

for (const error of ['UNSUPPORTED', 'ALREADY_FULLSCREEN']) {
  test(`fullscreenFailed ${error} shows nothing and the app works`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (failure) => errors.push(failure.message))
    await open(page, `tgmock=ios&tgfullscreen=${error}`)
    expect(await calls(page)).toEqual(['expand', 'requestFullscreen'])
    await expect(page.getByTestId('crash-screen')).toHaveCount(0)
    await expect(page.getByRole('status')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}

test('nothing sits under Telegram controls: the layout follows the insets when fullscreen changes', async ({ page }) => {
  await open(page, 'tgmock=ios')
  await page.evaluate(() => {
    const app = (window as never as { Telegram: { WebApp: Mock } }).Telegram.WebApp
    app.__fire('fullscreenChanged', { isFullscreen: true, contentSafeAreaInset: { top: 60, bottom: 0, left: 0, right: 0 } })
  })
  expect(await cssVar(page, '--safe-t')).toBe('107px')
  const top = (await page.getByTestId('compact-city').boundingBox())!.y
  expect(top).toBeGreaterThanOrEqual(107)
})

test('desktop uses the whole height Telegram gives, also when the window grows', async ({ page }) => {
  await open(page, 'tgmock=tdesktop', { width: 1024, height: 1000 })
  expect(await cssVar(page, '--app-h')).toBe('600px')
  await page.evaluate(() => {
    const app = (window as never as { Telegram: { WebApp: Mock } }).Telegram.WebApp
    app.__fire('viewportChanged', { viewportHeight: 900 })
  })
  expect(await cssVar(page, '--app-h')).toBe('900px')
  expect((await page.locator('.app-shell').boundingBox())!.height).toBe(900)
})

test('a phone keeps the stable height, so the keyboard does not jump the layout', async ({ page }) => {
  await open(page, 'tgmock=ios')
  expect(await cssVar(page, '--app-h')).toBe('700px')
  await page.evaluate(() => {
    const app = (window as never as { Telegram: { WebApp: Mock } }).Telegram.WebApp
    app.__fire('viewportChanged', { viewportHeight: 400 })
  })
  expect(await cssVar(page, '--app-h')).toBe('700px')
})
