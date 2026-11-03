import type { SizeCode } from './format'

export type WhenKind = '' | 'month' | 'exact' | 'open'

export type PostForm = {
  from: string
  to: string
  stops: string[] // A trip's cities, all in one country. A package has one: `to`.
  whenKind: WhenKind
  month: string
  date: string
  size: SizeCode | ''
  categories: string[]
  refusedOther: string
  description: string
}

const DIGITS: Record<string, string> = Object.fromEntries([...'۰۱۲۳۴۵۶۷۸۹'].map((digit, index) => [digit, String(index)]))
const ARABIC: Record<string, string> = Object.fromEntries([...'٠١٢٣٤٥٦٧٨٩'].map((digit, index) => [digit, String(index)]))
const MONTHS = ['ژانویه', 'فوریه', 'مارس', 'آوریل', 'مه', 'ژوئن', 'ژوئیه', 'اوت', 'سپتامبر', 'اکتبر', 'نوامبر', 'دسامبر']

const latin = (text: string) => text.replace(/[۰-۹٠-٩]/g, (digit) => DIGITS[digit] ?? ARABIC[digit] ?? digit)

function make(year: number, month: number, day: number) {
  const found = new Date(Date.UTC(year, month - 1, day))
  return found.getUTCMonth() === month - 1 && found.getUTCDate() === day ? found : null
}

// The same rules as `texts.parse_typed_date`: from today to a year on. The server still decides; this only keeps a
// date it would refuse from costing a round trip.
export function parseTypedDate(text: string, now = new Date()): Date | null {
  const raw = latin(text).trim()
  const today = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()))
  const year = today.getUTCFullYear()
  const orNextYear = (month: number, day: number) => {
    const found = make(year, month, day)
    if (!found) return null
    return found < today ? make(year + 1, month, day) : found
  }
  let found: Date | null = null
  const full = raw.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/)
  const short = raw.match(/^(\d{1,2})[-/.](\d{1,2})$/)
  const named = raw.match(/^(\d{1,2})\s*(\S+)$/)
  if (full) found = make(Number(full[1]), Number(full[2]), Number(full[3]))
  else if (short) found = orNextYear(Number(short[2]), Number(short[1]))
  else if (named && MONTHS.includes(named[2])) found = orNextYear(MONTHS.indexOf(named[2]) + 1, Number(named[1]))
  if (!found || found < today || found.getTime() - today.getTime() > 365 * 86_400_000) return null
  return found
}

const stop = (key: string) => {
  const [country, city] = key.split(':')
  return { country, city }
}

// What POST /me/requests/ and PATCH /me/requests/{id}/ take. A package's category list is optional; a trip's unticked ones are refused.
export function formBody(form: PostForm, trip: boolean) {
  return {
    origin: stop(form.from),
    destinations: (trip ? form.stops : [form.to]).map(stop),
    when: form.whenKind === 'exact' ? { kind: 'exact', date: form.date } : form.whenKind === 'month' ? { kind: 'month', month: form.month } : { kind: 'open' },
    size: form.size || '1',
    ...(trip || form.categories.length ? { category_codes: form.categories } : {}),
    refused_other: form.refusedOther,
    description: form.description,
  }
}
