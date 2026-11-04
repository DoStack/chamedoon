import { expect, type Page, type Request } from '@playwright/test'
import type { Overrides } from '../src/mocks/api'
import { EXCHANGE } from '../src/mocks/fixtures'
import { META_MANUAL, META_POSTING, p4, PARSE } from '../src/mocks/posting'
import { newDraft, type Draft, type DraftCity, type PostKind } from '../src/lib/post-draft'
import { APP, mockApi, savedCity, shot, TORONTO } from './api-mock'

export const CITY: DraftCity = { country: 'CA', city: 'Toronto', slug: 'toronto', name_fa: 'تورنتو' }
export const BERLIN: DraftCity = { country: 'DE', city: 'Berlin', slug: 'berlin', name_fa: 'برلین' }
export const TEXT = 'آپارتمان ۱ خوابه مبله نزدیک Yonge و Finch، ۵ دقیقه تا مترو. ۱۸۵۰ دلار با قبوض و اینترنت.'
export const WANTED_TEXT = 'دانشجوی ارشد، غیرسیگاری، بدون حیوان، آرام.'
export const NO_USERNAME = { status: 200, body: { ...EXCHANGE, user: { ...EXCHANGE.user, telegram_username: '' } } }
export const ON: Overrides = { '/api/v1/public/meta/': { status: 200, body: META_POSTING() }, '/api/v1/public/areas/': { status: 200, body: p4('city_areas') } }
export const MANUAL: Overrides = { '/api/v1/public/meta/': { status: 200, body: META_MANUAL() } }
export const parseWith = (name: Parameters<typeof PARSE>[0]) => ({ 'POST /api/v1/me/posts/parse/': { status: 200, body: PARSE(name) } })
export const P = '/app/v2/post'

// A real 1x1 PNG, so a file that gets as far as a canvas decodes.
export const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64')

export async function open(page: Page, path: string, api: Overrides = {}) {
  await savedCity(page, TORONTO)
  await mockApi(page, { ...ON, ...api })
  await stubStorage(page)
  await page.goto(APP(path))
}

// R2's CORS rule is not set yet, so no test reaches real storage: the signed PUT stops here.
export async function stubStorage(page: Page, status = 200) {
  await page.route('https://storage.test/**', (route) => {
    const headers = { 'access-control-allow-origin': '*', 'access-control-allow-methods': 'PUT, OPTIONS', 'access-control-allow-headers': '*' }
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers })
    return route.fulfill({ status, headers })
  })
}

export async function seed(page: Page, kind: PostKind, patch: Partial<Draft>, city = CITY) {
  const value = { ...newDraft(kind, city), savedAt: Date.now(), ...patch }
  await page.addInitScript(([key, json]) => localStorage.setItem(key, json), [`chamedoon.post.${kind}`, JSON.stringify(value)] as const)
}

export const READY_OFFER: Partial<Draft> = {
  stage: 'parsed',
  text: TEXT,
  homeType: 'b1',
  rooms: 1,
  rentalMode: 'long_term',
  price: '1850',
  bills: 'yes',
  furnished: 'yes',
  area: 'نورث یورک',
  availableFrom: '2026-12-01',
  minTerm: '12',
  size: '55',
  address: '5000 Yonge St',
}

export async function upload(page: Page, name = 'a.png', mime = 'image/png', buffer: Buffer = PNG) {
  await page.getByTestId('photo-input').setInputFiles({ name, mimeType: mime, buffer })
}

// The screens load lazily, and Back has nothing to press until one is up.
export async function pressBack(page: Page) {
  await expect(page.getByTestId('post-scroll')).toBeVisible()
  await page.evaluate(() => (window as never as { Telegram: { WebApp: { __back(): void } } }).Telegram.WebApp.__back())
}

export async function check(page: Page, state: string, size: string) {
  await expect(page.getByTestId('toast').or(page.locator('main')).first()).toBeVisible()
  const overflow = await page.evaluate(() => {
    const body = document.querySelector<HTMLElement>('[data-testid="post-scroll"]') ?? document.documentElement
    return { over: document.documentElement.scrollWidth - innerWidth, inner: body.scrollWidth - body.clientWidth, dir: getComputedStyle(body).direction }
  })
  expect(overflow.over, 'page must not scroll sideways').toBeLessThanOrEqual(0)
  expect(overflow.inner, 'form must not scroll sideways').toBeLessThanOrEqual(0)
  expect(overflow.dir).toBe('rtl')
  await shot(page, state, size)
}

export function requests(page: Page, method: string, part: string) {
  const seen: Request[] = []
  page.on('request', (request) => {
    if (request.method() === method && request.url().includes(part)) seen.push(request)
  })
  return seen
}
