import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, ChevronLeft, CircleAlert, List, Map as MapIcon, Package, PackageOpen, Plane, RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { Flag } from '@/components/flag'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Spinner } from '@/components/ui/spinner'
import { fa } from '@/lib/format'
import handover from '@/assets/courier/package-handover.jpg'
import sky from '@/assets/courier/plane-sky.jpg'
import { Steps } from '@/screens/post/parts'
import { track } from '@/lib/report'
import { courierFetch, CourierError, errorCode, newIdempotencyKey, postJson, unwrap } from './api'
import { categoriesLabel, monthLabel, placeKey, routeProps, SIZES, sizeName, upcomingMonths, type SizeCode } from './format'
import { ProhibitedButton } from './guide'
import { H1, ME_KEY, Page, Stack } from './page'
import { formBody, parseTypedDate, type PostForm } from './post-form'
import { ChoiceChips, Chip, Section, SizeTiles } from './parts'
import { ChoiceCard, dualText, DualNote, jalaliMonths, Note, ReviewRows, RouteFields, WHEN_ICONS, WhenTiles } from './post-v4'
import { ResultUnknown } from './request'
import { placeLabel, RouteSheet, useLocations } from './route-sheet'
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
  useEffect(() => track('courier_post_chooser_view'), [])
  const go = (path: string) => {
    track('courier_post_type_chosen', { type: path.split('/').pop() })
    navigate(path)
  }
  return (
    <Page tab="post" testId="post-chooser">
      <Stack center>
        <H1>چه چیزی ثبت می‌کنید؟</H1>
        <ChoiceCard photo={handover} icon={Package} title="بسته دارم" onClick={() => go('/post/package')} testId="post-package" />
        <ChoiceCard photo={sky} icon={Plane} title="مسافرم" onClick={() => go('/post/trip')} testId="post-trip" />
        <Card className="!gap-0 !rounded-2xl !p-0">
          <button type="button" className="flex w-full items-center gap-3 px-3.5 py-3 text-sm font-semibold" onClick={() => navigate('/me/requests')} data-testid="post-mine">
            <span className="flex size-[34px] flex-none items-center justify-center rounded-[10px] bg-muted">
              <List className="size-[18px]" aria-hidden />
            </span>
            درخواست‌ها و سفرهای من
            <ChevronLeft className="ms-auto size-4 text-muted-foreground" aria-hidden />
          </button>
        </Card>
      </Stack>
    </Page>
  )
}

const STEP_LABELS = { trip: ['مسیر', 'تاریخ', 'جا', 'مرور'], package: ['مسیر', 'تاریخ', 'اندازه', 'مرور'] }

