import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useDragScroll } from '@/lib/use-drag-scroll'
import { ChevronDown, SlidersHorizontal, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { courierFetch, unwrap } from './api'
import { monthLabel, nounFor, sizeName, SIZES, upcomingMonths, type Side } from './format'
import { Section, SingleChips, SizeTiles } from './parts'
import type { components } from '@/api/courier-schema.gen'

type Facets = components['schemas']['Facets']

export type Filters = { month: string; size: string }
export type SheetKind = 'all' | 'month' | 'size'

export function query(params: Record<string, string | number | undefined>) {
  const out = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') out.set(key, String(value))
  const text = out.toString()
  return text ? `?${text}` : ''
}

// C04: live counts for what is drafted, not yet applied. A failed count takes away the numbers, not the options.
export function FilterSheet({ open, kind, side, from, to, value, onClose, onApply }: { open: boolean; kind: SheetKind; side: Side; from: string; to: string; value: Filters; onClose: () => void; onApply: (next: Filters) => void }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => {
    if (open) {
      setDraft(value)
      track('courier_filter_sheet_open', { kind, side })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const facets = useQuery({
    queryKey: ['courier', 'facets', side, from, to, draft.month, draft.size],
    queryFn: () => unwrap(courierFetch<Facets>(`requests/facets/${query({ side, origin: from, destination: to, month: draft.month, size: draft.size })}`)),
    enabled: open,
    placeholderData: keepPreviousData,
    retry: false,
  })
  useEffect(() => {
    if (facets.isError) {
      track('courier.c04.facets_failed')
      track('courier_filter_counts_failed', { side })
    }
  }, [facets.isError])
  const data = facets.isError ? undefined : facets.data
  const months = upcomingMonths().map((key, index) => ({ value: key, label: monthLabel(key, index === 0), count: data ? (data.months.find((row) => row.key === key)?.count ?? 0) : null }))
  const sizes = SIZES[side].map((row) => ({ code: row.code, label: row.label, count: data ? (data.sizes.find((item) => item.code === row.code)?.count ?? 0) : null }))
  const showMonths = kind !== 'size'
  const showSizes = kind !== 'month'
  const title = kind === 'all' ? 'فیلترها' : kind === 'month' ? 'زمان' : 'اندازه'
  const apply = (
    <Button
      className="w-full"
      onClick={() => {
        track('courier_filter_apply', { kind, side, month: draft.month || null, size: draft.size || null, total: data?.total ?? null })
        onApply(draft)
      }}
      data-testid="filter-apply"
    >
      {data ? `نمایش ${fa(data.total)} ${nounFor(side)}` : 'نمایش نتایج'}
    </Button>
  )
  return (
    <AppSheet
      open={open}
      onClose={onClose}
      label={title}
      title={title}
      testId="filter-sheet"
      full={kind === 'all'}
      foot={
        kind === 'all' ? (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              className="flex-1"
              onClick={() => {
                track('courier_filter_clear', { surface: 'sheet', side })
                setDraft({ month: '', size: '' })
              }}
              data-testid="filter-clear"
            >
              پاک کردن
            </Button>
            <div className="flex-1">{apply}</div>
          </div>
        ) : (
          apply
        )
      }
    >
      {showMonths ? (
        kind === 'all' ? (
          <Section title="زمان">
            <SingleChips label="زمان" value={draft.month} options={months} onChange={(month) => (track('courier_filter_change', { filter: 'month', value: month || null, side, surface: 'sheet' }), setDraft({ ...draft, month }))} testId="filter-months" />
          </Section>
        ) : (
          <SingleChips label="زمان" value={draft.month} options={months} onChange={(month) => (track('courier_filter_change', { filter: 'month', value: month || null, side, surface: 'sheet' }), setDraft({ ...draft, month }))} testId="filter-months" />
        )
      ) : null}
      {kind === 'all' ? <Separator /> : null}
      {showSizes ? (
        kind === 'all' ? (
          <Section title="اندازه">
            <SizeTiles value={draft.size} options={sizes} trip={side === 'SUPPLY'} onChange={(size) => (track('courier_filter_change', { filter: 'size', value: size || null, side, surface: 'sheet' }), setDraft({ ...draft, size }))} testId="filter-sizes" />
          </Section>
        ) : (
          <SizeTiles value={draft.size} options={sizes} trip={side === 'SUPPLY'} onChange={(size) => (track('courier_filter_change', { filter: 'size', value: size || null, side, surface: 'sheet' }), setDraft({ ...draft, size }))} testId="filter-sizes" />
        )
      ) : null}
    </AppSheet>
  )
}

// Always visible, in the same order. Active filters become chips, each with its own remove.
export function FilterRow({ side, value, onOpen, onChange }: { side: Side; value: Filters; onOpen: (kind: SheetKind) => void; onChange: (next: Filters) => void }) {
  const dragRef = useDragScroll()
  const active = value.month || value.size
  const months = upcomingMonths()
  const chip = (label: string, clear: Filters, filter: 'month' | 'size') => (
    <Button
      key={label}
      variant="secondary"
      size="sm"
      onClick={() => {
        track('courier_filter_change', { filter, value: null, side, surface: 'chip' })
        onChange(clear)
      }}
      aria-label={`حذف فیلتر ${label}`}
      data-testid="filter-chip"
    >
      {label}
      <X />
    </Button>
  )
  return (
    <div ref={dragRef} className="flex flex-none items-center gap-2 overflow-x-auto whitespace-nowrap" data-testid="filter-row">
      <Button variant="outline" size="sm" onClick={() => onOpen('all')} data-testid="filter-open">
        <SlidersHorizontal />
        فیلتر
      </Button>
      {active ? (
        <>
          {value.month ? chip(monthLabel(value.month, value.month === months[0]), { ...value, month: '' }, 'month') : null}
          {value.size ? chip(sizeName(side, value.size), { ...value, size: '' }, 'size') : null}
        </>
      ) : (
        <>
          <Button variant="outline" size="sm" onClick={() => onOpen('month')} data-testid="filter-open-month">
            زمان
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => onOpen('size')} data-testid="filter-open-size">
            اندازه
            <ChevronDown />
          </Button>
        </>
      )}
    </div>
  )
}
