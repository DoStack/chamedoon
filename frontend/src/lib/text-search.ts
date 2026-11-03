import { useQuery } from '@tanstack/react-query'
import type { Card, SearchAreaLabel, SearchParse, SearchRecent, SearchSpec } from '@/api/types'
import { meFetch, unwrap } from './api'
import { getMeta } from './access'
import { fa, formatDate } from './format'
import { appliedChips, readView, writeView, type Chip, type Tile, type View } from './listing-view'

export const PARSE_URL = '/api/v1/me/search/parse/'
export const RECENT_URL = '/api/v1/me/search/recent/'
export const RESTORE_URL = '/api/v1/me/search/recent/restore/'

// The server stops at 6 seconds; this leaves room for the trip.
const PARSE_TIMEOUT_MS = 9_000

export function textSearchOn() {
  return Boolean(getMeta()?.text_search_on)
}

export type Size = { min: number | null; max: number | null }

// What a search shows: the list's own filters, plus the size the list has no filter for yet and the names of its areas.
export type SearchView = { view: View; size: Size; labels: SearchAreaLabel[] }

export function fromSpec(spec: SearchSpec, labels: SearchAreaLabel[]): SearchView {
  return {
    view: {
      ...readView(new URLSearchParams()),
      kind: spec.kind === 'wanted' ? 'wanted' : 'offer',
      types: spec.types,
      min: spec.min,
      max: spec.max,
      area_ids: spec.area_ids,
      q: spec.q,
      term: spec.term || null,
      furnished: spec.furnished,
      bills: spec.bills,
      owner: spec.owner,
      warm: spec.warm,
      reg: spec.reg,
      nowbs: spec.nowbs,
      gender: spec.gender || null,
      move: spec.move || null,
      by: spec.by || null,
    },
    size: { min: spec.size_min, max: spec.size_max },
    labels,
  }
}

// A card with no size stays, as a card with no price does under a budget.
export function fitsSize(card: Pick<Card, 'size_m2'>, size: Size) {
  if (card.size_m2 == null) return true
  if (size.min != null && card.size_m2 < size.min) return false
  if (size.max != null && card.size_m2 > size.max) return false
  return true
}

function sizeLabel(size: Size) {
  if (size.min == null || size.max == null) return size.min != null ? `از ${fa(size.min)} متر` : `تا ${fa(size.max ?? 0)} متر`
  return `${fa(Math.round((size.min + size.max) / 2))} متر`
}

export type SaidChip = { id: string; label: string; clear: (search: SearchView) => SearchView }

function onView(chip: Chip): SaidChip {
  return { id: chip.id, label: chip.label, clear: (search) => ({ ...search, view: chip.clear(search.view) }) }
}

// F08#4: every filter as its own chip, in the list's words, and the ones only a text search can set.
export function saidChips(search: SearchView, tiles: Tile[], country: string): SaidChip[] {
  const { view, size, labels } = search
  const chips = appliedChips(view, tiles, country).map(onView)
  if (size.min != null || size.max != null) chips.push({ id: 'size', label: sizeLabel(size), clear: (s) => ({ ...s, size: { min: null, max: null } }) })
  for (const id of view.area_ids) {
    const label = labels.find((row) => row.id === id)?.label ?? fa(id)
    chips.push({ id: `area-id-${id}`, label, clear: (s) => ({ ...s, view: { ...s.view, area_ids: s.view.area_ids.filter((item) => item !== id) } }) })
  }
  if (view.q) chips.push({ id: 'q', label: `نزدیک ${view.q}`, clear: (s) => ({ ...s, view: { ...s.view, q: '' } }) })
  if (view.by) chips.push({ id: 'by', label: `تا ${formatDate(view.by)}`, clear: (s) => ({ ...s, view: { ...s.view, by: null } }) })
  return chips
}

// To the list with the same filters. The list reads areas by name and has no chip for an id, nor a size filter.
export function toListParams({ view, labels }: SearchView) {
  const names = view.area_ids.map((id) => labels.find((row) => row.id === id)?.name).filter((name): name is string => Boolean(name))
  return writeView(new URLSearchParams(), { ...view, area_ids: [], areas: [...view.areas, ...names] })
}

function parseBody(text: string, country: string, city: string, rulesOnly: boolean) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, country, city, rules_only: rulesOnly }), timeoutMs: PARSE_TIMEOUT_MS }
}

export function useParse(text: string, country: string, city: string) {
  return useQuery({
    queryKey: ['search-parse', text, country, city],
    enabled: Boolean(text),
    retry: false,
    staleTime: 300_000,
    queryFn: () => unwrap(meFetch<SearchParse>(PARSE_URL, parseBody(text, country, city, false))),
  })
}

// F08#2: rules only, so typing never reaches the model or a cap.
export function usePreview(text: string, country: string, city: string) {
  return useQuery({
    queryKey: ['search-preview', text, country, city],
    enabled: text.trim().length >= 2,
    retry: false,
    staleTime: 300_000,
    queryFn: ({ signal }) => unwrap(meFetch<SearchParse>(PARSE_URL, { ...parseBody(text, country, city, true), signal })),
  })
}

export const RECENT_KEY = ['search-recent']

export function useRecents() {
  return useQuery({ queryKey: RECENT_KEY, retry: false, queryFn: () => unwrap(meFetch<SearchRecent>(RECENT_URL)) })
}

export function clearRecents() {
  return meFetch<{ cleared_at: string }>(RECENT_URL, { method: 'DELETE' })
}

export function restoreRecents(clearedAt: string) {
  return meFetch<SearchRecent>(RESTORE_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cleared_at: clearedAt }) })
}
