// Courier smart search: one line of Persian (or English) read by rules, no AI model.
// «از تورنتو به تهران تا آخر نوامبر، مدارک» → from, to, month, category, size, side.

export type ParseCountry = { code: string; name_fa: string; name_en: string; cities: Array<{ slug: string; name_fa: string; name_en: string }> }
export type ParseCategory = { code: string; name_fa: string; name_en: string }
export type Parsed = { from: string; to: string; month: string; cat: string; size: string; side: 'DEMAND' | 'SUPPLY' | '' }

const DIGITS: Record<string, string> = { '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9' }

export function normalize(text: string) {
  return text
    .replace(/[۰-۹]/g, (digit) => DIGITS[digit])
    .replace(/ي/g, 'ی')
    .replace(/ك/g, 'ک')
    .replace(/‌/g, ' ')
    .replace(/[،,.؛!?؟]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

// Month names people type, Gregorian in Persian and English, and Jalali, to a Gregorian month number. A Jalali month maps to the
// Gregorian month it mostly falls in.
const MONTHS: Array<[string, number]> = [
  ['ژانویه', 1], ['فوریه', 2], ['مارس', 3], ['آوریل', 4], ['مه', 5], ['می', 5], ['ژوئن', 6], ['ژوئیه', 7], ['جولای', 7], ['اوت', 8], ['آگوست', 8], ['سپتامبر', 9], ['اکتبر', 10], ['نوامبر', 11], ['دسامبر', 12],
  ['january', 1], ['february', 2], ['march', 3], ['april', 4], ['may', 5], ['june', 6], ['july', 7], ['august', 8], ['september', 9], ['october', 10], ['november', 11], ['december', 12],
  ['فروردین', 4], ['اردیبهشت', 5], ['خرداد', 6], ['تیر', 7], ['مرداد', 8], ['شهریور', 9], ['مهر', 10], ['آبان', 11], ['آذر', 12], ['دی', 1], ['بهمن', 2], ['اسفند', 3],
]

const SIZES: Array<[RegExp, string]> = [
  [/(فقط )?مدرک( و نامه)?|نامه|document/, 'doc'],
  [/تا ?1 ?کیلو|زیر ?1 ?کیلو|کمتر از ?1 ?کیلو/, '1'],
  [/1 ?تا ?3 ?کیلو|2 ?کیلو|3 ?کیلو/, '3'],
  [/(بیشتر از|بالای) ?3 ?کیلو|3 ?کیلو ?\+|[4-9] ?کیلو|\d{2} ?کیلو/, '3p'],
]

function monthKey(month: number, now: Date) {
  const year = now.getUTCFullYear() + (month < now.getUTCMonth() + 1 ? 1 : 0)
  return `${year}-${String(month).padStart(2, '0')}`
}

type Place = { key: string; names: string[] }

function places(countries: ParseCountry[]): Place[] {
  const rows: Place[] = []
  for (const country of countries) {
    for (const city of country.cities) rows.push({ key: `${country.code}:${city.slug}`, names: [city.name_fa, city.name_en, city.slug.replace(/-/g, ' ')].map(normalize).filter(Boolean) })
  }
  // Longer names first, so «سنت جانز» wins over «جانز».
  return rows.sort((a, b) => Math.max(...b.names.map((name) => name.length)) - Math.max(...a.names.map((name) => name.length)))
}

function findPlace(text: string, rows: Place[], taken: string[]): { key: string; at: number } | null {
  let best: { key: string; at: number } | null = null
  for (const row of rows) {
    if (taken.includes(row.key)) continue
    for (const name of row.names) {
      const at = ` ${text} `.indexOf(` ${name} `)
      if (at >= 0 && (best === null || at < best.at)) best = { key: row.key, at }
    }
  }
  return best
}

export function parseCourier(raw: string, countries: ParseCountry[], categories: ParseCategory[], now = new Date()): Parsed {
  const text = normalize(raw)
  const out: Parsed = { from: '', to: '', month: '', cat: '', size: '', side: '' }
  if (!text) return out
  const rows = places(countries)

  // «از X» and «به X» say which end; otherwise the first place named is the origin.
  const fromMatch = rows.find((row) => row.names.some((name) => ` ${text} `.includes(` از ${name} `)))
  const toMatch = rows.find((row) => row.names.some((name) => ` ${text} `.includes(` به ${name} `) || ` ${text} `.includes(` to ${name} `)))
  if (fromMatch) out.from = fromMatch.key
  if (toMatch && toMatch.key !== out.from) out.to = toMatch.key
  if (!out.from || !out.to) {
    const first = findPlace(text, rows, [out.from, out.to].filter(Boolean))
    const second = first ? findPlace(text, rows, [out.from, out.to, first.key].filter(Boolean)) : null
    const found = [first, second].filter(Boolean).sort((a, b) => a!.at - b!.at) as Array<{ key: string }>
    for (const place of found) {
      if (!out.from) out.from = place.key
      else if (!out.to && place.key !== out.from) out.to = place.key
    }
  }

  for (const [name, month] of MONTHS) {
    if (` ${text} `.includes(` ${normalize(name)} `)) {
      out.month = monthKey(month, now)
      break
    }
  }
  if (!out.month && /این ماه|همین ماه|this month/.test(text)) out.month = monthKey(now.getUTCMonth() + 1, now)

  for (const category of categories) {
    const names = [category.name_fa, category.name_en].map(normalize).filter(Boolean)
    if (names.some((name) => ` ${text} `.includes(` ${name} `))) {
      out.cat = category.code
      break
    }
  }
  if (!out.cat && /مدارک|مدرک/.test(text)) out.cat = categories.find((row) => row.code === 'DOCUMENTS')?.code ?? ''

  for (const [pattern, code] of SIZES) {
    if (pattern.test(text)) {
      out.size = code
      break
    }
  }
  // A document is a category here unless the person only said «فقط مدرک».
  if (out.size === 'doc' && out.cat === 'DOCUMENTS' && !/فقط مدرک|نامه/.test(text)) out.size = ''

  if (/مسافر|traveler|traveller|کسی که می ?بره/.test(text)) out.side = 'DEMAND'
  else if (/بسته ها|بسته‌ها|package/.test(text)) out.side = 'SUPPLY'
  return out
}
