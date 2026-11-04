import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banknote, Bell, CalendarDays, CheckCheck, CircleAlert, ListPlus, Luggage, Package, Pencil, Plane, Plus, Route, Scale, TimerOff, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { fa, formatDate } from '@/lib/format'
import { track } from '@/lib/report'
import { AlertSheet } from './alert-sheet'
import { courierFetch, errorCode, patchJson, postJson, unwrap } from './api'
import { query } from './filters'
import { categoriesLabel, dateLabel, isTrip, monthLabel, nounFor, packageSize, placeKey, routeLabel, SIZES, tripSpace, upcomingMonths, type Side, type SizeCode } from './format'
import { H1, ME_KEY, Page, Stack } from './page'
import { CardGrid, ChoiceChips, Fact, FactGrid, KindBadge, RequestCardView, RequestSkeleton, Section, Segmented, SingleChips, SizeTiles } from './parts'
import { mineKey, useMine } from './post'
import { formBody, parseTypedDate, type PostForm } from './post-form'
import { useCounterparts } from './posted'
import { ResultUnknown } from './request'
import { RouteBar, RouteSheet, useLocations } from './route-sheet'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type MyRequest = components['schemas']['MyRequest']
type MyRequests = components['schemas']['MyRequests']

const LIST_KEY = (state: string) => ['courier', 'mine', 'list', state]

export function StatusBadge({ status }: { status: MyRequest['status'] }) {
  switch (status) {
    case 'ACTIVE':
      return <Badge data-testid="status-badge">فعال</Badge>
    case 'COMPLETED':
      return (
        <Badge variant="secondary" data-testid="status-badge">
          <CheckCheck />
          تمام‌شده
        </Badge>
      )
    case 'CANCELLED':
      return (
        <Badge variant="outline" data-testid="status-badge">
          لغو شده
        </Badge>
      )
    case 'EXPIRED':
      return (
        <Badge variant="outline" data-testid="status-badge">
          <TimerOff />
          منقضی
        </Badge>
      )
    default:
      return (
        <Badge variant="outline" data-testid="status-badge">
          بسته شد
        </Badge>
      )
  }
}

const postAgainUrl = (item: MyRequest) => `/post/${isTrip(item) ? 'trip' : 'package'}${query({ from: placeKey(item.origin), to: item.stops[0] ? placeKey(item.stops[0]) : '' })}`

function useMineList(state: 'active' | 'archive') {
  return useQuery({ queryKey: LIST_KEY(state), queryFn: () => unwrap(courierFetch<MyRequests>(`me/requests/${query({ state })}`)) })
}

// C12.1, C12.2, C12.3
export function MineScreen() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<'active' | 'archive'>('active')
  const active = useMineList('active')
  const archive = useMineList('archive')
  const shown = tab === 'active' ? active : archive
  const rows = shown.data?.items ?? []
  const tabs = (
    <Segmented
      label="درخواست‌های من"
      value={tab}
      onChange={setTab}
      options={[
        { value: 'active', label: `فعال (${fa(active.data?.items.length ?? 0)})` },
        { value: 'archive', label: `بایگانی (${fa(archive.data?.items.length ?? 0)})` },
      ]}
    />
  )
  const suggestions = (item: MyRequest) => (item.status !== 'ACTIVE' || item.suggestions === null ? undefined : item.suggestions > 0 ? `${fa(item.suggestions)} پیشنهاد` : 'هنوز تطبیقی نیست')
  let body: ReactNode
  if (shown.isLoading) {
    body = (
      <CardGrid>
        <RequestSkeleton />
        <RequestSkeleton />
      </CardGrid>
    )
  } else if (shown.isError) {
    body = failedScreen(shown.error, () => void shown.refetch())
  } else if (!rows.length) {
    body =
      tab === 'active' ? (
        <StateBox icon={ListPlus} title="هنوز درخواست فعالی ندارید" testId="mine-empty">
          <div className="flex w-full flex-col gap-2">
            <Button className="w-full" onClick={() => navigate('/post/package')} data-testid="mine-post-package">
              <Package />
              ثبت بسته
            </Button>
            <Button variant="outline" className="w-full" onClick={() => navigate('/post/trip')} data-testid="mine-post-trip">
              <Luggage />
              ثبت سفر
            </Button>
          </div>
        </StateBox>
      ) : (
        <StateBox icon={ListPlus} title="چیزی در بایگانی نیست" testId="mine-archive-empty" />
      )
  } else {
    body = (
      <CardGrid>
        {rows.map((item) => (
          <RequestCardView key={item.id} card={item} status={<StatusBadge status={item.status} />} matches={suggestions(item)} dim={tab === 'archive'} onOpen={() => navigate(`/me/requests/${item.id}`)} />
        ))}
      </CardGrid>
    )
  }
  return (
    <Page tab="me" back="/me" testId="mine">
      <Stack gap="gap-3">
        <H1>درخواست‌ها و سفرهای من</H1>
        {tabs}
        {body}
      </Stack>
    </Page>
  )
}

