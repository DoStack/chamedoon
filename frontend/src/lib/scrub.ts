// Values that must never leave the phone: phone numbers, Telegram usernames and links, tokens, Telegram's launch data.
// Small and in the first load on purpose, since console lines (which replay stores) pass through it too.
const DIGIT = '[\\d۰-۹٠-٩]'
const PHONE = new RegExp(`(?<![\\w.])(?!\\d{4}-\\d{2}-\\d{2}\\b)\\+?\\(?${DIGIT}(?:[\\s().\\-\\u2010-\\u2015]{0,2}${DIGIT}){6,}(?!\\w)`, 'g')
const USERNAME = /(?<![\w/.@])@[A-Za-z][A-Za-z0-9_]{3,31}/g
const TELEGRAM_LINK = /(?:https?:\/\/)?(?:t|telegram)\.me\/[^\s"'<>)]+|tg:\/\/[^\s"'<>)]+/gi
const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/g
const JWT = /eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]*/g
const BEARER = /Bearer\s+[\w.-]+/gi
const LAUNCH_DATA = /\b(tgWebAppData|init_?data|query_id|auth_date|signature|hash|access_token|token)=[^&#\s"']*/gi

export function scrubText(text: string) {
  return text
    .replace(JWT, '[token]')
    .replace(BEARER, '[token]')
    .replace(LAUNCH_DATA, '$1=[masked]')
    .replace(TELEGRAM_LINK, '[telegram-link]')
    .replace(EMAIL, '[email]')
    .replace(USERNAME, '[username]')
    .replace(PHONE, '[phone]')
}

export function scrubData(value: unknown, depth = 0): unknown {
  if (typeof value === 'string') return scrubText(value)
  if (depth > 6 || value === null || typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map((item) => scrubData(item, depth + 1))
  const out: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(value)) out[key] = scrubData(item, depth + 1)
  return out
}

// Telegram puts initData in the fragment and its params in the query. A signed upload URL holds its secret in the query.
// Search words in the query are free text a person typed, so they can hold a phone number.
export function cleanUrl(raw: string) {
  try {
    const url = new URL(raw, location.origin)
    if (url.origin !== location.origin) return scrubText(`${url.origin}${url.pathname}`)
    for (const key of [...url.searchParams.keys()]) {
      if (/^(tgWebApp|init_?data|token|access_token|signature|hash|x-amz-|text$|edit$|q$|query$|search$)/i.test(key)) url.searchParams.delete(key)
    }
    url.hash = ''
    return scrubText(url.toString())
  } catch {
    return scrubText(raw.split('#')[0])
  }
}
