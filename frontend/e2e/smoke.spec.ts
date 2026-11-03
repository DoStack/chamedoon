import { expect, test } from '@playwright/test'
import { APP, mockApi } from './api-mock'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('shell renders RTL in Vazirmatn with the mocked Telegram insets', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))

  await page.goto(APP())
  await expect(page.getByTestId('app-ready')).toBeVisible()
  await page.evaluate(() => document.fonts.ready)

  const report = await page.evaluate(() => {
    const shell = document.querySelector('.app-shell')!
    return {
      dir: document.documentElement.dir,
      direction: getComputedStyle(document.body).direction,
      font: getComputedStyle(document.body).fontFamily,
      loaded: [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family),
      height: shell.getBoundingClientRect().height,
      top: getComputedStyle(document.querySelector('.app-top')!).paddingTop,
      bottom: getComputedStyle(document.querySelector('.app-bottom')!).paddingBottom,
      overflow: document.documentElement.scrollWidth > window.innerWidth,
    }
  })
  expect(report.dir).toBe('rtl')
  expect(report.direction).toBe('rtl')
  expect(report.font).toMatch(/^"Vazirmatn Variable"/)
  expect(report.loaded.join()).toContain('Vazirmatn Variable')
  expect(report.height).toBe(700) // stable height, not 720 or 100dvh
  expect(report.top).toBe('93px') // 47 + 46
  expect(report.bottom).toBe('34px')
  expect(report.overflow).toBe(false)

  await page.evaluate(() => {
    const app = (window as unknown as { Telegram: { WebApp: { __fire(e: string, p: object): void } } }).Telegram.WebApp
    app.__fire('viewportChanged', { viewportStableHeight: 500 })
  })
  await expect(page.locator('.app-shell')).toHaveCSS('height', '500px')
  expect(errors).toEqual([])
})

test('touch targets follow the viewport density', async ({ page }) => {
  const buttonHeight = () =>
    page.evaluate(() => {
      const b = document.createElement('button')
      b.dataset.slot = 'button'
      document.body.append(b)
      const h = b.getBoundingClientRect().height
      b.remove()
      return h
    })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(APP())
  await expect(page.getByTestId('app-ready')).toBeVisible()
  expect(await buttonHeight()).toBe(44)

  await page.setViewportSize({ width: 1024, height: 844 })
  await expect.poll(buttonHeight).toBe(36)
})

test('deep links stay inside the shell', async ({ page }) => {
  await page.goto('/app/v2/some/deep/link?tgmock=desktop&tginit=signed')
  await expect(page.getByTestId('app-ready')).toBeVisible()
})
