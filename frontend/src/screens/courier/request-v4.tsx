import { useMutation } from '@tanstack/react-query'
import { Flag as FlagIcon, Lock, Package, Plane, UserRound } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Flag } from '@/components/flag'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { dualDate } from '@/lib/format'
import { track } from '@/lib/report'
import handover from '@/assets/courier/package-handover.jpg'
import wing from '@/assets/courier/window-wing.jpg'
import { errorCode, postJson, unwrap } from './api'
import { ageLabel, isTrip, packageSize, stopsLabel, tripSpace } from './format'
import { categoryIcon, Chip, TrustLine } from './parts'
import type { components } from '@/api/courier-schema.gen'

type Detail = components['schemas']['RequestDetail']

function Place({ code, name, sub }: { code: string; name: string; sub: string }) {
  return (
    <div className="flex w-[104px] flex-col items-center gap-1.5 text-center">
      <span className="scale-[1.9] py-2 drop-shadow-md">
        <Flag code={code} />
      </span>
      <b className="text-[17px] leading-tight font-extrabold">
        <bdi>{name}</bdi>
      </b>
      <span className="text-[11px] opacity-85">{sub}</span>
    </div>
  )
}

function GlassFact({ value, label }: { value: ReactNode; label: string }) {
  return (
    <div className="rounded-xl border border-white/20 bg-white/15 p-2 text-center backdrop-blur-md">
      <b className="block text-[15px] font-extrabold">{value}</b>
      <span className="text-[11px] opacity-85">{label}</span>
    </div>
  )
}

// Design v4 `hero`: photo, two places with flags, the date in the middle, three facts on glass.
export function RequestHero({ item, countryName }: { item: Detail; countryName: (code: string) => string }) {
  const trip = isTrip(item)
  const date = item.date ? dualDate(item.date) : null
  const last = item.stops[item.stops.length - 1] ?? item.origin
  const MidIcon = trip ? Plane : Package
  const facts: Array<[ReactNode, string]> = trip
    ? [
        [item.capacity_kg ? tripSpace(item.capacity_kg) : 'نامشخص', 'جای خالی'],
        ['توافقی', 'هزینه'],
        item.stops.length > 1 ? [<bdi key="s">{stopsLabel(item.stops)}</bdi>, 'مقصدها'] : [ageLabel(item.created_at), 'ثبت'],
      ]
    : [
        [item.weight_kg ? packageSize(item.weight_kg) : 'نامشخص', 'وزن'],
        ['توافقی', 'هزینه'],
        [ageLabel(item.created_at), 'ثبت'],
      ]
  return (
    <div className="relative flex flex-col gap-3.5 bg-cover bg-center px-4 pt-5 pb-4 text-white" style={{ backgroundImage: `url(${trip ? wing : handover})` }} data-testid="request-hero">
      <div className="absolute inset-0 bg-gradient-to-b from-[rgba(15,20,30,.15)] to-[rgba(15,20,30,.85)]" aria-hidden />
      <div className="relative flex items-center justify-between">
        <Place code={item.origin.country} name={item.origin.name_fa} sub={countryName(item.origin.country)} />
        <div className="flex flex-1 flex-col items-center gap-1 text-xs font-semibold">
          <MidIcon className="size-[22px] -scale-x-100" aria-hidden />
          <div className="w-full border-t-2 border-dashed border-white/55" />
          <span data-testid="hero-date">{date ? `${trip ? '' : 'تا '}${date.main}` : trip ? 'تاریخ پرواز نامشخص' : 'بدون عجله'}</span>
          {date ? <em className="text-[10px] not-italic opacity-80">{date.jalali}</em> : null}
          <span className="sr-only">{trip ? 'تاریخ پرواز' : 'باید برسد'}</span>
        </div>
        <Place code={last.country} name={last.name_fa} sub={countryName(last.country)} />
      </div>
      <div className="relative grid grid-cols-3 gap-2">
        {facts.map(([value, label]) => (
          <GlassFact key={label} value={value} label={label} />
        ))}
      </div>
    </div>
  )
}

// «می‌برد / نمی‌برد» for a trip, «داخل بسته» for a package.
export function CarrySection({ item }: { item: Detail }) {
  const trip = isTrip(item)
  if (!item.categories.length && !item.refused.length && !item.excluded_other_text) return null
  return (
    <section className="flex flex-col gap-2" data-testid="carry">
      <h3 className="text-[13px] font-bold text-muted-foreground">{trip ? 'می‌برد / نمی‌برد' : 'داخل بسته'}</h3>
      <div className="flex flex-wrap gap-1.5">
        {item.categories.map((row) => (
          <Chip key={row.code} icon={categoryIcon(row.code)} tone={trip ? 'ok' : 'plain'}>
            {row.name_fa}
          </Chip>
        ))}
        {item.refused.map((row) => (
          <Chip key={`no-${row.code}`} icon={categoryIcon(row.code)} tone="no" testId="refused-chip">
            {row.name_fa}
          </Chip>
        ))}
      </div>
      {item.excluded_other_text ? <p className="ph-no-capture text-[13px] text-muted-foreground">قبول نمی‌کند: {item.excluded_other_text}</p> : null}
    </section>
  )
}

