import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ban, CalendarDays, Check, ChevronLeft, CircleAlert, Info, Luggage, Map as MapIcon, Package, Plane, RefreshCw, Route, Scale, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Progress } from '@/components/ui/progress'
import { Spinner } from '@/components/ui/spinner'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { courierFetch, CourierError, errorCode, newIdempotencyKey, postJson, unwrap } from './api'
import { categoriesLabel, monthLabel, placeKey, SIZES, sizeName, upcomingMonths, type SizeCode } from './format'
import { ProhibitedButton } from './guide'
import { H1, ME_KEY, Page, Stack } from './page'
import { formBody, parseTypedDate, type PostForm } from './post-form'
import { ChoiceChips, Fact, FactGrid, Section, SingleChips, SizeTiles } from './parts'
import { ResultUnknown } from './request'
import { placeLabel, RouteBar, RouteSheet, useLocations } from './route-sheet'
import type { components } from '@/api/courier-schema.gen'

type MyRequest = components['schemas']['MyRequest']

const PROHIBITED_HINT = 'دارو بدون نسخه، مایعات، مواد قابل اشتعال و پول نقد بالای حد مجاز ممنوع است.'
const DATE_ERROR = 'این تاریخ را نفهمیدم. مثل «۱۲ دسامبر» بنویسید، از امروز تا یک سال بعد.'
// As the bot's four sizes: what a traveler takes unless they change it.
const TRIP_DEFAULTS = ['DOCUMENTS', 'CLOTHES', 'PERSONAL_ITEMS', 'OTHER']

const EMPTY: PostForm = { from: '', to: '', stops: [], whenKind: '', month: '', date: '', size: '', categories: [], refusedOther: '', description: '' }

// C07.1
export function PostChooser() {
  const navigate = useNavigate()
  const row = (path: string, Icon: typeof Package, title: string, text: string) => (
    <Card className="!gap-0 !px-4 !py-1">
      <ItemGroup className="!gap-0">
        <Item asChild>
          <button type="button" className="w-full text-start" onClick={() => navigate(path)} data-testid={`post-${path.split('/').pop()}`}>
            <ItemMedia variant="icon">
              <Icon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>{title}</ItemTitle>
              <ItemDescription>{text}</ItemDescription>
            </ItemContent>
            <ChevronLeft className="size-4 text-muted-foreground" aria-hidden />
          </button>
        </Item>
      </ItemGroup>
    </Card>
  )
  return (
    <Page tab="post" testId="post-chooser">
      <Stack center>
        <H1>چه چیزی ثبت می‌کنید؟</H1>
        {row('/post/package', Package, 'بسته دارم', 'دنبال مسافرم')}
        {row('/post/trip', Luggage, 'مسافرم', 'جا دارم')}
      </Stack>
    </Page>
  )
}

function Wizard({ step, title, foot, onBack, children, overlay }: { step: number; title: string; foot?: ReactNode; onBack: () => void; children: ReactNode; overlay?: ReactNode }) {
  return (
    <Page tab={null} back={onBack} foot={foot ? <div className="mx-auto w-full max-w-[512px]">{foot}</div> : undefined} testId={`post-step-${step}`}>
      <Stack center>
        <div className="flex items-center gap-2" data-testid="post-progress">
          <Progress value={(step / 4) * 100} aria-label={`قدم ${fa(step)} از ۴`} dir="rtl" />
          <span className="text-[13px] whitespace-nowrap text-muted-foreground">
            {fa(step)} از ۴
          </span>
        </div>
        <H1>{title}</H1>
        {children}
      </Stack>
      {overlay}
    </Page>
  )
}

function ErrorNote({ text, testId }: { text: string; testId: string }) {
  return (
    <Alert variant="destructive" data-testid={testId}>
      <CircleAlert />
      <AlertDescription>{text}</AlertDescription>
    </Alert>
  )
}

