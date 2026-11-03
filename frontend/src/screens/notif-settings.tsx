import { useQuery } from '@tanstack/react-query'
import { Bot, CircleAlert, RefreshCw } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { Account, Prefs } from '@/api/types'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { ACCOUNT_KEY, fetchAccount, patchPrefs, setCachedPrefs } from '@/lib/account'
import { useBack } from '@/lib/back'
import { fa } from '@/lib/format'
import { openExternal } from '@/lib/links'
import { appDir, t } from '@/lib/locale'
import { track } from '@/lib/report'
import { cn } from '@/lib/utils'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

const SAME_TIME = 'ساعت شروع و پایان یکی است'
const DEFAULT_FROM = '23:00'
const DEFAULT_TO = '08:00'
const APP_WIDE = new Set(['maintenance', 'user_blocked', 'session_expired', 'auth_invalid', 'auth_outside', 'miniapp_off'])

type Change = Partial<Pick<Prefs, 'notify_enabled' | 'notify_alerts' | 'notify_saved' | 'notify_news' | 'quiet_from' | 'quiet_to'>>

const shortTime = (time: string | null | undefined) => (time ? time.slice(0, 5) : '')

export function timeLabel(time: string) {
  const [hour, minute] = time.split(':').map(Number)
  return `${fa(Math.floor(hour / 10))}${fa(hour % 10)}:${fa(Math.floor(minute / 10))}${fa(minute % 10)}`
}