function OwnSide({ item }: { item: MyRequest }) {
  const trip = isTrip(item)
  return (
    <Card className="!gap-2 !p-4" data-testid="own-side">
      <div className="flex items-center gap-1.5">
        <KindBadge card={item} />
        <StatusBadge status={item.status} />
      </div>
      <H1>
        <bdi>{routeLabel(item)}</bdi>
      </H1>
      <FactGrid>
        {trip ? <Fact icon={Plane} label="تاریخ پرواز" value={item.date ? formatDate(item.date) : 'نامشخص'} /> : <Fact icon={CalendarDays} label="باید برسد" value={dateLabel(item)} />}
        {trip ? <Fact icon={Luggage} label="جای خالی" value={item.capacity_kg ? tripSpace(item.capacity_kg) : 'نامشخص'} /> : <Fact icon={Scale} label="وزن" value={item.weight_kg ? packageSize(item.weight_kg) : 'نامشخص'} />}
        {item.categories.length ? <Fact icon={Package} label={trip ? 'می‌برد' : 'داخل بسته'} value={categoriesLabel(item.categories)} /> : null}
        <Fact icon={Banknote} label="هزینه" value="توافقی" />
      </FactGrid>
    </Card>
  )
}

function Columns({ side, main }: { side: ReactNode; main: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 p-4 md:grid md:grid-cols-2 md:items-start md:gap-6 md:px-6 md:py-5">
      <div className="min-w-0">{side}</div>
      <div className="flex min-w-0 flex-col gap-3">{main}</div>
    </div>
  )
}

