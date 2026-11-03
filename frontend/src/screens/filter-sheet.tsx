import { BedDouble, BedSingle, Building, Calendar, ChevronDown, DoorOpen, House, LayoutGrid, Search, Sofa, Warehouse, type LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Card } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { Button } from '@/components/ui/button'
import { FieldError } from '@/components/ui/field'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Slider } from '@/components/ui/slider'
import { Switch } from '@/components/ui/switch'
import { Toggle } from '@/components/ui/toggle'
import { currency, fa } from '@/lib/format'
import { areasByCount, budgetLabel, clearFilters, countMatches, GENDERS, MODES, tileOf, typeTiles, waysOut, WINDOWS, type Tile, type View } from '@/lib/listing-view'
import { track } from '@/lib/report'
import { cn } from '@/lib/utils'
import { AreaSheet } from './area-sheet'

export type FilterSection = 'type' | 'budget' | 'mode' | 'reg'

const ICONS: Record<Tile['icon'], LucideIcon> = { all: LayoutGrid, room: DoorOpen, studio: Sofa, base: Warehouse, one: BedSingle, two: BedDouble, three: Building, house: House, apartment: Building, wanted: Search }
const ON = 'data-[state=on]:border-primary data-[state=on]:bg-accent data-[state=on]:text-accent-foreground'
const TOP_AREAS = 5
const BARS = 16

function Section({ title, side, children, anchor }: { title: string; side?: ReactNode; children: ReactNode; anchor?: React.Ref<HTMLElement> }) {
  return (
    <section ref={anchor} className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">{title}</h3>
        {side ? <span className="text-[13px] text-muted-foreground">{side}</span> : null}
      </div>
      {children}
    </section>
  )
}

export function Chip({ label, count, on, onClick, testId }: { label: string; count?: number; on: boolean; onClick: () => void; testId?: string }) {
  // A count of 0 disables the option unless it is selected (BRIEF).
  return (
    <Toggle variant="outline" pressed={on} disabled={count === 0 && !on} onPressedChange={onClick} data-testid={testId} className={cn('gap-1.5 px-3 text-sm', ON)}>
      {label}
      {count !== undefined ? <span className="text-xs font-normal">{fa(count)}</span> : null}
    </Toggle>
  )
}

function SwitchRow({ label, count, checked, onChange, description }: { label: string; count?: number; checked: boolean; onChange: (next: boolean) => void; description?: string }) {
  return (
    <label className="flex min-h-11 items-center justify-between gap-3">
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-semibold">
          {label} {count !== undefined && !description ? <span className="text-[13px] font-normal text-muted-foreground">{fa(count)}</span> : null}
        </span>
        {description ? <span className="text-[13px] text-muted-foreground">{description}</span> : null}
      </span>
      <Switch dir="rtl" aria-label={label} checked={checked} onCheckedChange={onChange} />
    </label>
  )
}

const LATIN = '۰۱۲۳۴۵۶۷۸۹'

function parseAmount(text: string) {
  const digits = text.replace(/[۰-۹]/g, (digit) => String(LATIN.indexOf(digit))).replace(/\D/g, '')
  return digits ? Number(digits) : null
}

function Bound({ prefix, value, invalid, onChange }: { prefix: string; value: number | null; invalid?: boolean; onChange: (next: number | null) => void }) {
  return (
    <div className="min-w-0 flex-1">
      <InputGroup>
        <InputGroupAddon>{prefix}</InputGroupAddon>
        <InputGroupInput aria-label={prefix} inputMode="numeric" aria-invalid={invalid} value={value == null ? '' : fa(value)} onChange={(event) => onChange(parseAmount(event.target.value))} />
      </InputGroup>
    </div>
  )
}

