import { useQuery } from '@tanstack/react-query'
import { AtSign, ChevronRight, CircleAlert, CircleCheck, Clock, ExternalLink, MapPin, Send } from 'lucide-react'
import { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { CityAreas } from '@/api/post-types'
import type { Card as CardData } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { MainNav } from '@/components/main-nav'
import { Photo, Price } from '@/components/listing-card'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { exchangeSession, getSession, publicFetch, subscribeSession, unwrap } from '@/lib/api'
import { useBack } from '@/lib/back'
import { cardPlace, cardTitle, currency, fa, modeBadge } from '@/lib/format'
import { openExternal } from '@/lib/links'
import { DESCRIPTION_MAX, type HomeType } from '@/lib/post-draft'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { Chip } from '../filter-sheet'

const hasTelegramBack = () => !getWebApp()?.initData || isVersionAtLeast('6.1')

// One screen of a post flow. `nav`: the tabs, as `c.page(nav=True)`; a wizard step draws none. These screens are Persian
// for now, so they stay RTL when the app's language is English or German.
export function PostPage({ back, foot, nav, testId, children }: { back?: (() => void) | null; foot?: ReactNode; nav?: 'post' | 'me'; testId?: string; children: ReactNode }) {
  useBack(back ?? null)
  return (
    <div dir="rtl" className="flex h-full min-h-0 flex-col" data-testid={testId}>
      {nav ? <MainNav place="top" active={nav} /> : null}
      {back && !hasTelegramBack() ? (
        <div className="flex-none px-2 pt-2">
          <Button variant="ghost" size="sm" onClick={back} data-testid="page-back">
            <ChevronRight />
            برگشت
          </Button>
        </div>
      ) : null}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto" data-testid="post-scroll">
        {children}
      </div>
      {foot ? <div className="app-footer">{foot}</div> : null}
      {nav ? <MainNav place="bottom" active={nav} /> : null}
    </div>
  )
}

// `c.stack(center=True)`: forms keep a 560 px column on desktop.
export function Stack({ children, gap = 'gap-4' }: { children: ReactNode; gap?: string }) {
  return <div className={`mx-auto flex w-full flex-col ${gap} p-4 md:max-w-[560px] md:p-6`}>{children}</div>
}

export function FootColumn({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-lg items-center gap-2">{children}</div>
}

// `steps` in the design library: the bar fills from the right, as a RTL reader expects.
export function Steps({ step, total, label }: { step: number; total: number; label?: string }) {
  return (
    <div className="flex flex-col gap-1.5" data-testid="post-steps">
      <div className="-scale-x-100">
        <Progress dir="rtl" value={Math.round((step * 100) / total)} aria-label={`قدم ${fa(step)} از ${fa(total)}`} />
      </div>
      <p className="m-0 text-[13px] text-muted-foreground">
        قدم {fa(step)} از {fa(total)}
        {label ? ` · ${label}` : ''}
      </p>
    </div>
  )
}

export function Labeled({ label, error, hint, htmlFor, children, testId }: { label?: string; error?: string; hint?: string; htmlFor?: string; children: ReactNode; testId?: string }) {
  return (
    <Field data-invalid={Boolean(error)} data-testid={testId}>
      {label ? <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel> : null}
      {children}
      {hint ? <FieldDescription>{hint}</FieldDescription> : null}
      {error ? <FieldError>{error}</FieldError> : null}
    </Field>
  )
}

export function Pair({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>
}

export function ChipsOne<T extends string>({ options, value, onChange, label }: { options: ReadonlyArray<readonly [T, string]>; value: T | null; onChange: (next: T) => void; label?: string }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      {options.map(([key, name]) => (
        <Chip key={key} label={name} on={value === key} onClick={() => onChange(key)} />
      ))}
    </div>
  )
}

export function ChipsMany<T extends string>({ options, value, onChange, label }: { options: ReadonlyArray<readonly [T, string]>; value: T[]; onChange: (next: T[]) => void; label?: string }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-2">
      {options.map(([key, name]) => (
        <Chip key={key} label={name} on={value.includes(key)} onClick={() => onChange(value.includes(key) ? value.filter((item) => item !== key) : [...value, key])} />
      ))}
    </div>
  )
}

export function SwitchLine({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (next: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex min-h-11 items-center justify-between gap-3">
      <span className="text-sm font-semibold">{label}</span>
      <Switch dir="rtl" aria-label={label} checked={checked} disabled={disabled} onCheckedChange={onChange} />
    </label>
  )
}

export function MoneyInput({ id, code, value, onChange, placeholder, invalid, label }: { id?: string; code: string; value: string; onChange: (next: string) => void; placeholder?: string; invalid?: boolean; label?: string }) {
  return (
    <InputGroup className={invalid ? '!border-destructive' : ''}>
      <InputGroupAddon>
        <bdi dir="ltr">{currency(code)}</bdi>
      </InputGroupAddon>
      <InputGroupInput id={id} aria-label={label} inputMode="decimal" dir="auto" placeholder={placeholder} aria-invalid={invalid || undefined} value={value} onChange={(event) => onChange(event.target.value)} />
    </InputGroup>
  )
}

export function DescriptionField({ label, value, onChange, onBlur, error, rows, id, testId }: { label: string; value: string; onChange: (next: string) => void; onBlur?: () => void; error?: string; rows?: number; id: string; testId?: string }) {
  return (
    <Labeled label={label} htmlFor={id} error={error} hint={error ? undefined : `${fa(value.length)} / ${fa(DESCRIPTION_MAX)}`} testId={testId}>
      <Textarea id={id} dir="auto" rows={rows} className={rows ? undefined : 'min-h-48'} maxLength={DESCRIPTION_MAX} aria-invalid={Boolean(error) || undefined} value={value} onChange={(event) => onChange(event.target.value)} onBlur={onBlur} />
    </Labeled>
  )
}

// Browsing alone never exchanges (ADR-3). A post needs to know the person's username, so it starts the session.
export function usePostUser() {
  const session = useSyncExternalStore(subscribeSession, getSession)
  useEffect(() => {
    if (!getSession()) void exchangeSession()
  }, [])
  return { ready: session !== null, username: session?.user.telegram_username?.trim().replace(/^@/, '') ?? '' }
}

export function phoneOk(phone: string) {
  return phone.replace(/\D/g, '').length >= 7
}

const USERNAME_HELP = 'https://t.me/settings'

// F18#7, #13, F19#5. These are the person's own contact, and the only place a post asks for one.
export function ContactFields({ username, ready, phone, showPhone, onPhone, onShowPhone, missingCopy, serverError }: { username: string; ready: boolean; phone: string; showPhone: boolean; onPhone: (next: string) => void; onShowPhone: (next: boolean) => void; missingCopy: string; serverError?: boolean }) {
  if (!ready) return <Skeleton className="h-20 w-full" data-testid="contact-loading" />
  const bad = !username && !phoneOk(phone)
  if (username) {
    return (
      <>
        <div className="flex flex-col gap-2">
          <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">راه تماس</h3>
          <Card className="gap-1 p-4" data-testid="contact-username">
            <div className="flex items-center gap-3">
              <Send className="size-5 flex-none" aria-hidden />
              <div className="flex min-w-0 flex-col items-start">
                <bdi dir="ltr" className="text-[15px] font-bold">
                  @{username}
                </bdi>
                <span className="text-[13px] text-muted-foreground">تلگرام</span>
              </div>
            </div>
          </Card>
        </div>
        <Labeled label="شماره تلفن" htmlFor="post-phone" error={serverError ? missingCopy : undefined}>
          <Input id="post-phone" type="tel" dir="ltr" maxLength={24} placeholder="+1" value={phone} onChange={(event) => onPhone(event.target.value)} data-testid="phone-input" />
        </Labeled>
        {phone.trim() ? <SwitchLine label="نمایش شماره در آگهی" checked={showPhone} onChange={onShowPhone} /> : null}
      </>
    )
  }
  return (
    <>
      <div className="flex flex-col gap-2">
        <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">راه تماس</h3>
        <Card className="gap-3 p-4" data-testid="no-username">
          <div className="flex items-center gap-2">
            <AtSign className="size-5 flex-none" aria-hidden />
            <span className="text-sm font-bold">نام کاربری تلگرام ندارید</span>
          </div>
          <Button variant="outline" className="w-full" onClick={() => openExternal(USERNAME_HELP)}>
            <ExternalLink />
            ساختن نام کاربری
          </Button>
        </Card>
      </div>
      <Labeled label="شماره تلفن" htmlFor="post-phone" error={bad || serverError ? missingCopy : undefined}>
        <Input id="post-phone" type="tel" dir="ltr" maxLength={24} placeholder="+1" aria-invalid={bad || undefined} className={bad ? '!border-destructive' : ''} value={phone} onChange={(event) => onPhone(event.target.value)} data-testid="phone-input" />
      </Labeled>
    </>
  )
}

export function Bad({ children, testId, action }: { children: ReactNode; testId?: string; action?: ReactNode }) {
  return (
    <Alert variant="destructive" data-testid={testId}>
      <CircleAlert />
      <AlertDescription className={action ? 'flex w-full flex-wrap items-center justify-between gap-2' : undefined}>
        <span>{children}</span>
        {action}
      </AlertDescription>
    </Alert>
  )
}

// F18#9, #32, F19#3, #10. Published at once unless review holds it.
export function DoneBody({ held, actions, testId }: { held: boolean; actions: ReactNode; testId?: string }) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-4 md:p-6" data-testid={testId}>
      <StateBox icon={CircleCheck} title={held ? 'آگهی شما ثبت شد' : 'آگهی شما منتشر شد'}>
        <div className="flex w-60 max-w-full flex-col items-center gap-2">
          {held ? (
            <Badge variant="secondary" data-testid="held-badge">
              <Clock />
              در انتظار بررسی
            </Badge>
          ) : null}
          {actions}
        </div>
      </StateBox>
    </div>
  )
}