// C12.4 to C12.10. No contact and no contact button on someone's own request.
export function MineDetailScreen() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const location = useLocation()
  const mine = useMine(id)
  const item = mine.data
  const open = item?.status === 'ACTIVE' && !item.date_passed
  const counterparts = useCounterparts(id, Boolean(open))
  const [closing, setClosing] = useState(false)
  const [refused, setRefused] = useState(Boolean((location.state as { notEditable?: boolean } | null)?.notEditable))
  const [alert, setAlert] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['courier', 'mine'] })
    void client.invalidateQueries({ queryKey: ME_KEY })
  }
  const close = useMutation({
    mutationFn: () => unwrap(postJson<MyRequest>(`me/requests/${id}/close/`, { package_sent: false })),
    onSuccess: (saved) => {
      client.setQueryData(mineKey(id), saved)
      refresh()
      track('courier.c12.closed')
    },
    onError: async (error) => {
      const code = errorCode(error)
      if (code === 'not_editable') {
        setRefused(true)
        return refresh()
      }
      if (code === 'offline' || code === 'network_error' || code === 'timeout') {
        // A second close says not_editable, so a read is what tells.
        setUnknown(true)
        await mine.refetch()
        setUnknown(false)
        return
      }
      showToast({ message: 'بسته نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => close.mutate() } })
    },
  })
  if (unknown) return <ResultUnknown onMine={() => navigate('/me/requests')} />
  if (mine.isLoading) {
    return (
      <Page tab={null} back="/me/requests" testId="mine-detail-loading">
        <Stack center>
          <Skeleton className="h-40 w-full" />
        </Stack>
      </Page>
    )
  }
  if (!item) {
    return (
      <Page tab={null} back="/me/requests">
        {failedScreen(mine.error, () => void mine.refetch())}
      </Page>
    )
  }
  const side: Side = isTrip(item) ? 'SUPPLY' : 'DEMAND'
  const noun = nounFor(side)
  const dateGone = item.date_passed || (item.status === 'EXPIRED' && isTrip(item))
  if (!open) {
    const again = dateGone && isTrip(item) ? 'ثبت سفر تازه' : 'ثبت دوباره'
    return (
      <Page tab={null} back="/me/requests" testId="mine-closed">
        <Stack center gap="gap-3">
          {refused ? (
            <Alert variant="destructive" data-testid="mine-not-editable">
              <CircleAlert />
              <AlertDescription>این درخواست دیگر فعال نیست و تغییر نمی‌کند.</AlertDescription>
            </Alert>
          ) : dateGone ? (
            <Alert data-testid="mine-date-passed">
              <TimerOff />
              <AlertDescription>{isTrip(item) ? 'تاریخ این سفر گذشته است.' : 'تاریخ این درخواست گذشته است.'}</AlertDescription>
            </Alert>
          ) : null}
          <OwnSide item={item} />
          <Button variant={dateGone ? 'default' : 'outline'} className="w-full" onClick={() => navigate(postAgainUrl(item))} data-testid="mine-post-again">
            <Plus />
            {again}
          </Button>
        </Stack>
      </Page>
    )
  }
  const rows = counterparts.data?.items ?? []
  const tools = (
    <div className="flex gap-2">
      <Button variant="outline" className="flex-1" onClick={() => navigate(`/me/requests/${id}/edit`)} data-testid="mine-edit">
        <Pencil />
        ویرایش
      </Button>
      <Button variant="outline" className="flex-1" disabled={close.isPending} onClick={() => setClosing(true)} data-testid="mine-close">
        {close.isPending ? <Spinner /> : <X />}
        بستن
      </Button>
    </div>
  )
  return (
    <Page tab={null} back="/me/requests" testId="mine-detail">
      <Columns
        side={<OwnSide item={item} />}
        main={
          <>
            {counterparts.isLoading ? (
              <RequestSkeleton />
            ) : counterparts.isError ? (
              <Alert data-testid="mine-matches-failed">
                <CircleAlert />
                <AlertDescription className="flex w-full items-center justify-between gap-2">
                  فهرست پیشنهادها بارگذاری نشد
                  <Button variant="outline" size="sm" onClick={() => void counterparts.refetch()}>
                    تلاش مجدد
                  </Button>
                </AlertDescription>
              </Alert>
            ) : rows.length ? (
              <Section title="پیشنهادها" side={`${fa(rows.length)} ${noun === 'مسافر' ? 'مسافر' : 'بسته'}`}>
                <div className="flex flex-col gap-3" data-testid="mine-suggestions">
                  {rows.map((row) => (
                    <RequestCardView key={row.id} card={row} onOpen={() => navigate(`/request/${row.id}`)} />
                  ))}
                </div>
              </Section>
            ) : (
              <StateBox icon={Route} title="هنوز تطبیقی نیست" testId="mine-no-match">
                <Button variant="outline" onClick={() => setAlert(true)} data-testid="mine-alert">
                  <Bell />
                  گوش به زنگ
                </Button>
              </StateBox>
            )}
            {tools}
          </>
        }
      />
      <AlertDialog open={closing} onOpenChange={setClosing}>
        <AlertDialogContent dir="rtl" data-testid="mine-close-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>بستن درخواست</AlertDialogTitle>
            <AlertDialogDescription>پس از بستن، این درخواست دیگر فعال نخواهد بود.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>بازگشت</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => close.mutate()} data-testid="mine-close-go">
              بستن
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AlertSheet open={alert} onClose={() => setAlert(false)} draft={{ side, from: placeKey(item.origin), to: item.stops[0] ? placeKey(item.stops[0]) : '', month: '', size: '' }} />
    </Page>
  )
}