// F05, F06: one sheet. Each section counts from the other sections' filters, so a choice never changes its own section.
export function FilterSheet({ open, focus, onClose, cards, view, country, region, onApply }: { open: boolean; focus: FilterSection | null; onClose: () => void; cards: Card[]; view: View; country: string; region: string; onApply: (next: View) => void }) {
  const [draft, setDraft] = useState(view)
  const [areasOpen, setAreasOpen] = useState(false)
  const modeRef = useRef<HTMLElement>(null)
  const budgetRef = useRef<HTMLElement>(null)
  const german = country === 'DE'
  const wanted = draft.kind === 'wanted'

  useEffect(() => {
    if (!open) return
    setDraft(view)
    track('filter_open', { focus })
    const target = focus === 'budget' ? budgetRef : focus === 'mode' || focus === 'reg' ? modeRef : null
    if (target) setTimeout(() => target.current?.scrollIntoView({ block: 'start' }), 100)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const change = (patch: Partial<View>) => {
    track('filter_changed', { keys: Object.keys(patch) })
    setDraft((current) => ({ ...current, ...patch }))
  }
  const count = (patch: Partial<View>) => countMatches(cards, { ...draft, ...patch })

  const tiles = useMemo(() => typeTiles(country, region), [country, region])
  const active = tileOf(tiles, draft)

  // Bars and slider cover this city's real prices, without this sheet's own budget.
  const prices = useMemo(() => cards.filter((card) => card.kind === draft.kind).map((card) => (wanted ? card.price_max : card.price_monthly)).filter((price): price is number => price != null), [cards, draft.kind, wanted])
  const low = prices.length ? Math.floor(Math.min(...prices) / 50) * 50 : 0
  const high = prices.length ? Math.ceil(Math.max(...prices) / 50) * 50 : 0
  const inBudget = useMemo(() => cards.filter((card) => {
    const price = wanted ? card.price_max : card.price_monthly
    return price != null && countMatches([card], { ...draft, min: null, max: null, unpriced: true }) > 0
  }).map((card) => (wanted ? card.price_max! : card.price_monthly!)), [cards, draft, wanted])
  const bars = useMemo(() => {
    const width = (high - low) / BARS || 1
    const totals = Array.from({ length: BARS }, () => 0)
    for (const price of inBudget) totals[Math.min(BARS - 1, Math.floor((price - low) / width))] += 1
    const tallest = Math.max(1, ...totals)
    return totals.map((total, index) => ({ height: Math.max(6, (total / tallest) * 100), from: low + index * width, to: low + (index + 1) * width }))
  }, [inBudget, low, high])

  const budgetError = draft.min != null && draft.max != null && draft.min > draft.max
  const unpricedCount = cards.reduce((sum, card) => sum + ((wanted ? card.price_max : card.price_monthly) == null && countMatches([card], { ...draft, min: null, max: null, unpriced: true }) > 0 ? 1 : 0), 0)

  const areaRows = useMemo(() => areasByCount(cards, draft.kind), [cards, draft.kind])
  const noAreas = { areas: [] as string[] }

  const total = budgetError ? 0 : countMatches(cards, draft)
  const ways = !budgetError && total === 0 ? waysOut(cards, draft, high) : []
  useEffect(() => {
    if (open && total === 0 && !budgetError) track('filter_no_result')
    if (open && budgetError) track('filter_error')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, total, budgetError])

  const money = currency(cards[0]?.price_currency ?? (german ? 'EUR' : 'CAD'))
  const budgetSide = budgetError ? '' : draft.min != null || draft.max != null ? `${budgetLabel(draft)} ${money}` : 'همه‌ی قیمت‌ها'

  const foot = (
    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={() => setDraft(clearFilters(draft))}>
        پاک کردن همه
      </Button>
      <Button
        className="min-w-0 flex-1"
        disabled={budgetError || total === 0}
        onClick={() => {
          onApply(draft)
          onClose()
        }}
      >
        {budgetError ? 'نمایش آگهی‌ها' : `نمایش ${fa(total)} آگهی`}
      </Button>
    </div>
  )

  return (
    <>
      <AppSheet open={open && !areasOpen} full onClose={onClose} label="فیلترها" title="فیلترها" foot={foot} testId="filter-sheet">
        <Section title="نوع خانه">
          <div className={cn('grid gap-2', tiles.length > 8 ? 'grid-cols-5' : 'grid-cols-4')} data-testid="type-tiles">
            {tiles.map((item) => {
              const Icon = ICONS[item.icon]
              const n = count(item.patch)
              const on = active?.id === item.id
              return (
                <Toggle key={item.id} variant="outline" pressed={on} disabled={n === 0 && !on} aria-label={item.label} onPressedChange={() => change(item.patch)} className={cn('h-[76px] w-full flex-col gap-0.5 px-1 text-xs font-semibold', ON)}>
                  <Icon className="size-[22px]" aria-hidden />
                  <span>{item.label}</span>
                  <span className="text-xs font-normal">{fa(n)}</span>
                </Toggle>
              )
            })}
          </div>
        </Section>

        <Section title="بودجه‌ی ماهانه" side={<bdi>{budgetSide}</bdi>} anchor={budgetRef}>
          {prices.length && !budgetError ? (
            <>
              <div aria-hidden className="flex h-10 items-end gap-[3px]" data-testid="histogram">
                {bars.map((bar) => {
                  const on = (draft.min == null || bar.to > draft.min) && (draft.max == null || bar.from < draft.max)
                  return <i key={bar.from} className={cn('flex-1 rounded-t-[3px]', on ? 'bg-chart-1' : 'bg-chart-2')} style={{ height: `${bar.height}%` }} />
                })}
              </div>
              <Slider dir="rtl" aria-label="بازه" min={low} max={high} step={50} value={[draft.min ?? low, draft.max ?? high]} onValueChange={([from, to]) => change({ min: from === low ? null : from, max: to === high ? null : to })} />
            </>
          ) : null}
          <div className="flex items-center gap-2">
            <Bound prefix="از" value={draft.min} invalid={budgetError} onChange={(min) => change({ min })} />
            <Bound prefix="تا" value={draft.max} onChange={(max) => change({ max })} />
          </div>
          {budgetError ? <FieldError>حداقل از حداکثر بیشتر است</FieldError> : null}
          {unpricedCount > 0 ? <SwitchRow label="آگهی‌های بدون قیمت هم بیاید" count={unpricedCount} checked={draft.unpriced} onChange={(unpriced) => change({ unpriced })} /> : null}
          {german && !wanted ? <SwitchRow label="اجاره‌ی سرد نباشد" count={count({ warm: true })} checked={draft.warm} onChange={(warm) => change({ warm })} /> : null}
        </Section>

        {areaRows.length ? (
          <Section title="محله">
            <div className="flex flex-wrap items-center gap-2" data-testid="area-chips">
              <Chip label="همه‌جا" on={draft.areas.length === 0} onClick={() => change(noAreas)} />
              {areaRows.slice(0, TOP_AREAS).map((name) => (
                <Chip key={name} label={name} count={count({ areas: [name] })} on={draft.areas.includes(name)} onClick={() => change({ areas: draft.areas.includes(name) ? draft.areas.filter((item) => item !== name) : [...draft.areas, name] })} />
              ))}
              {areaRows.length > TOP_AREAS ? (
                <Button variant="ghost" size="sm" onClick={() => setAreasOpen(true)}>
                  بیشتر
                  <ChevronDown />
                </Button>
              ) : null}
            </div>
          </Section>
        ) : null}

        {ways.length ? (
          <div className="flex flex-wrap items-center gap-2" data-testid="ways-out">
            {ways.map((way) => (
              <Button key={way.label} variant="outline" size="sm" onClick={() => setDraft(way.view)}>
                {way.label}
              </Button>
            ))}
          </div>
        ) : null}

        {wanted ? null : (
          <>
            <Section title="نوع اجاره" anchor={modeRef}>
              <div className="flex flex-wrap gap-2" data-testid="mode-chips">
                {(MODES[country] ?? MODES.CA).map((item) => (
                  <Chip key={item.id} label={item.label} count={count({ mode: item.id })} on={draft.mode === item.id} onClick={() => change({ mode: draft.mode === item.id ? null : item.id })} />
                ))}
              </div>
            </Section>
            <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
              <SwitchRow label="مبله" count={count({ furnished: true })} checked={draft.furnished} onChange={(furnished) => change({ furnished })} />
              {german ? <SwitchRow label="ملد (Anmeldung) ممکن باشد" count={count({ reg: true })} checked={draft.reg} onChange={(reg) => change({ reg })} /> : <SwitchRow label="قبوض داخل قیمت" count={count({ bills: true })} checked={draft.bills} onChange={(bills) => change({ bills })} />}
              <SwitchRow label="فقط مالک" count={count({ owner: true })} checked={draft.owner} onChange={(owner) => change({ owner })} />
            </div>
            <Section title="هم‌خانه">
              <div className="flex flex-wrap gap-2" data-testid="gender-chips">
                {GENDERS.map((item) => (
                  <Chip key={item.label} label={item.label} count={count({ gender: item.id })} on={draft.gender === item.id} onClick={() => change({ gender: item.id })} />
                ))}
              </div>
            </Section>
            <Section title="تاریخ ورود">
              <InputGroup>
                <InputGroupAddon>
                  <Calendar />
                  از
                </InputGroupAddon>
                <InputGroupInput type="date" dir="ltr" aria-label="تاریخ ورود" value={draft.from ?? ''} onChange={(event) => change({ from: event.target.value || null })} />
              </InputGroup>
              {draft.from ? (
                <div className="flex flex-wrap gap-2" data-testid="window-chips">
                  {WINDOWS.map((item) => (
                    <Chip key={item.days} label={item.label} count={count({ window: item.days })} on={draft.window === item.days} onClick={() => change({ window: item.days })} />
                  ))}
                </div>
              ) : null}
            </Section>
          </>
        )}
      </AppSheet>
      {areasOpen ? <AreaSheet open onClose={() => setAreasOpen(false)} cards={cards} view={draft} onApply={(areas) => change({ areas })} /> : null}
    </>
  )
}