function WhenField({ form, set, error, trip }: { form: PostForm; set: (next: Partial<PostForm>) => void; error: boolean; trip: boolean }) {
  const months = upcomingMonths()
  const options = [
    ...months.map((key, index) => ({ value: `m:${key}`, label: monthLabel(key, !trip && index === 0) })),
    { value: 'x', label: 'تاریخ دقیق' },
    ...(trip ? [] : [{ value: 'o', label: 'عجله ندارم' }]),
  ]
  const value = form.whenKind === 'month' ? `m:${form.month}` : form.whenKind === 'exact' ? 'x' : form.whenKind === 'open' ? 'o' : ''
  return (
    <>
      <SingleChips
        label="زمان"
        value={value}
        options={options}
        required
        testId="post-when"
        onChange={(next) => {
          if (next === 'x') set({ whenKind: 'exact', month: '' })
          else if (next === 'o') set({ whenKind: 'open', month: '', date: '' })
          else set({ whenKind: 'month', month: next.slice(2), date: '' })
        }}
      />
      {form.whenKind === 'exact' ? (
        <Field data-invalid={error || undefined}>
          <FieldLabel htmlFor="post-date">تاریخ</FieldLabel>
          <Input id="post-date" dir="rtl" placeholder="۱۲ دسامبر" value={form.date} aria-invalid={error || undefined} onChange={(event) => set({ date: event.target.value })} data-testid="post-date" />
          {error ? <FieldError data-testid="date-error">{DATE_ERROR}</FieldError> : null}
        </Field>
      ) : null}
    </>
  )
}

// C08.3 and C08.8. A past day is shown, as the board draws it, and says so when tapped.
function DayGrid({ month, value, onPick }: { month: string; value: string; onPick: (day: number) => void }) {
  const [year, number] = month.split('-').map(Number)
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate()
  const now = new Date()
  const isThisMonth = now.getFullYear() === year && now.getMonth() + 1 === number
  return (
    <div className="grid grid-cols-7 gap-1.5" data-testid="post-days">
      {Array.from({ length: days }, (_, index) => index + 1).map((day) => {
        const iso = `${month}-${String(day).padStart(2, '0')}`
        return (
          <Button
            key={day}
            variant={value === iso ? 'default' : 'outline'}
            size="sm"
            className={`!w-full !px-0${isThisMonth && day < now.getDate() ? ' opacity-50' : ''}`}
            onClick={() => {
              if (isThisMonth && day < now.getDate()) {
                showToast({ message: 'این روز گذشته است.', icon: 'alert' })
                track('courier.c08.past_day')
                return
              }
              onPick(day)
            }}
            data-testid={`post-day-${day}`}
          >
            {fa(day)}
          </Button>
        )
      })}
    </div>
  )
}

type Failure = 'failed' | 'duplicate' | null

