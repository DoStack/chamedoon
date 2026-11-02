// SPIKE P0: client filter and facet counts. Each section is counted from the other sections' filters (F05).
export type Listing = {
  id: number
  kind: string
  property_type: string
  rental_mode: string
  area: string
  price_amount: number | null
  price_currency: string
  rooms: number | null
  furnished: string
  roommate_gender: string
  lister_type: string
  available_from: string | null
  first_seen: string
  summary_fa: string
  thumb: string
  photo_count: number
}

export const SECTIONS = ['kind', 'property_type', 'rental_mode', 'furnished', 'roommate_gender', 'lister_type', 'area', 'rooms', 'price'] as const
export type Section = (typeof SECTIONS)[number]
export type Filters = Partial<Record<Section, string[]>>

const PRICE_BUCKETS: [string, number, number][] = [
  ['<800', 0, 800],
  ['800-1200', 800, 1200],
  ['1200-1600', 1200, 1600],
  ['1600-2200', 1600, 2200],
  ['2200+', 2200, Infinity],
]

function valueOf(l: Listing, s: Section): string {
  if (s === 'rooms') return l.rooms == null ? 'none' : String(Math.min(Math.floor(l.rooms), 4))
  if (s === 'price') {
    if (l.price_amount == null) return 'none'
    for (const [name, lo, hi] of PRICE_BUCKETS) if (l.price_amount >= lo && l.price_amount < hi) return name
    return 'none'
  }
  return (l as any)[s]
}

function matches(l: Listing, f: Filters, skip?: Section) {
  for (const s of SECTIONS) {
    if (s === skip) continue
    const want = f[s]
    if (want && want.length && !want.includes(valueOf(l, s))) return false
  }
  return true
}

export function applyFilters(all: Listing[], f: Filters) {
  return all
    .filter((l) => matches(l, f))
    .sort((a, b) => (a.first_seen < b.first_seen ? 1 : a.first_seen > b.first_seen ? -1 : b.id - a.id))
}

export function facetCounts(all: Listing[], f: Filters) {
  const out: Record<string, Record<string, number>> = {}
  for (const s of SECTIONS) {
    const counts: Record<string, number> = {}
    for (const l of all) {
      if (!matches(l, f, s)) continue
      const v = valueOf(l, s)
      counts[v] = (counts[v] ?? 0) + 1
    }
    out[s] = counts
  }
  return out
}
