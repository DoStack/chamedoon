import { ArrowLeft, Backpack, BookOpen, Cigarette, FileText, Gift, Infinity, Luggage, Package, PawPrint, Pill, Plane, Scale, Shirt, ShieldCheck, Smartphone, Utensils, Weight, Wine, type LucideIcon } from 'lucide-react'
import { type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Flag } from '@/components/flag'
import { dualDate, fa } from '@/lib/format'
import { ageLabel, isTrip, sizeLabel, stopsLabel, trustLabel, tripSpace } from './format'
import type { RequestCard as Row } from './types'

export function KindBadge({ card }: { card: Pick<Row, 'type'> }) {
  return isTrip(card) ? (
    <Badge variant="outline">
      <Plane />
      مسافر
    </Badge>
  ) : (
    <Badge variant="outline">
      <Package />
      بسته
    </Badge>
  )
}

export function TrustBadge({ count }: { count: number }) {
  return count > 0 ? (
    <Badge variant="secondary" data-testid="trust-badge">
      <ShieldCheck />
      {trustLabel(count)}
    </Badge>
  ) : null
}

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  DOCUMENTS: FileText,
  CLOTHES: Shirt,
  PERSONAL_ITEMS: Backpack,
  ELECTRONICS: Smartphone,
  FOOD: Utensils,
  MEDICINE: Pill,
  CIGARETTES: Cigarette,
  FRAGILE: Wine,
  PET: PawPrint,
  BOOKS: BookOpen,
  GIFTS: Gift,
}

export const categoryIcon = (code: string): LucideIcon => CATEGORY_ICONS[code] ?? Package

type Tone = 'ok' | 'no' | 'on' | 'plain'
const TONES: Record<Tone, string> = {
  ok: 'border-transparent bg-ok-soft text-ok',
  no: 'border-transparent bg-no-soft text-no',
  on: 'border-transparent bg-accent text-accent-foreground',
  plain: 'border-border bg-card',
}