// C07 and C08: four steps in one component, because they differ only in what they ask. The form stays through a failure.
export function PostScreen({ kind }: { kind: 'package' | 'trip' }) {
  const trip = kind === 'trip'
  const navigate = useNavigate()
  const client = useQueryClient()
  const [params] = useSearchParams()
  const locations = useLocations()
  const prefill = useMemo(() => {
    const from = params.get('from') ?? ''
    const to = params.get('to') ?? ''
    return { from: /:/.test(from) ? from : '', to: /:/.test(to) ? to : '' }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const [form, setForm] = useState<PostForm>({ ...EMPTY, ...prefill, stops: trip && prefill.to ? [prefill.to] : [], categories: trip ? TRIP_DEFAULTS : [] })
  const hasRoute = Boolean(prefill.from && prefill.to)
  const [step, setStep] = useState(hasRoute ? 2 : 1)
  const [days, setDays] = useState(false) // The trip's day grid, inside step 2.
  const [route, setRoute] = useState<'from' | 'to' | null>(null)
  const [dateError, setDateError] = useState(false)
  const [noDate, setNoDate] = useState(false)
  const [sameCity, setSameCity] = useState(false)
  const [failure, setFailure] = useState<Failure>(null)
  const [duplicateId, setDuplicateId] = useState<number | null>(null)
  const [leaving, setLeaving] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const key = useRef(newIdempotencyKey())
  const set = (next: Partial<PostForm>) => setForm((current) => ({ ...current, ...next }))
  const dirty = form.size !== '' || form.whenKind !== '' || form.description !== '' || (!hasRoute && Boolean(form.from))

  const toCountry = form.to.split(':')[0]
  const countryCities = locations.data?.countries.find((row) => row.code === toCountry)?.cities ?? []
  const routeText = `${placeLabel(form.from, locations.data)} ← ${(trip ? form.stops : [form.to]).map((key_) => placeLabel(key_, locations.data)).join(trip && form.stops.length > 1 ? ' و ' : '')}`

  const submit = useMutation({
    mutationFn: ({ allowDuplicate }: { allowDuplicate?: boolean }) =>
      unwrap(postJson<MyRequest>('me/requests/', { type: trip ? 'SUPPLY' : 'DEMAND', ...formBody(form, trip), ...(allowDuplicate ? { allow_duplicate: true } : {}) }, { key: key.current })),
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: ME_KEY })
      void client.invalidateQueries({ queryKey: ['courier', 'mine'] })
      track(`courier.${trip ? 'c08' : 'c07'}.submitted`)
      navigate(`/posted/${saved.id}`, { replace: true })
    },
    onError: async (error, variables) => {
      const code = errorCode(error)
      track(`courier.${trip ? 'c08' : 'c07'}.submit_${code}`)
      setFailure(null)
      switch (code) {
        case 'duplicate_request':
          setDuplicateId((error as CourierError).failure.requestId ?? null)
          return setFailure('duplicate')
        case 'same_city':
          setSameCity(true)
          return setStep(1)
        case 'date_invalid':
        case 'date_past':
          setDateError(true)
          return setStep(2)
        case 'flight_date_required':
          setNoDate(true)
          setDays(false)
          return setStep(2)
        case 'category_required':
          return setStep(3)
        case 'offline':
        case 'network_error':
        case 'timeout': {
          // C18.8: the key makes a resend safe. If it had landed, this returns the saved request.
          setRecovering(true)
          const again = await postJson<MyRequest>('me/requests/', { type: trip ? 'SUPPLY' : 'DEMAND', ...formBody(form, trip), ...(variables.allowDuplicate ? { allow_duplicate: true } : {}) }, { key: key.current })
          setRecovering(false)
          if (again.ok) {
            track(`courier.${trip ? 'c08' : 'c07'}.submit_recovered`)
            return navigate(`/posted/${again.data.id}`, { replace: true })
          }
          return setFailure('failed')
        }
        default:
          return setFailure('failed')
      }
    },
  })

  const back = () => {
    if (days) return setDays(false)
    if (step > (hasRoute ? 2 : 1)) return setStep(step - 1)
    if (dirty) return setLeaving(true)
    navigate('/post')
  }

  useEffect(() => {
    track(`courier.${trip ? 'c08' : 'c07'}.step`, { step })
  }, [step, trip])

  if (recovering) return <ResultUnknown onMine={() => navigate('/me/requests')} />

  const overlay = (
    <>
      <RouteSheet
        open={route !== null}
        side={route ?? 'from'}
        value={{ from: form.from, to: form.to }}
        allowAny={false}
        frequent={!trip}
        onClose={() => setRoute(null)}
        onDone={(next) => {
          setRoute(null)
          setSameCity(false)
          set({ from: next.from, to: next.to, stops: next.to ? [next.to] : [] })
          if (!trip && next.from && next.to) setStep(2)
        }}
      />
      <AlertDialog open={leaving} onOpenChange={setLeaving}>
        <AlertDialogContent dir="rtl" data-testid="leave-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>از فرم خارج می‌شوید؟</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="leave-stay">ادامه‌ی فرم</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => navigate('/post')} data-testid="leave-go">
              خروج
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )

  if (step === 1) {
    const frequent = locations.data?.frequent ?? []
    if (trip) {
      return (
        <Wizard
          step={1}
          title="مسیر سفرتان؟"
          onBack={back}
          overlay={overlay}
          foot={
            <Button className="w-full" disabled={!form.from || !form.stops.length} onClick={() => setStep(2)} data-testid="post-next">
              ادامه
            </Button>
          }
        >
          {sameCity ? <ErrorNote text="مقصد باید با مبدا فرق داشته باشد." testId="post-same-city" /> : null}
          <RouteBar value={{ from: form.from, to: form.to }} onOpen={setRoute} empty="انتخاب کنید" />
          {form.to && countryCities.length ? (
            <Section title="مقصدها">
              <ChoiceChips
                label="مقصدها"
                testId="post-stops"
                value={form.stops}
                options={countryCities.filter((city) => `${toCountry}:${city.slug}` !== form.from).map((city) => ({ value: `${toCountry}:${city.slug}`, label: city.name_fa }))}
                onChange={(stops) => set({ stops })}
              />
            </Section>
          ) : null}
        </Wizard>
      )
    }
    return (
      <Wizard step={1} title="مسیر بسته؟" onBack={back} overlay={overlay}>
        {sameCity ? <ErrorNote text="مقصد باید با مبدا فرق داشته باشد." testId="post-same-city" /> : null}
        <Card className="!gap-0 !px-4 !py-1">
          <ItemGroup className="!gap-0">
            {frequent.map((row) => (
              <Item asChild key={`${placeKey(row.origin)}-${placeKey(row.destination)}`}>
                <button
                  type="button"
                  className="w-full text-start"
                  data-testid="post-frequent"
                  onClick={() => {
                    set({ from: placeKey(row.origin), to: placeKey(row.destination) })
                    setStep(2)
                  }}
                >
                  <ItemMedia variant="icon">
                    <Route />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle>
                      <bdi>{`${row.origin.name_fa} ← ${row.destination.name_fa}`}</bdi>
                    </ItemTitle>
                  </ItemContent>
                </button>
              </Item>
            ))}
            <Item asChild>
              <button type="button" className="w-full text-start" onClick={() => setRoute('from')} data-testid="post-other-route">
                <ItemMedia variant="icon">
                  <MapIcon />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>مسیر دیگر</ItemTitle>
                </ItemContent>
              </button>
            </Item>
          </ItemGroup>
        </Card>
      </Wizard>
    )
  }

  if (step === 2) {
    if (trip && days) {
      const monthName = monthLabel(form.month, false)
      const day = form.date
      return (
        <Wizard
          step={2}
          title={`پرواز چندم ${monthName} است؟`}
          onBack={back}
          overlay={overlay}
          foot={
            <Button className="w-full" disabled={!day} onClick={() => setStep(3)} data-testid="post-next">
              ادامه
            </Button>
          }
        >
          <p className="font-bold">
            <bdi>{routeText}</bdi>
          </p>
          <DayGrid month={form.month} value={day} onPick={(picked) => set({ date: `${form.month}-${String(picked).padStart(2, '0')}` })} />
        </Wizard>
      )
    }
    const exactBad = form.whenKind === 'exact' && Boolean(form.date) && !parseTypedDate(form.date)
    const canGo = trip ? form.whenKind !== '' && !(form.whenKind === 'exact' && (!form.date || exactBad)) : form.whenKind !== '' && !(form.whenKind === 'exact' && (!form.date || exactBad))
    return (
      <Wizard
        step={2}
        title={trip ? 'کی پرواز دارید؟' : 'کی باید برسد؟'}
        onBack={back}
        overlay={overlay}
        foot={
          <Button
            className="w-full"
            disabled={!trip && !canGo}
            data-testid="post-next"
            onClick={() => {
              if (trip && !canGo) return setNoDate(true)
              setNoDate(false)
              setDateError(false)
              if (trip && form.whenKind === 'month') {
                set({ whenKind: 'exact', date: '' })
                return setDays(true)
              }
              setStep(3)
            }}
          >
            ادامه
          </Button>
        }
      >
        {noDate ? <ErrorNote text="تاریخ پرواز را انتخاب کنید." testId="post-no-date" /> : null}
        <p className="font-bold">
          <bdi>{routeText}</bdi>
        </p>
        <WhenField form={form} set={(next) => (setDateError(false), setNoDate(false), set(next))} error={dateError || exactBad} trip={trip} />
      </Wizard>
    )
  }

  const categoryOptions = (locations.data?.categories ?? []).map((row) => ({ value: row.code, label: row.name_fa }))
  if (step === 3) {
    const noCategory = trip && !form.categories.length
    return (
      <Wizard
        step={3}
        title={trip ? 'چند کیلو جا دارید؟' : 'چه چیزی و چقدر؟'}
        onBack={back}
        overlay={overlay}
        foot={
          <Button className="w-full" disabled={!form.size || noCategory} onClick={() => setStep(4)} data-testid="post-next">
            ادامه
          </Button>
        }
      >
        <SizeTiles value={form.size} options={SIZES[trip ? 'SUPPLY' : 'DEMAND']} trip={trip} cols={trip ? 4 : 2} required onChange={(size) => set({ size: size as SizeCode })} testId="post-size" />
        {noCategory ? <ErrorNote text="حداقل یک مورد را انتخاب کنید." testId="post-no-category" /> : null}
        <Section title={trip ? 'چه چیزهایی می‌برید؟' : 'داخل بسته'}>
          <ChoiceChips label={trip ? 'چه چیزهایی می‌برید؟' : 'داخل بسته'} value={form.categories} options={categoryOptions} onChange={(categories) => set({ categories })} testId="post-categories" />
        </Section>
        {trip ? (
          <Field>
            <FieldLabel htmlFor="post-refused">چیزهای دیگری که قبول نمی‌کنید</FieldLabel>
            <Input id="post-refused" maxLength={255} placeholder="اختیاری" value={form.refusedOther} onChange={(event) => set({ refusedOther: event.target.value })} data-testid="post-refused" />
          </Field>
        ) : (
          <Field>
            <FieldLabel htmlFor="post-note">توضیح</FieldLabel>
            <Input id="post-note" maxLength={500} placeholder="یک خط توضیح" value={form.description} onChange={(event) => set({ description: event.target.value })} data-testid="post-note" />
          </Field>
        )}
        <ProhibitedButton />
      </Wizard>
    )
  }

  const side = trip ? 'SUPPLY' : 'DEMAND'
  const names = (codes: string[]) => categoriesLabel((locations.data?.categories ?? []).filter((row) => codes.includes(row.code)))
  const refused = (locations.data?.categories ?? []).filter((row) => !form.categories.includes(row.code))
  const whenText = form.whenKind === 'month' ? monthLabel(form.month, !trip && form.month === upcomingMonths()[0]) : form.whenKind === 'open' ? 'عجله ندارم' : form.date
  const busy = submit.isPending
  return (
    <Wizard
      step={4}
      title="همه چیز درست است؟"
      onBack={back}
      overlay={overlay}
      foot={
        failure === 'duplicate' ? (
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={() => navigate(duplicateId ? `/me/requests/${duplicateId}` : '/me/requests')} data-testid="post-see-old">
              دیدن درخواست قبلی
            </Button>
            <Button className="flex-1" disabled={busy} onClick={() => submit.mutate({ allowDuplicate: true })} data-testid="post-new-anyway">
              ثبت تازه
            </Button>
          </div>
        ) : (
          <Button className="w-full" disabled={busy} onClick={() => submit.mutate({})} data-testid="post-submit">
            {busy ? <Spinner /> : failure === 'failed' ? <RefreshCw /> : <Check />}
            {busy ? 'در حال ثبت' : failure === 'failed' ? 'تلاش مجدد' : trip ? 'ثبت و دیدن بسته‌ها' : 'ثبت و دیدن مسافرها'}
          </Button>
        )
      }
    >
      {failure === 'failed' ? <ErrorNote text="ثبت نشد. دوباره تلاش کنید." testId="post-failed" /> : null}
      {failure === 'duplicate' ? (
        <Alert data-testid="post-duplicate">
          <Info />
          <AlertDescription>برای همین مسیر یک درخواست فعال دارید.</AlertDescription>
        </Alert>
      ) : null}
      <Card className="!gap-3 !p-4" data-testid="post-review">
        <FactGrid>
          <Fact icon={Route} label="مسیر" value={<bdi>{routeText}</bdi>} />
          <Fact icon={trip ? Plane : CalendarDays} label={trip ? 'تاریخ پرواز' : 'باید برسد'} value={whenText} />
          <Fact icon={trip ? Luggage : Scale} label={trip ? 'جای خالی' : 'اندازه'} value={sizeName(side, form.size)} />
          {trip ? refused.length ? <Fact icon={Ban} label="قبول نمی‌کند" value={categoriesLabel(refused)} /> : null : form.categories.length ? <Fact icon={Package} label="داخل بسته" value={names(form.categories)} /> : null}
        </FactGrid>
      </Card>
      {trip ? (
        <Alert>
          <ShieldCheck />
          <AlertDescription>هر مسافر حق دارد بسته را باز کند و ببیند.</AlertDescription>
        </Alert>
      ) : (
        <Alert data-testid="post-hint">
          <TriangleAlert />
          <AlertDescription>{PROHIBITED_HINT}</AlertDescription>
        </Alert>
      )}
      {!trip ? <ProhibitedButton /> : null}
    </Wizard>
  )
}

export const PostPackage = () => <PostScreen kind="package" />
export const PostTrip = () => <PostScreen kind="trip" />

export const mineKey = (id: string | number) => ['courier', 'mine', String(id)]

export function useMine(id: string) {
  return useQuery({ queryKey: mineKey(id), queryFn: () => unwrap(courierFetch<MyRequest>(`me/requests/${id}/`)), retry: false })
}
