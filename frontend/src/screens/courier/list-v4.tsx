import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ArrowLeftRight, Bell, BellRing, CalendarDays, ChevronDown, ChevronUp, Package, Plane, Search, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { Flag } from '@/components/flag'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { useAlerts } from './alert-sheet'
import { courierFetch, unwrap } from './api'
import { query } from './filters'
import { monthLabel, nounFor, placeKey, sizeName, SIZES, upcomingMonths, type Side } from './format'
import { categoryIcon, SingleChips, SizeTiles } from './parts'
import { placeLabel, useLocations, type RouteValue } from './route-sheet'
import type { components } from '@/api/courier-schema.gen'

type Facets = components['schemas']['Facets'] & { categories?: Array<{ code: string; count: number }> }

export type V4Filters = { month: string; size: string; cat: string }

const country = (key: string) => key.split(':')[0] ?? ''

// Design v4 `bar`: the whole route in one box, then the filter icon with its count. Sticks under the header.
export function RouteBarV4({ value, filters, onOpen, onSwap, onSearch, onFilter }: { value: RouteValue; filters: number; onOpen: (side: 'from' | 'to') => void; onSwap: () => void; onSearch: () => void; onFilter: () => void }) {
  const locations = useLocations()
  const end = (side: 'from' | 'to', key: string) => (
    <button type="button" className="flex min-w-0 items-center gap-1.5 outline-none" onClick={() => onOpen(side)} aria-label={`${side === 'from' ? 'مبدا' : 'مقصد'}: ${placeLabel(key, locations.data)}`} data-testid={`route-${side}`}>
      {key ? <Flag code={country(key)} /> : null}
      <bdi className={`truncate text-sm font-bold ${key ? '' : 'text-muted-foreground'}`}>{placeLabel(key, locations.data)}</bdi>
    </button>
  )
  return (
    <div className="sticky top-0 z-10 -mx-4 flex flex-none items-center gap-2 border-b bg-card px-3 py-2 md:-mx-6 md:px-6" data-testid="route-bar-v4">
      <div className="flex h-11 min-w-0 flex-1 items-center gap-1.5 rounded-lg border px-2.5">
        {end('from', value.from)}
        <button type="button" className="flex flex-none text-muted-foreground" onClick={onSwap} aria-label="جابه‌جا کردن مبدا و مقصد" data-testid="route-swap">
          <ArrowLeftRight className="size-[18px]" />
        </button>
        {end('to', value.to)}
        <button type="button" className="ms-auto flex flex-none text-muted-foreground" onClick={onSearch} aria-label="جستجو" data-testid="route-search">
          <Search className="size-[18px]" />
        </button>
      </div>
      <Button variant="outline" size="icon" className="relative !size-11 flex-none" onClick={onFilter} aria-label="فیلترها" data-testid="filter-open">
        <SlidersHorizontal />
        {filters ? <Badge className="pointer-events-none absolute -top-1.5 -end-1.5 h-[17px] min-w-[17px] px-1 text-[10px]">{fa(filters)}</Badge> : null}
      </Button>
    </div>
  )
}

// Design v4 `seg`: travelers or packages, as a small pill switch.
export function SideSwitch({ value, onChange }: { value: Side; onChange: (side: Side) => void }) {
  const option = (side: Side, Icon: typeof Plane, label: string) => (
    <button type="button" role="tab" aria-selected={value === side} onClick={() => side !== value && onChange(side)} className={`flex items-center gap-1 rounded-full px-3 py-1.5 text-[13px] font-semibold ${value === side ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground'}`} data-testid={`side-${side}`}>
      <Icon className="size-[15px]" aria-hidden />
      {label}
    </button>
  )
  return (
    <div role="tablist" aria-label="طرف بازار" className="flex flex-none rounded-full bg-muted p-[3px]">
      {option('DEMAND', Plane, 'مسافرها')}
      {option('SUPPLY', Package, 'بسته‌ها')}
    </div>
  )
}

