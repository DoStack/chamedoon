// Temporary production check (not committed): an anonymous browser opens the web app.
import { chromium } from '@playwright/test'

const base = 'https://chamedoon.bigde.li'
const out = process.argv[2] || '.'
const browser = await chromium.launch()
for (const [name, viewport] of [['phone', { width: 390, height: 844 }], ['desktop', { width: 1440, height: 900 }]]) {
  const page = await browser.newPage({ viewport })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 120)))
  const response = await page.goto(`${base}/app/home/`)
  const robots = response.headers()['x-robots-tag']
  let cards = 0
  let state = ''
  try {
    await page.waitForSelector('[data-testid="listing-card"], [data-testid="outside-screen"], [data-testid="first-run"], [data-testid="city-sheet"]', { timeout: 20000 })
  } catch {}
  await page.waitForTimeout(1500)
  if (await page.getByText('کانادا').count()) {
    await page.getByText('کانادا').first().click()
    await page.getByText('تورنتو').first().click()
    await page.waitForSelector('[data-testid="listing-card"]', { timeout: 20000 }).catch(() => {})
    await page.waitForTimeout(1500)
  }
  cards = await page.locator('[data-testid="listing-card"]').count()
  state = (await page.locator('[data-testid="outside-screen"]').count()) ? 'outside' : 'app'
  const wide = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)
  await page.screenshot({ path: `${out}/prod-home-${name}.png` })
  console.log(name, 'home', response.status(), 'robots', robots, 'state', state, 'cards', cards, 'noSideScroll', wide, 'errors', errors.length)
  if (cards) {
    await page.locator('[data-testid="listing-card"]').first().click()
    await page.waitForTimeout(2500)
    console.log(name, 'detail url', new URL(page.url()).pathname)
    await page.screenshot({ path: `${out}/prod-detail-${name}.png` })
    await page.goBack()
    await page.waitForTimeout(2000)
    const saveBtn = page.locator('[data-testid="bookmark"]:visible').first()
    if (await saveBtn.count()) {
      await saveBtn.click()
      const sheet = await page.waitForSelector('[data-testid="web-login-match"]', { timeout: 10000 }).then(() => true, () => false)
      console.log(name, 'login sheet with match', sheet, sheet ? await page.locator('[data-testid="web-login-match"]').innerText() : '')
      await page.screenshot({ path: `${out}/prod-login-${name}.png` })
    }
  }
  const courier = await browser.newPage({ viewport })
  await courier.goto(`${base}/app/`)
  const signin = await courier.waitForSelector('[data-testid="web-signin"], [data-testid="outside"]', { timeout: 20000 }).then((el) => el.getAttribute('data-testid'), () => 'none')
  console.log(name, 'courier', signin)
  await courier.screenshot({ path: `${out}/prod-courier-${name}.png` })
}
await browser.close()