function Wizard({ step, title, trip, foot, onBack, children, overlay }: { step: number; title: string; trip: boolean; foot?: ReactNode; onBack: () => void; children: ReactNode; overlay?: ReactNode }) {
  return (
    <Page tab={null} back={onBack} foot={foot ? <div className="mx-auto w-full max-w-[512px]">{foot}</div> : undefined} testId={`post-step-${step}`}>
      <Stack center>
        <div data-testid="post-progress">
          <Steps step={step} total={4} label={STEP_LABELS[trip ? 'trip' : 'package'][step - 1]} />
        </div>
        <h2 className="text-xl font-extrabold">{title}</h2>
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
    ...months.map((key, index) => ({ value: `m:${key}`, label: monthLabel(key, !trip && index === 0), sub: jalaliMonths(key) })),
    { value: 'x', label: 'تاریخ دقیق', icon: WHEN_ICONS.exact },
    ...(trip ? [] : [{ value: 'o', label: 'عجله ندارم', icon: WHEN_ICONS.open }]),
  ]
  const value = form.whenKind === 'month' ? `m:${form.month}` : form.whenKind === 'exact' ? 'x' : form.whenKind === 'open' ? 'o' : ''
  return (
    <>
      <WhenTiles
        value={value}
        options={options}
        testId="post-when"
        onChange={(next) => {
          track('courier_post_when_change', { type: trip ? 'trip' : 'package', kind: next === 'x' ? 'exact' : next === 'o' ? 'open' : 'month', month: next.startsWith('m:') ? next.slice(2) : null })
          if (next === 'x') set({ whenKind: 'exact', month: '' })
          else if (next === 'o') set({ whenKind: 'open', month: '', date: '' })
          else set({ whenKind: 'month', month: next.slice(2), date: '' })
        }}
      />
      {form.whenKind === 'exact' ? (
        <Field data-invalid={error || undefined}>
          <FieldLabel htmlFor="post-date">تاریخ</FieldLabel>
          <Input className="ph-no-capture" id="post-date" dir="rtl" placeholder="۱۲ دسامبر" value={form.date} aria-invalid={error || undefined} onChange={(event) => set({ date: event.target.value })} data-testid="post-date" />
          {error ? <FieldError data-testid="date-error">{DATE_ERROR}</FieldError> : null}
        </Field>
      ) : null}
      {form.whenKind === 'exact' && parseTypedDate(form.date) ? <DualNote iso={(parseTypedDate(form.date) as Date).toISOString().slice(0, 10)} /> : null}
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
            className={`!h-10 !w-full !rounded-[10px] !px-0 !text-sm !font-bold ${isThisMonth && day < now.getDate() ? ' opacity-30' : ''}`}
            onClick={() => {
              if (isThisMonth && day < now.getDate()) {
                showToast({ message: 'این روز گذشته است.', icon: 'alert' })
                track('courier.c08.past_day')
                track('courier_post_validation_failed', { type: 'trip', step: 2, field: 'date', code: 'past_day' })
                return
              }
              track('courier_post_day_pick', { type: 'trip', day })
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
  // «ثبت دوباره با همین مشخصات»: the closed request's details, with a new date to pick.
  const again = (useLocation().state as { again?: PostForm } | null)?.again
  const [form, setForm] = useState<PostForm>(again ? { ...EMPTY, ...again } : { ...EMPTY, ...prefill, stops: trip && prefill.to ? [prefill.to] : [], categories: trip ? TRIP_DEFAULTS : [] })
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
      track('courier_post_done', { type: kind, id: saved.id })
      navigate(`/posted/${saved.id}`, { replace: true })
    },
    onError: async (error, variables) => {
      const code = errorCode(error)
      track(`courier.${trip ? 'c08' : 'c07'}.submit_${code}`)
      track('courier_post_refused', { type: kind, code, allow_duplicate: Boolean(variables.allowDuplicate) })
      setFailure(null)
      switch (code) {
        case 'duplicate_request':
          setDuplicateId((error as CourierError).failure.requestId ?? null)
          track('courier_post_duplicate_shown', { type: kind, duplicate_id: (error as CourierError).failure.requestId ?? null })
          return setFailure('duplicate')
        case 'same_city':
          track('courier_post_validation_failed', { type: kind, step: 1, field: 'route', code })
          setSameCity(true)
          return setStep(1)
        case 'date_invalid':
        case 'date_past':
          track('courier_post_validation_failed', { type: kind, step: 2, field: 'date', code })
          setDateError(true)
          return setStep(2)
        case 'flight_date_required':
          track('courier_post_validation_failed', { type: kind, step: 2, field: 'date', code })
          setNoDate(true)
          setDays(false)
          return setStep(2)
        case 'category_required':
          track('courier_post_validation_failed', { type: kind, step: 3, field: 'categories', code })
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
            track('courier_post_done', { type: kind, id: again.data.id, recovered: true })
            return navigate(`/posted/${again.data.id}`, { replace: true })
          }
          track('courier_post_refused', { type: kind, code: again.code, after_resend: true })
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
    if (dirty) {
      track('courier_post_leave_shown', { type: kind, step })
      return setLeaving(true)
    }
    navigate('/post')
  }

  useEffect(() => {
    track(`courier.${trip ? 'c08' : 'c07'}.step`, { step })
  }, [step, trip])

  useEffect(() => {
    track('courier_post_start', { type: kind, prefilled: hasRoute })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    track('courier_post_step', { type: kind, step, days })
  }, [kind, step, days])

  const dateUnparsed = form.whenKind === 'exact' && Boolean(form.date) && !parseTypedDate(form.date)
  useEffect(() => {
    if (dateUnparsed) track('courier_post_validation_failed', { type: kind, step: 2, field: 'date', code: 'date_unparsed' })
  }, [kind, dateUnparsed])

  const categoriesMissing = step === 3 && trip && !form.categories.length
  useEffect(() => {
    if (categoriesMissing) track('courier_post_validation_failed', { type: kind, step: 3, field: 'categories', code: 'required' })
  }, [kind, categoriesMissing])

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
          track('courier_post_route_set', { type: kind, surface: 'sheet', ...routeProps(next.from, next.to) })
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
            <AlertDialogCancel onClick={() => track('courier_post_leave_stay', { type: kind, step })} data-testid="leave-stay">
              ادامه‌ی فرم
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                track('courier_post_leave_go', { type: kind, step })
                navigate('/post')
              }}
              data-testid="leave-go"
            >
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
          trip={trip}
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
          <RouteFields
            from={form.from}
            to={form.to}
            onOpen={setRoute}
            onSwap={
              form.from && form.to
                ? () => {
                    track('courier_post_route_swap', { type: kind })
                    set({ from: form.to, to: form.from, stops: [form.from] })
                  }
                : undefined
            }
          />
          {form.to && countryCities.length ? (
            <Section title="مقصدهای دیگر">
              <ChoiceChips
                label="مقصدها"
                testId="post-stops"
                value={form.stops}
                options={countryCities.filter((city) => `${toCountry}:${city.slug}` !== form.from).map((city) => ({ value: `${toCountry}:${city.slug}`, label: city.name_fa }))}
                onChange={(stops) => {
                  track('courier_post_stops_change', { type: kind, count: stops.length })
                  set({ stops })
                }}
              />
            </Section>
          ) : null}
        </Wizard>
      )
    }
    return (
      <Wizard trip={trip} step={1} title="مسیر بسته؟" onBack={back} overlay={overlay}>
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
                    track('courier_post_route_set', { type: kind, surface: 'frequent', ...routeProps(placeKey(row.origin), placeKey(row.destination)) })
                    set({ from: placeKey(row.origin), to: placeKey(row.destination) })
                    setStep(2)
                  }}
                >
                  <ItemMedia>
                    <Flag code={row.origin.country} />
                  </ItemMedia>
                  <ItemContent>
                    <ItemTitle className="!flex !items-center !gap-1.5">
                      <bdi>{row.origin.name_fa}</bdi>
                      <ArrowLeft className="size-4 text-muted-foreground" aria-hidden />
                      <Flag code={row.destination.country} />
                      <bdi>{row.destination.name_fa}</bdi>
                    </ItemTitle>
                  </ItemContent>
                  <ChevronLeft className="size-4 text-muted-foreground" aria-hidden />
                </button>
              </Item>
            ))}
            <Item asChild>
              <button
                type="button"
                className="w-full text-start"
                onClick={() => {
                  track('courier_post_other_route_tap', { type: kind })
                  setRoute('from')
                }}
                data-testid="post-other-route"
              >
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
          trip={trip}
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
          {day ? <DualNote iso={day} /> : null}
        </Wizard>
      )
    }
    const exactBad = form.whenKind === 'exact' && Boolean(form.date) && !parseTypedDate(form.date)
    const canGo = trip ? form.whenKind !== '' && !(form.whenKind === 'exact' && (!form.date || exactBad)) : form.whenKind !== '' && !(form.whenKind === 'exact' && (!form.date || exactBad))
    return (
      <Wizard
        trip={trip}
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
              if (trip && !canGo) {
                track('courier_post_validation_failed', { type: kind, step: 2, field: 'date', code: exactBad ? 'date_unparsed' : 'required' })
                return setNoDate(true)
              }
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
        trip={trip}
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
        <SizeTiles value={form.size} options={SIZES[trip ? 'SUPPLY' : 'DEMAND']} trip={trip} cols={trip ? 4 : 2} required onChange={(size) => (track('courier_post_size_pick', { type: kind, size }), set({ size: size as SizeCode }))} testId="post-size" />
        {noCategory ? <ErrorNote text="حداقل یک مورد را انتخاب کنید." testId="post-no-category" /> : null}
        <Section title={trip ? 'چه چیزهایی می‌برید؟' : 'داخل بسته'}>
          <ChoiceChips label={trip ? 'چه چیزهایی می‌برید؟' : 'داخل بسته'} value={form.categories} options={categoryOptions} onChange={(categories) => (track('courier_post_categories_change', { type: kind, count: categories.length }), set({ categories }))} testId="post-categories" />
        </Section>
        {trip ? (
          <>
            <Field>
              <FieldLabel htmlFor="post-refused">چیزهای دیگری که قبول نمی‌کنید</FieldLabel>
              <Input className="ph-no-capture" id="post-refused" maxLength={255} placeholder="اختیاری" value={form.refusedOther} onChange={(event) => set({ refusedOther: event.target.value })} data-testid="post-refused" />
            </Field>
            <Field>
              <FieldLabel htmlFor="post-note">توضیح</FieldLabel>
              <Input className="ph-no-capture" id="post-note" maxLength={500} placeholder="اختیاری" value={form.description} onChange={(event) => set({ description: event.target.value })} data-testid="post-note" />
            </Field>
          </>
        ) : (
          <Field>
            <FieldLabel htmlFor="post-note">توضیح</FieldLabel>
            <Input className="ph-no-capture" id="post-note" maxLength={500} placeholder="یک خط توضیح" value={form.description} onChange={(event) => set({ description: event.target.value })} data-testid="post-note" />
          </Field>
        )}
        <ProhibitedButton />
      </Wizard>
    )
  }

  const side = trip ? 'SUPPLY' : 'DEMAND'
  const names = (codes: string[]) => categoriesLabel((locations.data?.categories ?? []).filter((row) => codes.includes(row.code)))
  const refused = (locations.data?.categories ?? []).filter((row) => !form.categories.includes(row.code))
  const carried = (locations.data?.categories ?? []).filter((row) => form.categories.includes(row.code))
  const chips = (rows: typeof carried, tone: 'ok' | 'no') => (
    <span className="flex flex-wrap justify-end gap-1">
      {rows.map((row) => (
        <Chip key={row.code} tone={tone}>
          {row.name_fa}
        </Chip>
      ))}
    </span>
  )
  const reviewExtra: Array<[string, ReactNode]> = trip
    ? [['می‌برد', chips(carried, 'ok')], ...(refused.length ? ([['قبول نمی‌کند', chips(refused, 'no')]] as Array<[string, ReactNode]>) : [])]
    : form.categories.length
      ? [['داخل بسته', names(form.categories)]]
      : []
  const whenText = form.whenKind === 'month' ? monthLabel(form.month, !trip && form.month === upcomingMonths()[0]) : form.whenKind === 'open' ? 'عجله ندارم' : /^\d{4}-\d{2}-\d{2}$/.test(form.date) ? dualText(form.date) : form.date
  const busy = submit.isPending
  const submitProps = { type: kind, when_kind: form.whenKind, size: form.size, stops: form.stops.length, categories: form.categories.length, description_length: form.description.length, refused_length: form.refusedOther.length, ...routeProps(form.from, trip ? (form.stops[0] ?? '') : form.to) }
  return (
    <Wizard
      trip={trip}
      step={4}
      title="همه چیز درست است؟"
      onBack={back}
      overlay={overlay}
      foot={
        (
          <Button
            className="w-full"
            disabled={busy}
            onClick={() => {
              track('courier_post_submit', { ...submitProps, allow_duplicate: false })
              if (failure === 'failed') track('courier_retry_tap', { where: 'post_submit' })
              submit.mutate({})
            }}
            data-testid="post-submit"
          >
            {busy ? <Spinner /> : failure === 'failed' ? <RefreshCw /> : <Check />}
            {busy ? 'در حال ثبت' : failure === 'failed' ? 'تلاش مجدد' : trip ? 'ثبت و دیدن بسته‌ها' : 'ثبت و دیدن مسافرها'}
          </Button>
        )
      }
    >
      {failure === 'failed' ? <ErrorNote text="ثبت نشد. دوباره تلاش کنید." testId="post-failed" /> : null}
      <AlertDialog open={failure === 'duplicate'} onOpenChange={(open) => (open ? undefined : setFailure(null))}>
        <AlertDialogContent dir="rtl" data-testid="post-duplicate">
          <AlertDialogHeader>
            <AlertDialogTitle>برای همین مسیر یک درخواست فعال دارید.</AlertDialogTitle>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              onClick={() => {
                track('courier_post_duplicate_see_old', { type: kind, duplicate_id: duplicateId })
                navigate(duplicateId ? `/me/requests/${duplicateId}` : '/me/requests')
              }}
              data-testid="post-see-old"
            >
              دیدن قبلی
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={() => {
                track('courier_post_submit', { ...submitProps, allow_duplicate: true })
                submit.mutate({ allowDuplicate: true })
              }}
              data-testid="post-new-anyway"
            >
              ثبت تازه
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ReviewRows
        testId="post-review"
        rows={[
          ['مسیر', <bdi key="r">{routeText}</bdi>],
          [trip ? 'پرواز' : 'باید برسد', <span key="w" className="ph-no-capture">{whenText}</span>],
          [trip ? 'جای خالی' : 'اندازه', sizeName(side, form.size)],
          ...reviewExtra,
        ]}
      />
      {trip ? (
        <Note icon={PackageOpen}>هر مسافر حق دارد بسته را باز کند و ببیند.</Note>
      ) : (
        <Note icon={TriangleAlert} tone="no" testId="post-hint">
          {PROHIBITED_HINT}
        </Note>
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