// Side switch, count, and the bell for this route.
export function MetaRow({ side, total, loading, draft, onSide, onAlert }: { side: Side; total: number | null; loading?: boolean; draft: { side: Side; from: string; to: string }; onSide: (side: Side) => void; onAlert: () => void }) {
  const alerts = useAlerts()
  const on = alerts.data?.alerts.some((row) => row.active && row.side === draft.side && placeKey(row.origin) === draft.from && placeKey(row.destination) === draft.to)
  const Icon = on ? BellRing : Bell
  return (
    <div className="flex min-h-10 flex-none items-center gap-2" data-testid="count-bar">
      <SideSwitch value={side} onChange={onSide} />
      {loading || total === null ? null : (
        <span className="ms-auto text-[13px] text-muted-foreground" data-testid="count">
          {fa(total)} {nounFor(side)}
        </span>
      )}
      <Button variant="ghost" size="icon" className={`${loading || total === null ? 'ms-auto' : ''} text-primary`} onClick={onAlert} aria-label={on ? 'گوش به زنگ روشن' : 'گوش به زنگ'} data-testid={on ? 'alert-button-on' : 'alert-button'}>
        <Icon />
      </Button>
    </div>
  )
}

// Active filters as accent chips, each with its own remove.
export function ActiveChips({ side, value, categoryName, onChange }: { side: Side; value: V4Filters; categoryName: (code: string) => string; onChange: (next: Partial<V4Filters>) => void }) {
  const months = upcomingMonths()
  const chip = (label: string, icon: ReactNode, clear: Partial<V4Filters>, filter: string) => (
    <button
      key={filter}
      type="button"
      className="inline-flex items-center gap-1 rounded-full bg-accent px-2.5 py-1.5 text-xs leading-none font-semibold whitespace-nowrap text-accent-foreground"
      onClick={() => {
        track('courier_filter_change', { filter, value: null, side, surface: 'chip' })
        onChange(clear)
      }}
      aria-label={`حذف فیلتر ${label}`}
      data-testid="filter-chip"
    >
      {icon}
      {label}
      <X className="size-3.5" aria-hidden />
    </button>
  )
  if (!value.month && !value.size && !value.cat) return null
  const CatIcon = categoryIcon(value.cat)
  return (
    <div className="flex flex-none flex-wrap gap-1.5" data-testid="filter-row">
      {value.month ? chip(monthLabel(value.month, value.month === months[0]), <CalendarDays className="size-3.5" aria-hidden />, { month: '' }, 'month') : null}
      {value.cat ? chip(categoryName(value.cat), <CatIcon className="size-3.5" aria-hidden />, { cat: '' }, 'category') : null}
      {value.size ? chip(sizeName(side, value.size), null, { size: '' }, 'size') : null}
    </div>
  )
}

function Section({ title, summary, open, onToggle, children, testId }: { title: string; summary: string; open: boolean; onToggle: () => void; children: ReactNode; testId: string }) {
  const Chevron = open ? ChevronUp : ChevronDown
  return (
    <div className="flex flex-col gap-2.5 border-b pb-3 last:border-b-0" data-testid={testId}>
      <button type="button" className="flex items-center justify-between text-[15px] font-bold" onClick={onToggle} aria-expanded={open}>
        {title}
        <span className={`flex items-center gap-1 text-[13px] font-semibold ${summary ? 'text-primary' : 'text-muted-foreground'}`}>
          {summary || 'همه'}
          <Chevron className="size-4" aria-hidden />
        </span>
      </button>
      {open ? children : null}
    </div>
  )
}

