import { describe, expect, it } from 'vitest'
import { cleanUrl, scrubData, scrubText } from './scrub'

describe('scrubText', () => {
  it.each([
    ['call +1 (514) 555-1212 now', 'call [phone] now'],
    ['۰۹۱۲۳۴۵۶۷۸۹', '[phone]'],
    ['tel 514-555-1212', 'tel [phone]'],
    ['ask @some_user today', 'ask [username] today'],
    ['https://t.me/some_user?x=1', '[telegram-link]'],
    ['t.me/some_user', '[telegram-link]'],
    ['tg://resolve?domain=abc', '[telegram-link]'],
    ['me@mail.example.com', '[email]'],
    ['eyJhbGciOiJIUzI1.eyJ1c2VyX2lkIjox.abc-def', '[token]'],
    ['Bearer abc.def', '[token]'],
    ['x?tgWebAppData=query_id%3DAAH&y=1', 'x?tgWebAppData=[masked]&y=1'],
  ])('masks %s', (input, output) => {
    expect(scrubText(input)).toBe(output)
  })

  it.each(['۱۲۰۰ دلار', '1,200,000', '$1200 - 1500', '2026-11-04', 'listing 123456', 'tg_77830141', 'خانه در تورنتو'])('leaves %s', (input) => {
    expect(scrubText(input)).toBe(input)
  })
})

describe('scrubData', () => {
  it('walks objects and arrays and keeps numbers', () => {
    expect(scrubData({ a: ['@some_user'], b: { c: 5551212555 }, d: true })).toEqual({ a: ['[username]'], b: { c: 5551212555 }, d: true })
  })
})

describe('cleanUrl', () => {
  it('drops the fragment and Telegram params, keeps the filters', () => {
    const url = cleanUrl(`${location.origin}/app/v2/?city=toronto&min=900&tgWebAppStartParam=home_5#tgWebAppData=secret`)
    expect(url).toContain('city=toronto')
    expect(url).toContain('min=900')
    expect(url).not.toContain('tgWebApp')
    expect(url).not.toContain('secret')
  })

  it('drops search words', () => {
    const url = cleanUrl(`${location.origin}/app/v2/search?text=0912345&q=abc&edit=x&city=toronto`)
    expect(url).toContain('city=toronto')
    expect(url).not.toMatch(/0912345|abc|edit=/)
  })

  it('keeps only origin and path of another host, so a signed upload loses its signature', () => {
    expect(cleanUrl('https://acc.r2.cloudflarestorage.com/home-media/a.jpg?X-Amz-Signature=abc')).toBe('https://acc.r2.cloudflarestorage.com/home-media/a.jpg')
  })
})