// «نورث یورک · 5000 Yonge St»: a street that starts with its number needs isolating, or RTL moves the number to its end.
export function PlaceText({ text }: { text: string }) {
  const [area, ...rest] = text.split(' · ')
  const street = rest.join(' · ')
  if (!/^[\d۰-۹]/.test(street)) return <>{text}</>
  return (
    <>
      {area} · <bdi dir="ltr">{street}</bdi>
    </>
  )
}

// `listing_card` with no link and no bookmark, for what is not a place to go: the preview and the duplicate sheet.
export function StaticCard({ card, badge, line, action, dim }: { card: CardData; badge?: ReactNode; line?: ReactNode; action?: ReactNode; dim?: boolean }) {
  const mode = modeBadge(card)
  return (
    <div className="grid min-w-0" data-testid="static-card">
      <Card className="relative h-full gap-0 py-0">
        <Photo src={card.thumb} count={card.photo_count > 0 ? fa(card.photo_count) : undefined} dim={dim}>
          {badge}
        </Photo>
        <div className="flex min-w-0 flex-col gap-1 px-4 pt-3 pb-4">
          <Price card={card} />
          <div className="truncate text-sm font-bold">{cardTitle(card)}</div>
          <div className="flex items-center gap-1 text-sm text-muted-foreground">
            <MapPin className="size-3.5 flex-none" aria-hidden />
            <span className="truncate">
              <PlaceText text={cardPlace(card)} />
            </span>
          </div>
          {mode ? (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <Badge variant="outline">{mode}</Badge>
            </div>
          ) : null}
          {line ? <div className="truncate text-[13px] text-muted-foreground">{line}</div> : null}
        </div>
        {action ? <div className="px-4 pb-4">{action}</div> : null}
      </Card>
    </div>
  )
}