// Every hour, plus a time set elsewhere that is not on the hour, so the field never shows what is not saved.
function timeOptions(current: string) {
  const hours = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`)
  return current && !hours.includes(current) ? [...hours, current].sort() : hours
}

// F23 rules: each change saves at once, with no save button. A failure puts the last value back (F25#10).
async function save(before: Prefs, change: Change, done?: () => void) {
  setCachedPrefs(change)
  const result = await patchPrefs(change)
  if (result.ok) {
    setCachedPrefs(result.data)
    return done?.()
  }
  setCachedPrefs(Object.fromEntries(Object.keys(change).map((key) => [key, before[key as keyof Change]])) as Change)
  track('prefs_failed', { code: result.code })
  if (APP_WIDE.has(result.code)) return
  const retry = () => void save(before, change, done)
  if (result.code === 'rate_limited') return rateLimitedToast(retry, 'prefs')
  saveFailedToast(retry, 'prefs')
}

function SwitchRow({ label, checked, disabled, onChange, testId }: { label: string; checked: boolean; disabled?: boolean; onChange: (next: boolean) => void; testId: string }) {
  return (
    <label className="flex min-h-[52px] items-center justify-between gap-3 py-2">
      <span className="min-w-0 text-sm font-medium">{label}</span>
      <Switch dir={appDir()} aria-label={label} data-testid={testId} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </label>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="m-0 text-sm font-bold">{title}</h2>
      {children}
    </section>
  )
}

function Settings({ account }: { account: Account }) {
  const prefs = account.prefs
  const off = !prefs.notify_enabled
  const quietOn = Boolean(prefs.quiet_from && prefs.quiet_to)
  const savedFrom = shortTime(prefs.quiet_from) || DEFAULT_FROM
  const savedTo = shortTime(prefs.quiet_to) || DEFAULT_TO
  // What is picked can differ from what is saved: two equal times stay here, with their error, and never reach the server.
  const [picked, setPicked] = useState({ from: savedFrom, to: savedTo })
  const same = quietOn && picked.from === picked.to

  useEffect(() => {
    if (account.bot_blocked) track('alerts_blocked')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.bot_blocked])

  const master = (on: boolean) => {
    void save(prefs, { notify_enabled: on }, () => {
      if (on) return track('notif_on')
      track('notif_all_off')
      showToast({ id: 'prefs', message: t('همه‌ی اعلان‌ها خاموش شد'), icon: 'info', action: { label: t('برگردان'), onClick: () => master(true) } })
    })
  }
  const quietSaved = () => {
    track('quiet_saved')
    showToast({ id: 'prefs', message: t('ساعت بی‌صدا ذخیره شد'), icon: 'check' })
  }
  const pick = (next: { from: string; to: string }) => {
    setPicked(next)
    if (next.from === next.to) return track('quiet_same')
    void save(prefs, { quiet_from: next.from, quiet_to: next.to }, quietSaved)
  }
  const quietSwitch = (on: boolean) => {
    if (on) return pick(picked.from === picked.to ? { from: DEFAULT_FROM, to: DEFAULT_TO } : picked)
    void save(prefs, { quiet_from: null, quiet_to: null })
  }
  const time = (label: string, id: string, value: string, change: (next: string) => void) => (
    <Field className="min-w-0 flex-1" data-invalid={same || undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <NativeSelect id={id} data-testid={id} className={cn('w-full', same && 'border-destructive')} aria-invalid={same || undefined} disabled={off || !quietOn} value={value} onChange={(event) => change(event.target.value)}>
        {timeOptions(value).map((option) => (
          <NativeSelectOption key={option} value={option}>
            {timeLabel(option)}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </Field>
  )

  return (
    <>
      {account.bot_blocked ? (
        <Alert data-testid="bot-blocked">
          <Bot />
          <AlertDescription className="flex w-full items-center justify-between gap-2">
            <span className="text-foreground">{t('ربات بسته است و پیامی نمی‌رسد.')}</span>
            <Button size="sm" onClick={() => openExternal(account.bot_url)}>
              {t('باز کردن ربات')}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      <Card className="gap-0 px-4 py-1">
        <SwitchRow label={t('همه‌ی اعلان‌ها')} testId="switch-all" checked={prefs.notify_enabled ?? true} onChange={master} />
      </Card>
      <div className={cn('flex flex-col gap-4', off && 'opacity-70')} data-testid="notif-rest">
        <Section title={t('پیام‌ها')}>
          <Card className="gap-0 divide-y divide-border px-4 py-1">
            <SwitchRow label={t('آگهی تازه برای گوش به زنگ‌ها')} testId="switch-alerts" disabled={off} checked={prefs.notify_alerts ?? true} onChange={(on) => void save(prefs, { notify_alerts: on })} />
            <SwitchRow label={t('آگهی شما منتشر یا رد شد')} testId="switch-news" disabled={off} checked={prefs.notify_news ?? false} onChange={(on) => void save(prefs, { notify_news: on })} />
            <SwitchRow label={t('آگهی نشان‌شده بسته شد')} testId="switch-saved" disabled={off} checked={prefs.notify_saved ?? true} onChange={(on) => void save(prefs, { notify_saved: on })} />
          </Card>
        </Section>
        <Section title={t('ساعت بی‌صدا')}>
          <Card className="gap-0 px-4 py-1">
            <SwitchRow label={t('بی‌صدا در شب')} testId="switch-quiet" disabled={off} checked={quietOn} onChange={quietSwitch} />
            <div className="pt-1 pb-2">
              <div className="flex gap-3">
                {time(t('از'), 'quiet-from', picked.from, (from) => pick({ ...picked, from }))}
                {time(t('تا'), 'quiet-to', picked.to, (to) => pick({ ...picked, to }))}
              </div>
              {same ? <FieldError data-testid="quiet-error" className="mt-1">{t(SAME_TIME)}</FieldError> : null}
            </div>
            <div className="flex min-h-10 items-center justify-between gap-2 text-sm">
              <span className="text-muted-foreground">{t('منطقه‌ی زمانی')}</span>
              <span>{t('شهر هر گوش به زنگ')}</span>
            </div>
          </Card>
        </Section>
      </div>
    </>
  )
}

// F23. Reached from the account's notifications row.
export default function NotifSettingsScreen() {
  const navigate = useNavigate()
  const here = useLocation()
  const online = useOnline()
  useBack(() => (here.key !== 'default' ? navigate(-1) : navigate('/account', { replace: true })))
  const query = useQuery({ queryKey: ACCOUNT_KEY, queryFn: fetchAccount, staleTime: 0 })
  const slow = useAfter(3000, query.isPending)
  let body: ReactNode
  if (query.isPending) {
    body = (
      <div className="flex flex-col gap-3" data-testid="notif-loading">
        {slow ? <SlowNote /> : null}
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    )
  } else if (!query.data) {
    body = (
      <div className="flex min-h-[420px] flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title={t('بارگذاری نشد')} testId="notif-failed">
          <Button className="w-full" onClick={() => void query.refetch()}>
            <RefreshCw />
            {t('تلاش مجدد')}
          </Button>
        </StateBox>
      </div>
    )
  } else {
    body = <Settings account={query.data} />
  }
  return (
    <div className="h-full overflow-y-auto" data-testid="notif-screen">
      <div className="mx-auto flex min-h-full w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
        {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
        <h1 className="m-0 text-2xl leading-8 font-extrabold">{t('تنظیم اعلان‌ها')}</h1>
        {body}
      </div>
    </div>
  )
}