// Design v4 filter sheet: when, what it carries, how much room; each section folds and shows live counts.
export function FilterSheetV4({ open, side, from, to, value, categories, onClose, onApply }: { open: boolean; side: Side; from: string; to: string; value: V4Filters; categories: Array<{ code: string; name_fa: string }>; onClose: () => void; onApply: (next: V4Filters) => void }) {
  const [draft, setDraft] = useState(value)
  const [shown, setShown] = useState({ month: true, cat: true, size: false })
  useEffect(() => {
    if (open) {
      setDraft(value)
      setShown({ month: true, cat: true, size: Boolean(value.size) })
      track('courier_filter_sheet_open', { kind: 'all', side, v4: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const facets = useQuery({
    queryKey: ['courier', 'facets', side, from, to, draft.month, draft.size, draft.cat],
    queryFn: () => unwrap(courierFetch<Facets>(`requests/facets/${query({ side, origin: from, destination: to, month: draft.month, size: draft.size, category: draft.cat })}`)),
    enabled: open,
    placeholderData: keepPreviousData,
    retry: false,
  })
  useEffect(() => {
    if (facets.isError) track('courier_filter_counts_failed', { side })
  }, [facets.isError, side])
  const data = facets.isError ? undefined : facets.data
  const months = upcomingMonths().map((key, index) => ({ value: key, label: monthLabel(key, index === 0), count: data ? (data.months.find((row) => row.key === key)?.count ?? 0) : null }))
  const sizes = SIZES[side].map((row) => ({ code: row.code, label: row.label, count: data ? (data.sizes.find((item) => item.code === row.code)?.count ?? 0) : null }))
  const cats = categories.map((row) => ({ value: row.code, label: row.name_fa, count: data?.categories ? (data.categories.find((item) => item.code === row.code)?.count ?? 0) : null }))
  const change = (filter: keyof V4Filters, next: string) => {
    track('courier_filter_change', { filter: filter === 'cat' ? 'category' : filter, value: next || null, side, surface: 'sheet' })
    setDraft({ ...draft, [filter]: next })
  }
  const months0 = upcomingMonths()[0]
  return (
    <AppSheet
      open={open}
      onClose={onClose}
      label="فیلترها"
      testId="filter-sheet"
      full
      title={
        <span className="flex items-center gap-3">
          فیلترها
          <button
            type="button"
            className="text-[13px] font-bold text-primary"
            onClick={() => {
              track('courier_filter_clear', { surface: 'sheet', side })
              setDraft({ month: '', size: '', cat: '' })
            }}
            data-testid="filter-clear"
          >
            پاک کردن
          </button>
        </span>
      }
      foot={
        <Button
          className="w-full"
          size="lg"
          onClick={() => {
            track('courier_filter_apply', { kind: 'all', side, month: draft.month || null, size: draft.size || null, category: draft.cat || null, total: data?.total ?? null })
            onApply(draft)
          }}
          data-testid="filter-apply"
        >
          {data ? `نمایش ${fa(data.total)} ${nounFor(side)}` : 'نمایش نتایج'}
        </Button>
      }
    >
      <Section title="کی؟" summary={draft.month ? monthLabel(draft.month, draft.month === months0) : ''} open={shown.month} onToggle={() => setShown({ ...shown, month: !shown.month })} testId="filter-when">
        <SingleChips label="زمان" value={draft.month} options={months} onChange={(next) => change('month', next)} testId="filter-months" />
      </Section>
      {categories.length ? (
        <Section title={side === 'DEMAND' ? 'چی می‌بره؟' : 'داخل بسته'} summary={categories.find((row) => row.code === draft.cat)?.name_fa ?? ''} open={shown.cat} onToggle={() => setShown({ ...shown, cat: !shown.cat })} testId="filter-what">
          <SingleChips label="دسته" value={draft.cat} options={cats} onChange={(next) => change('cat', next)} testId="filter-categories" />
        </Section>
      ) : null}
      <Section title={side === 'DEMAND' ? 'چقدر جا؟' : 'اندازه'} summary={draft.size ? sizeName(side, draft.size) : ''} open={shown.size} onToggle={() => setShown({ ...shown, size: !shown.size })} testId="filter-size">
        <SizeTiles value={draft.size} options={sizes} trip={side === 'SUPPLY'} cols={4} onChange={(next) => change('size', next)} testId="filter-sizes" />
      </Section>
    </AppSheet>
  )
}