// The other person, without a name until both accept.
export function WhoCard({ item }: { item: Detail }) {
  return (
    <Card className="!flex-row !items-center !gap-2.5 !rounded-2xl !p-3" data-testid="who">
      <span className="flex size-10 flex-none items-center justify-center rounded-full bg-accent text-accent-foreground">
        <UserRound className="size-5" aria-hidden />
      </span>
      <b className="text-sm">{isTrip(item) ? 'مسافر' : 'فرستنده'}</b>
      <TrustLine count={item.trust_count} />
      <span className="ms-auto">
        <Chip icon={Lock}>نام بعد از قبول</Chip>
      </span>
    </Card>
  )
}

export function DescriptionCard({ item }: { item: Detail }) {
  if (!item.description) return null
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[13px] font-bold text-muted-foreground">{isTrip(item) ? 'توضیح مسافر' : 'توضیح فرستنده'}</h3>
      <Card className="!rounded-2xl !p-3">
        <p className="ph-no-capture text-sm leading-[1.8] break-words whitespace-pre-line">
          <bdi>{item.description}</bdi>
        </p>
      </Card>
    </section>
  )
}

// Pending: hours left before the 72-hour expiry, from the intro.
export function hoursLeft(expiresAt: string | undefined, now = Date.now()) {
  if (!expiresAt) return null
  const hours = Math.ceil((new Date(expiresAt).getTime() - now) / 3_600_000)
  return hours > 0 ? hours : null
}

export function DateTileSmall({ iso, pkg }: { iso: string | null | undefined; pkg?: boolean }) {
  const date = iso ? dualDate(iso) : null
  return date ? (
    <div className={`flex w-[50px] flex-none flex-col items-center rounded-xl py-1.5 leading-[1.1] ${pkg ? 'bg-accent text-accent-foreground' : 'bg-navy-soft text-navy'}`}>
      <b className="text-xl font-black">{date.day}</b>
      <span className="text-[11px] font-bold">{date.month}</span>
      <em className="text-[10px] not-italic opacity-75">{date.jalali}</em>
    </div>
  ) : null
}

const REASONS = [
  ['SCAM', 'کلاهبرداری'],
  ['PROHIBITED', 'کالای ممنوع'],
  ['SPAM', 'تبلیغ'],
  ['OTHER', 'سایر'],
] as const

// v4_report: a REPORT_USER ticket naming this request.
export function ReportSheet({ id, open, onClose }: { id: number; open: boolean; onClose: () => void }) {
  const [reason, setReason] = useState<string>('SCAM')
  const [text, setText] = useState('')
  useEffect(() => {
    if (open) track('courier_report_open', { id })
  }, [open, id])
  const send = useMutation({
    mutationFn: () => unwrap(postJson(`requests/${id}/report/`, { reason, text })),
    onSuccess: () => {
      track('courier_report_sent', { id, reason })
      showToast({ message: 'گزارش فرستاده شد', icon: 'check' })
      setText('')
      onClose()
    },
    onError: (error) => {
      track('courier_report_failed', { id, code: errorCode(error) })
      showToast({ message: 'گزارش فرستاده نشد', icon: 'alert' })
    },
  })
  return (
    <AppSheet
      open={open}
      onClose={send.isPending ? null : onClose}
      onBack={onClose}
      label="گزارش این درخواست"
      title="گزارش این درخواست"
      testId="report-sheet"
      foot={
        <Button className="w-full" size="lg" disabled={send.isPending} onClick={() => send.mutate()} data-testid="report-send">
          {send.isPending ? <Spinner /> : <FlagIcon />}
          فرستادن گزارش
        </Button>
      }
    >
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="دلیل">
        {REASONS.map(([code, label]) => (
          <button key={code} type="button" role="radio" aria-checked={reason === code} onClick={() => setReason(code)} className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${reason === code ? 'border-transparent bg-accent text-accent-foreground' : 'bg-card'}`}>
            {label}
          </button>
        ))}
      </div>
      <Textarea value={text} onChange={(event) => setText(event.target.value)} maxLength={1000} placeholder="توضیح (اختیاری)" className="min-h-[92px]" data-testid="report-text" />
    </AppSheet>
  )
}

export function ReportLink({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className="flex items-center gap-1 text-xs font-bold text-no" onClick={onOpen} data-testid="report-open">
      <FlagIcon className="size-3.5" aria-hidden />
      گزارش
    </button>
  )
}