// `chip` in the design: small pill with an icon. ok = carries, no = refuses, on = chosen.
export function Chip({ icon: Icon, tone = 'plain', children, testId }: { icon?: LucideIcon; tone?: Tone; children: ReactNode; testId?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs leading-none font-semibold whitespace-nowrap ${TONES[tone]}`} data-testid={testId}>
      {Icon ? <Icon className="size-3.5 shrink-0" aria-hidden /> : null}
      {children}
    </span>
  )
}

// The date tile: Gregorian day and month, Jalali under it. Blue for a flight, orange for a package's deadline.
export function DateTile({ iso, pkg }: { iso: string | null | undefined; pkg?: boolean }) {
  const date = iso ? dualDate(iso) : null
  return (
    <div className={`flex w-[50px] flex-none flex-col items-center rounded-xl py-1.5 text-center leading-[1.1] ${pkg ? 'bg-accent text-accent-foreground' : 'bg-navy-soft text-navy'}`} data-testid="date-tile">
      {date ? (
        <>
          <b className="text-xl font-black">{date.day}</b>
          <span className="text-[11px] font-bold">{date.month}</span>
          <em className="text-[10px] not-italic opacity-75">{date.jalali}</em>
        </>
      ) : (
        <>
          <Infinity className="size-[22px]" aria-hidden />
          <em className="text-[10px] not-italic opacity-75">بدون تاریخ</em>
        </>
      )}
    </div>
  )
}

// Flag, origin, arrow, flag, stops: one line, Latin names kept in order by <bdi>.
export function RouteLine({ card, className = '' }: { card: Pick<Row, 'origin' | 'stops'>; className?: string }) {
  const last = card.stops[card.stops.length - 1]
  return (
    <span className={`flex min-w-0 items-center gap-1.5 leading-none font-extrabold ${className}`}>
      <Flag code={card.origin.country} />
      <bdi className="truncate">{card.origin.name_fa}</bdi>
      <ArrowLeft className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      {last ? <Flag code={last.country} /> : null}
      <bdi className="truncate">{stopsLabel(card.stops)}</bdi>
    </span>
  )
}

export function TrustLine({ count }: { count: number }) {
  return count > 0 ? (
    <span className="flex items-center gap-1 text-xs font-bold whitespace-nowrap text-ok" data-testid="trust-badge">
      <ShieldCheck className="size-[15px]" aria-hidden />
      {trustLabel(count)}
    </span>
  ) : null
}

// `req_card` in the design: the one courier card. `status` sits at the head's end, for owners.
export function RequestCardView({ card, status, foot, matches, dim, onOpen }: { card: Row; status?: ReactNode; foot?: ReactNode; matches?: string; dim?: boolean; onOpen?: () => void }) {
  const trip = isTrip(card)
  const sub = trip ? ageLabel(card.created_at) : `${card.date ? 'باید برسد' : 'بدون عجله'} · ${ageLabel(card.created_at)}`
  return (
    <div className={`grid min-w-0 ${dim ? ' opacity-60' : ''}`} data-testid="request-card" data-id={card.id}>
      <Card
        className={`!h-full !gap-0 !rounded-2xl !p-0 ${onOpen ? ' cursor-pointer' : ''}`}
        role={onOpen ? 'link' : undefined}
        tabIndex={onOpen ? 0 : undefined}
        onClick={onOpen}
        onKeyDown={
          onOpen
            ? (event) => {
                if (event.key === 'Enter') onOpen()
              }
            : undefined
        }
      >
        <div className="flex items-center gap-2.5 px-3 pt-3 pb-2">
          <DateTile iso={card.date} pkg={!trip} />
          <div className="min-w-0 flex-1">
            <RouteLine card={card} className="text-[15px]" />
            <div className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">{sub}</div>
          </div>
          {status ? <span className="ms-auto flex-none">{status}</span> : null}
        </div>
        <div className="flex flex-wrap gap-1.5 px-3 pb-2.5">
          <Chip icon={trip ? Weight : Scale}>{trip ? (card.capacity_kg ? tripSpace(card.capacity_kg) : 'جا: نامشخص') : sizeLabel(card)}</Chip>
          {card.categories.map((row) => (
            <Chip key={row.code} icon={categoryIcon(row.code)} tone={trip ? 'ok' : 'plain'}>
              {row.name_fa}
            </Chip>
          ))}
          {card.refused.map((row) => (
            <Chip key={`no-${row.code}`} icon={categoryIcon(row.code)} tone="no">
              {row.name_fa}
            </Chip>
          ))}
        </div>
        <div className="flex items-center justify-between gap-2 border-t px-3 py-2 text-xs text-muted-foreground">
          <span>
            هزینه: <b className="text-foreground">توافقی</b>
          </span>
          <TrustLine count={card.trust_count} />
        </div>
        {matches ? <div className="border-t px-3 py-2 text-sm">{matches}</div> : null}
        {foot ? <div className="border-t px-3 py-2">{foot}</div> : null}
      </Card>
    </div>
  )
}

export function RequestSkeleton() {
  return (
    <div className="grid min-w-0" data-testid="card-skeleton">
      <Card className="!gap-2.5 !p-4">
        <div className="flex justify-between">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-4 w-16" />
        </div>
        <Skeleton className="h-4 w-[70%]" />
        <Skeleton className="h-4 w-[55%]" />
        <Skeleton className="h-3 w-24" />
      </Card>
    </div>
  )
}

export function CardGrid({ children }: { children: ReactNode }) {
  return <div className="grid auto-rows-max grid-cols-1 gap-4 pt-1 md:grid-cols-3">{children}</div>
}

export function Fact({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="flex items-center gap-1 text-[13px] text-muted-foreground">
        <Icon className="size-3.5 shrink-0" aria-hidden />
        {label}
      </span>
      <b className="text-sm font-bold break-words">{value}</b>
    </div>
  )
}

export function FactGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>
}

export function Section({ title, side, children }: { title: string; side?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-base font-bold">{title}</h2>
        {side ? <span className="text-sm text-muted-foreground">{side}</span> : null}
      </div>
      {children}
    </section>
  )
}

// Manual: by default a click fires twice in one tick, on mouse down and on focus, and each one would save.
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: Array<{ value: T; label: ReactNode }>; onChange: (value: T) => void; label: string }) {
  return (
    <Tabs value={value} dir="rtl" className="!w-full" activationMode="manual" onValueChange={(next) => onChange(next as T)}>
      <TabsList className="!w-full" aria-label={label}>
        {options.map((option) => (
          <TabsTrigger key={option.value} value={option.value}>
            {option.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  )
}

const ON = 'data-[state=on]:!border-primary data-[state=on]:!bg-primary/10 data-[state=on]:!text-primary'

// A count of 0 disables a choice unless it is selected. No count at all (facets failed, forms): every choice works.
function dead(option: { value: string; count?: number | null }, selected: boolean) {
  return option.count === 0 && !selected
}

export function ChoiceChips({ value, options, onChange, label, testId }: { value: string[]; options: Array<{ value: string; label: string; count?: number | null }>; onChange: (next: string[]) => void; label: string; testId?: string }) {
  return (
    <ToggleGroup type="multiple" dir="rtl" spacing={2} className="!flex !w-full !flex-wrap !gap-2" value={value} onValueChange={onChange} aria-label={label} data-testid={testId}>
      {options.map((option) => (
        <ToggleGroupItem key={option.value} value={option.value} variant="outline" disabled={dead(option, value.includes(option.value))} className={`!gap-1.5 !px-3 !text-sm ${ON}`}>
          {option.label}
          {option.count != null ? <span className="text-xs font-normal">{fa(option.count)}</span> : null}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

// One choice from chips. Tapping the selected one clears it unless `required`.
export function SingleChips({ value, options, onChange, label, required, testId }: { value: string; options: Array<{ value: string; label: string; count?: number | null }>; onChange: (next: string) => void; label: string; required?: boolean; testId?: string }) {
  return (
    <ToggleGroup
      type="single"
      dir="rtl"
      spacing={2}
      className="!flex !w-full !flex-wrap !gap-2"
      value={value}
      onValueChange={(next) => {
        if (next || !required) onChange(next)
      }}
      aria-label={label}
      data-testid={testId}
    >
      {options.map((option) => (
        <ToggleGroupItem key={option.value} value={option.value} variant="outline" disabled={dead(option, value === option.value)} className={`!gap-1.5 !px-3 !text-sm ${ON}`}>
          {option.label}
          {option.count != null ? <span className="text-xs font-normal">{fa(option.count)}</span> : null}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

export function SizeTiles({ value, options, onChange, trip, cols = 2, required, testId }: { value: string; options: Array<{ code: string; label: string; count?: number | null }>; onChange: (next: string) => void; trip?: boolean; cols?: 2 | 4; required?: boolean; testId?: string }) {
  const Tile = trip ? Luggage : Package
  return (
    <ToggleGroup
      type="single"
      dir="rtl"
      spacing={2}
      className={`!grid ${cols === 4 ? '!grid-cols-4' : '!grid-cols-2'} !w-full !gap-2`}
      value={value}
      onValueChange={(next) => {
        if (next || !required) onChange(next)
      }}
      aria-label="اندازه"
      data-testid={testId}
    >
      {options.map((option) => {
        const Icon = option.code === 'doc' ? FileText : Tile
        return (
          <ToggleGroupItem key={option.code} value={option.code} variant="outline" disabled={dead({ value: option.code, count: option.count }, value === option.code)} className={`!h-[76px] !w-full !flex-col !gap-0.5 !px-1 !text-xs !font-semibold ${ON}`}>
            <Icon className="!size-[22px]" aria-hidden />
            <span>{option.label}</span>
            {option.count != null ? <span className="text-xs font-normal">{fa(option.count)}</span> : null}
          </ToggleGroupItem>
        )
      })}
    </ToggleGroup>
  )
}
