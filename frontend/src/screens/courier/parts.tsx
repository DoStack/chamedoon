import { Ban, FileText, Luggage, Package, Plane, Route, Scale, ShieldCheck, type LucideIcon } from 'lucide-react'
import { type ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { fa } from '@/lib/format'
import { ageLabel, categoriesLabel, dateLabel, isTrip, routeLabel, sizeLabel, trustLabel } from './format'
import type { RequestCard as Row } from './types'

function Line({ icon: Icon, children, muted, small }: { icon: LucideIcon; children: ReactNode; muted?: boolean; small?: boolean }) {
  return (
    <div className={`flex min-w-0 items-center gap-1.5 ${small ? 'text-[13px]' : 'text-sm'} leading-[22px]${muted ? ' text-muted-foreground' : ''}`}>
      <Icon className="size-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 truncate">{children}</span>
    </div>
  )
}

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

// `req_card` in the design: the one courier card. `status` replaces the trust slot, for owners.
export function RequestCardView({ card, status, foot, matches, dim, onOpen }: { card: Row; status?: ReactNode; foot?: ReactNode; matches?: string; dim?: boolean; onOpen?: () => void }) {
  const HeadIcon = isTrip(card) ? Plane : Package
  const SizeIcon = isTrip(card) ? Luggage : Scale
  const what = categoriesLabel(card.categories)
  const refused = categoriesLabel(card.refused)
  const side = status ?? (card.trust_count > 0 ? <TrustBadge count={card.trust_count} /> : <span className="whitespace-nowrap text-[13px] text-muted-foreground">{ageLabel(card.created_at)}</span>)
  return (
    <div className={`grid min-w-0${dim ? ' opacity-60' : ''}`} data-testid="request-card" data-id={card.id}>
      <Card
        className={`!h-full !gap-1.5 !p-4${onOpen ? ' cursor-pointer' : ''}`}
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
        <div className="flex items-center justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5">
            <HeadIcon className="size-[18px] shrink-0 text-primary" aria-hidden />
            <b className="text-base font-extrabold whitespace-nowrap">{dateLabel(card)}</b>
          </span>
          {side}
        </div>
        <Line icon={Route}>
          <b>
            <bdi>{routeLabel(card)}</bdi>
          </b>
        </Line>
        <Line icon={SizeIcon}>
          {sizeLabel(card)}
          {what ? ` · ${what}` : ''}
        </Line>
        {refused ? (
          <Line icon={Ban} muted small>
            قبول نمی‌کند: {refused}
          </Line>
        ) : null}
        <div className="text-[13px] text-muted-foreground">
          هزینه: توافقی{card.trust_count > 0 || status ? ` · ${ageLabel(card.created_at)}` : ''}
        </div>
        {matches ? <div className="text-sm">{matches}</div> : null}
        {foot ? <div className="mt-1">{foot}</div> : null}
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
