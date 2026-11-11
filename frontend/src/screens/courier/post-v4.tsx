import { ArrowDownUp, Calendar, CalendarDays, CalendarSearch, ChevronDown, ChevronLeft, Infinity as InfinityIcon, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { Flag } from '@/components/flag'
import { Card } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { dualDate } from '@/lib/format'
import { placeLabel, useLocations } from './route-sheet'

const ON = 'data-[state=on]:!border-transparent data-[state=on]:!bg-accent data-[state=on]:!text-accent-foreground'

// Design v4 `choice`: a photo on one side, the choice in bold, a chevron.
export function ChoiceCard({ photo, icon: Icon, title, onClick, testId }: { photo: string; icon: LucideIcon; title: string; onClick: () => void; testId: string }) {
  return (
    <Card className="!h-[110px] !flex-row !items-center !gap-3.5 !overflow-hidden !rounded-2xl !p-0">
      <button type="button" className="flex h-full w-full items-center gap-3.5 text-start" onClick={onClick} data-testid={testId}>
        <span className="h-full w-[120px] flex-none bg-cover bg-center" style={{ backgroundImage: `url(${photo})` }} aria-hidden />
        <b className="flex items-center gap-2 text-lg font-extrabold">
          <Icon className="size-[22px] text-primary" aria-hidden />
          {title}
        </b>
        <ChevronLeft className="ms-auto me-3.5 size-5 text-muted-foreground" aria-hidden />
      </button>
    </Card>
  )
}

// One of a few, as big tiles (month, «تاریخ دقیق», «عجله ندارم»). Radios, as the chips were.
export function WhenTiles({ value, options, onChange, testId }: { value: string; options: Array<{ value: string; label: string; sub?: string; icon?: LucideIcon }>; onChange: (next: string) => void; testId: string }) {
  return (
    <ToggleGroup type="single" dir="rtl" spacing={2} className="!grid !w-full !grid-cols-2 !gap-2" value={value} onValueChange={(next) => next && onChange(next)} aria-label="زمان" data-testid={testId}>
      {options.map((option) => {
        const Icon = option.icon ?? Calendar
        return (
          <ToggleGroupItem key={option.value} value={option.value} variant="outline" className={`!h-[76px] !w-full !flex-col !gap-1 !px-1 !text-xs !font-bold ${ON}`}>
            <Icon className="!size-[22px]" aria-hidden />
            <span>{option.label}</span>
            {option.sub ? <small className="text-[11px] font-medium">{option.sub}</small> : null}
          </ToggleGroupItem>
        )
      })}
    </ToggleGroup>
  )
}

export const WHEN_ICONS = { exact: CalendarSearch, open: InfinityIcon }

// Two big fields with flags, the swap circle between them.
export function RouteFields({ from, to, onOpen, onSwap }: { from: string; to: string; onOpen: (side: 'from' | 'to') => void; onSwap?: () => void }) {
  const locations = useLocations()
  const name = (key: string) => {
    if (!key) return 'انتخاب کنید'
    const country = locations.data?.countries.find((row) => row.code === key.split(':')[0])
    return `${placeLabel(key, locations.data)}${country ? `، ${country.name_fa}` : ''}`
  }
  const field = (side: 'from' | 'to', key: string): ReactNode => (
    <button type="button" className="flex h-14 w-full items-center gap-2.5 rounded-[14px] border bg-card px-3.5 text-start" onClick={() => onOpen(side)} data-testid={`route-${side}`}>
      {key ? <Flag code={key.split(':')[0]} /> : null}
      <span className="min-w-0">
        <span className="block text-xs text-muted-foreground">{side === 'from' ? 'از' : 'به'}</span>
        <b className={`block truncate text-[15px] ${key ? '' : 'text-muted-foreground'}`}>
          <bdi>{name(key)}</bdi>
        </b>
      </span>
      <ChevronDown className="ms-auto size-[18px] text-muted-foreground" aria-hidden />
    </button>
  )
  return (
    <div className="flex flex-col">
      {field('from', from)}
      {onSwap ? (
        <button type="button" className="z-[1] -my-4 flex size-10 items-center justify-center self-center rounded-full border-4 border-background bg-muted" onClick={onSwap} aria-label="جابه‌جا کردن مبدا و مقصد" data-testid="route-swap">
          <ArrowDownUp className="size-[18px]" />
        </button>
      ) : (
        <div className="h-2" />
      )}
      {field('to', to)}
    </div>
  )
}

// Design v4 `rev`: label at the start, value at the end, one row each.
export function ReviewRows({ rows, testId }: { rows: Array<[string, ReactNode]>; testId: string }) {
  return (
    <Card className="!gap-0 !rounded-2xl !px-3.5 !py-0.5" data-testid={testId}>
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-2.5 border-b py-3 text-sm last:border-b-0">
          <span className="flex-none text-[13px] text-muted-foreground">{label}</span>
          <b className="flex flex-wrap items-center justify-end gap-1.5 text-end">{value}</b>
        </div>
      ))}
    </Card>
  )
}

export function Note({ icon: Icon, children, tone = 'accent', testId }: { icon: LucideIcon; children: ReactNode; tone?: 'accent' | 'muted' | 'no'; testId?: string }) {
  const tones = { accent: 'bg-accent text-accent-foreground', muted: 'bg-muted text-muted-foreground', no: 'bg-no-soft text-no' }
  return (
    <div className={`flex items-center gap-2 rounded-lg px-3 py-2.5 text-[13px] font-semibold ${tones[tone]}`} data-testid={testId}>
      <Icon className="size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </div>
  )
}

const JALALI_MONTH = new Intl.DateTimeFormat('fa-IR-u-ca-persian', { month: 'long', timeZone: 'UTC' })

// The two Jalali months a Gregorian month spans: «آبان · آذر».
export function jalaliMonths(key: string) {
  const first = new Date(`${key}-01T00:00:00Z`)
  if (Number.isNaN(first.getTime())) return ''
  const last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0))
  const a = JALALI_MONTH.format(first)
  const b = JALALI_MONTH.format(last)
  return a === b ? a : `${a} · ${b}`
}

export function dualText(iso: string) {
  const date = dualDate(iso)
  return date ? `${date.main} · ${date.jalali}` : iso
}

export function DualNote({ iso }: { iso: string }) {
  return (
    <Note icon={CalendarDays} tone="muted" testId="post-dual-date">
      {dualText(iso)}
    </Note>
  )
}