// A request from the old form holds exact kilos, so it takes the size it fits in.
function sizeOf(weight: string | null): SizeCode | '' {
  if (!weight) return ''
  const value = Number(weight)
  return value <= 0.5 ? 'doc' : value <= 1 ? '1' : value <= 3 ? '3' : '3p'
}

function formOf(item: MyRequest): PostForm {
  const weight = isTrip(item) ? item.capacity_kg : item.weight_kg
  const kind = !item.date ? 'open' : item.date_kind === 'by' ? 'month' : 'exact'
  return {
    from: placeKey(item.origin),
    to: item.stops[0] ? placeKey(item.stops[0]) : '',
    stops: item.stops.map(placeKey),
    whenKind: kind,
    month: kind === 'month' ? item.date!.slice(0, 7) : '',
    date: kind === 'exact' ? item.date! : '',
    size: sizeOf(weight),
    categories: item.categories.map((row) => row.code),
    refusedOther: item.excluded_other_text,
    description: item.description,
  }
}

function EditForm({ item }: { item: MyRequest }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const locations = useLocations()
  const trip = isTrip(item)
  const [form, setForm] = useState(() => formOf(item))
  const [route, setRoute] = useState<'from' | 'to' | null>(null)
  const [dateError, setDateError] = useState(false)
  const [failed, setFailed] = useState(false)
  const [same, setSame] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const set = (next: Partial<PostForm>) => setForm((current) => ({ ...current, ...next }))
  const exactBad = form.whenKind === 'exact' && Boolean(form.date) && !parseTypedDate(form.date)
  const months = upcomingMonths()
  const value = form.whenKind === 'month' ? `m:${form.month}` : form.whenKind === 'exact' ? 'x' : 'o'
  const options = [
    ...(form.month && !months.includes(form.month) ? [form.month] : []).concat(months).map((key, index) => ({ value: `m:${key}`, label: monthLabel(key, !trip && index === 0) })),
    { value: 'x', label: 'تاریخ دقیق' },
    ...(trip ? [] : [{ value: 'o', label: 'عجله ندارم' }]),
  ]
  const leave = (state?: object) => navigate(`/me/requests/${item.id}`, { replace: true, state })
  const save = useMutation({
    mutationFn: () => unwrap(patchJson<MyRequest>(`me/requests/${item.id}/`, formBody(form, trip))),
    onSuccess: (saved) => {
      client.setQueryData(mineKey(item.id), saved)
      void client.invalidateQueries({ queryKey: ['courier', 'mine'] })
      track('courier.c12.edited')
      leave()
    },
    onError: async (error) => {
      const code = errorCode(error)
      track(`courier.c12.edit_${code}`)
      setFailed(false)
      switch (code) {
        case 'date_invalid':
        case 'date_past':
          return setDateError(true)
        case 'same_city':
          return setSame(true)
        case 'not_editable':
          void client.invalidateQueries({ queryKey: ['courier', 'mine'] })
          return leave({ notEditable: true })
        case 'offline':
        case 'network_error':
        case 'timeout':
          setUnknown(true)
          await client.invalidateQueries({ queryKey: mineKey(item.id) })
          setUnknown(false)
          return setFailed(true)
        default:
          return setFailed(true)
      }
    },
  })
  if (unknown) return <ResultUnknown onMine={() => navigate('/me/requests')} />
  const canSave = form.from && (trip ? form.stops.length : form.to) && form.size && !exactBad && !(form.whenKind === 'exact' && !form.date) && !(trip && !form.categories.length)
  return (
    <Page
      tab={null}
      back={`/me/requests/${item.id}`}
      testId="mine-edit-form"
      foot={
        <div className="mx-auto w-full max-w-[512px]">
          <Button className="w-full" disabled={!canSave || save.isPending} onClick={() => save.mutate()} data-testid="edit-save">
            {save.isPending ? <Spinner /> : null}
            ذخیره
          </Button>
        </div>
      }
    >
      <Stack center>
        <H1>ویرایش درخواست</H1>
        {failed ? (
          <Alert variant="destructive" data-testid="edit-failed">
            <CircleAlert />
            <AlertDescription>ثبت نشد. دوباره تلاش کنید.</AlertDescription>
          </Alert>
        ) : null}
        {same ? (
          <Alert variant="destructive" data-testid="edit-same-city">
            <CircleAlert />
            <AlertDescription>مقصد باید با مبدا فرق داشته باشد.</AlertDescription>
          </Alert>
        ) : null}
        <RouteBar value={{ from: form.from, to: form.to }} onOpen={setRoute} empty="انتخاب کنید" />
        <Section title={trip ? 'کی پرواز دارید؟' : 'کی باید برسد؟'}>
          <SingleChips
            label="زمان"
            value={value}
            options={options}
            required
            testId="edit-when"
            onChange={(next) => {
              setDateError(false)
              if (next === 'x') set({ whenKind: 'exact', month: '' })
              else if (next === 'o') set({ whenKind: 'open', month: '', date: '' })
              else set({ whenKind: 'month', month: next.slice(2), date: '' })
            }}
          />
          {form.whenKind === 'exact' ? (
            <Field data-invalid={dateError || exactBad || undefined}>
              <FieldLabel htmlFor="edit-date">تاریخ</FieldLabel>
              <Input
                id="edit-date"
                placeholder="۱۲ دسامبر"
                value={form.date}
                onChange={(event) => {
                  setDateError(false)
                  set({ date: event.target.value })
                }}
                data-testid="edit-date"
              />
              {dateError || exactBad ? <FieldError data-testid="edit-date-error">این تاریخ را نفهمیدم. مثل «۱۲ دسامبر» بنویسید، از امروز تا یک سال بعد.</FieldError> : null}
            </Field>
          ) : null}
        </Section>
        <Section title="اندازه">
          <SizeTiles value={form.size} options={SIZES[trip ? 'SUPPLY' : 'DEMAND']} trip={trip} cols={trip ? 4 : 2} required onChange={(size) => set({ size: size as SizeCode })} testId="edit-size" />
        </Section>
        {trip ? (
          <Section title="چه چیزهایی می‌برید؟">
            <ChoiceChips label="چه چیزهایی می‌برید؟" value={form.categories} options={(locations.data?.categories ?? []).map((row) => ({ value: row.code, label: row.name_fa }))} onChange={(categories) => set({ categories })} testId="edit-categories" />
          </Section>
        ) : null}
        <Field>
          <FieldLabel htmlFor="edit-note">توضیح</FieldLabel>
          <Input id="edit-note" maxLength={500} value={form.description} onChange={(event) => set({ description: event.target.value })} data-testid="edit-note" />
        </Field>
      </Stack>
      <RouteSheet
        open={route !== null}
        side={route ?? 'from'}
        value={{ from: form.from, to: form.to }}
        allowAny={false}
        frequent={false}
        onClose={() => setRoute(null)}
        onDone={(next) => {
          setRoute(null)
          setSame(false)
          set({ from: next.from, to: next.to, stops: next.to ? [next.to] : [] })
        }}
      />
    </Page>
  )
}

// C12.6: one screen, the type fixed. Closed while open here: the server says not_editable and the detail shows C12.9.
export function MineEditScreen() {
  const { id = '' } = useParams()
  const mine = useMine(id)
  if (mine.isLoading) {
    return (
      <Page tab={null} back={`/me/requests/${id}`} testId="mine-edit-loading">
        <Stack center>
          <Skeleton className="h-60 w-full" />
        </Stack>
      </Page>
    )
  }
  if (!mine.data) {
    return (
      <Page tab={null} back={`/me/requests/${id}`}>
        {failedScreen(mine.error, () => void mine.refetch())}
      </Page>
    )
  }
  return <EditForm item={mine.data} />
}
