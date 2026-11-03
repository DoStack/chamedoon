import type { AlertFilters, AlertRow, Cities } from '@/api/types'
import { findCity } from './city'
import { appliedChips, readView, typeTiles, type Chip, type View } from './listing-view'
import { t } from './locale'

// What an alert keeps of the list's view. Text search, parts of a metro, sort and a map box are not saved (F15 rules).
export function filtersOfView(view: View): AlertFilters {
  const out: AlertFilters = {}
  if (view.types.length) out.types = view.types
  if (view.min != null) out.min = view.min
  if (view.max != null) out.max = view.max
  if (view.min != null || view.max != null) out.unpriced = view.unpriced
  if (view.areas.length) out.areas = view.areas
  if (view.area_ids.length) out.area_ids = view.area_ids
  if (view.term) out.term = view.term as AlertFilters['term']
  if (view.gender) out.gender = view.gender as AlertFilters['gender']
  if (view.move) out.move = view.move
  if (view.by) out.by = view.by
  for (const flag of ['furnished', 'owner', 'bills', 'warm', 'reg', 'nowbs'] as const) if (view[flag]) out[flag] = true
  return out
}

export function viewOfFilters(filters: AlertFilters): View {
  return {
    ...readView(new URLSearchParams()),
    types: filters.types ?? [],
    min: filters.min ?? null,
    max: filters.max ?? null,
    unpriced: filters.unpriced ?? false,
    areas: filters.areas ?? [],
    area_ids: filters.area_ids ?? [],
    term: filters.term || null,
    gender: filters.gender || null,
    move: filters.move ?? null,
    by: filters.by ?? null,
    furnished: Boolean(filters.furnished),
    owner: Boolean(filters.owner),
    bills: Boolean(filters.bills),
    warm: Boolean(filters.warm),
    reg: Boolean(filters.reg),
    nowbs: Boolean(filters.nowbs),
  }
}

export function filterChips(view: View, country: string): Chip[] {
  return appliedChips(view, typeTiles(country, ''), country)
}

const isType = (chip: Chip) => chip.id === 'type' || chip.id.startsWith('type-')
const isArea = (chip: Chip) => chip.id.startsWith('area-')

// «۱ خوابه · نورث یورک · تا ۲٬۰۰۰», and the rest under it: «تورنتو · مبله». Only a city: F15#10.
export function alertName(view: View, country: string, cityName: string) {
  const chips = filterChips(view, country)
  const labels = (test: (chip: Chip) => boolean) => chips.filter(test).map((chip) => chip.label)
  const title = [labels(isType).join(' یا '), labels(isArea).join('، '), ...labels((chip) => chip.id === 'budget'), ...labels((chip) => chip.id === 'term')].filter(Boolean).join(' · ')
  const rest = labels((chip) => !isType(chip) && !isArea(chip) && chip.id !== 'budget' && chip.id !== 'term')
  return { title: title || t('همه‌ی آگهی‌های {city}', { city: cityName }), sub: [cityName, ...rest].join(' · ') }
}

export function rowName(row: AlertRow, cities: Cities | undefined) {
  const found = cities ? findCity(cities, row.city_slug) : null
  return alertName(viewOfFilters(row.filters), row.country, found?.row.name_fa ?? row.city)
}

function canonical(filters: AlertFilters) {
  const { areas, area_ids, ...rest } = filters
  const entries = Object.entries({ ...rest, ...(area_ids?.length ? { area_ids: [...area_ids].sort() } : { areas: [...(areas ?? [])].sort() }) })
    .filter(([, value]) => value !== null && value !== undefined && value !== '' && value !== false && !(Array.isArray(value) && value.length === 0))
    .map(([key, value]) => [key, Array.isArray(value) ? [...value].map(String).sort() : value] as const)
    .sort(([a], [b]) => a.localeCompare(b))
  return JSON.stringify(entries)
}

// The chip reads «روشن» when this very search is already on. The server decides for real when saving (F15#5).
export function sameSearch(a: AlertFilters, b: AlertFilters) {
  return canonical(a) === canonical(b)
}