export const AREAS_KEY = (country: string, city: string) => ['post-areas', country, city]

// A metro's areas. A failure leaves a text field, so a post never waits on this.
export function useAreas(country: string | undefined, city: string | undefined) {
  return useQuery({
    queryKey: AREAS_KEY(country ?? '', city ?? ''),
    enabled: Boolean(country && city),
    retry: false,
    queryFn: () => unwrap(publicFetch<CityAreas>(`/api/v1/public/areas/?country=${encodeURIComponent(country ?? '')}&city=${encodeURIComponent(city ?? '')}`)),
  })
}

export function GoButton({ to, children, variant, testId }: { to: string; children: ReactNode; variant?: 'outline'; testId?: string }) {
  const navigate = useNavigate()
  return (
    <Button variant={variant} className="w-full" data-testid={testId} onClick={() => navigate(to)}>
      {children}
    </Button>
  )
}

export function ConfirmSheet({ title, foot, onClose, testId, children }: { title: string; foot: ReactNode; onClose: () => void; testId: string; children?: ReactNode }) {
  return (
    <AppSheet open onClose={onClose} label={title} title={title} foot={foot} testId={testId}>
      {children}
    </AppSheet>
  )
}

export const LONG_TYPES: ReadonlyArray<readonly [HomeType, string]> = [
  ['room', 'اتاق'],
  ['studio', 'استودیو'],
  ['basement', 'بیسمنت'],
  ['b1', '۱ خوابه'],
  ['b2', '۲ خوابه'],
  ['b3', '۳ خوابه+'],
]

export const BAD_CHIP = '!border-destructive !text-destructive'
