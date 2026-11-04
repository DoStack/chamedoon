import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { CircleAlert, CircleOff, EllipsisVertical, FileText, MapPin, Pencil, Plus, RefreshCw, Trash, Undo2 } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { Availability, MineForm, MineList, MineRow, PostResult } from '@/api/post-types'
import { needsInternetToast, rateLimitedToast, showToast } from '@/components/app-toast'
import { CardSkeleton, ON_PHOTO, Photo, Price, wantedWhat } from '@/components/listing-card'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Spinner } from '@/components/ui/spinner'
import { ApiFailureError, meFetch, unwrap, type ApiResult } from '@/lib/api'
import { useSavedCity } from '@/lib/city'
import { cardPlace, cardTitle, currency, fa, relativeTime, typeLabel } from '@/lib/format'
import { amount, fromForm, priceOk, type Draft } from '@/lib/post-draft'
import { closePosting } from '@/lib/posting'
import { track } from '@/lib/report'
import { OfflineBar, SlowNote, useAfter, useOnline } from '../global-states'
import { OfferWizard } from './offer'
import { Bad, ConfirmSheet, FootColumn, GoButton, Labeled, MoneyInput, PlaceText, PostPage, Stack } from './parts'
import { MINE_KEY } from './use-publish'
import { WantedForm } from './wanted'

const JSON_HEADERS = { 'Content-Type': 'application/json' }
const LIST_KEY = [...MINE_KEY, 'list']

type Pages = InfiniteData<MineList, string | null>
type Action = 'close' | 'restore' | 'renew'

const REASON: Record<string, string> = { required: 'قیمت ندارد', too_short: 'توضیحاتش کوتاه است', contact_in_text: 'متنش شماره یا آیدی دارد', no_contact: 'راه تماس ندارد' }
const FIXABLE = new Set(Object.keys(REASON))
const reasonText = (code: string | null) => REASON[code ?? ''] ?? 'آگهی خانه نیست'

function failure(result: ApiResult<unknown>, retry: () => void, client: ReturnType<typeof useQueryClient>) {
  if (result.ok) return
  track('mine_action_failed', { code: result.code })
  switch (result.code) {
    case 'rate_limited':
      rateLimitedToast(retry, 'mine-rate')
      break
    case 'offline':
    case 'network_error':
      needsInternetToast()
      break
    case 'feature_off':
      closePosting('posting')
      break
    case 'listing_gone':
    case 'status_not_allowed':
    case 'confirm_not_allowed':
      void client.invalidateQueries({ queryKey: MINE_KEY })
      showToast({ message: 'این آگهی تغییر کرده است', icon: 'alert' })
      break
    default:
      showToast({ message: 'ذخیره نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: retry } })
  }
}

function rowTitle(row: MineRow) {
  return row.kind === 'wanted' ? wantedWhat(row.card) : `${cardTitle({ ...row.card, size_m2: null })} · ${cardPlace(row.card).split(' · ')[0]}`
}

// F20#8
function RowMenu({ row, onEdit, onClose, onDelete }: { row: MineRow; onEdit: () => void; onClose: () => void; onDelete: () => void }) {
  return (
    <DropdownMenu dir="rtl">
      {/* Not asChild on Button: it takes no ref, and the menu would have nothing to sit next to. */}
      <DropdownMenuTrigger aria-label="گزینه‌های آگهی" className={`${buttonVariants({ variant: 'outline', size: 'icon' })} ${ON_PHOTO}`} data-testid="row-menu">
        <EllipsisVertical />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" data-testid="row-menu-items">
        {row.can_edit ? (
          <DropdownMenuItem onSelect={onEdit} data-testid="menu-edit">
            <Pencil />
            ویرایش
          </DropdownMenuItem>
        ) : null}
        {row.owner_state !== 'closed' ? (
          <DropdownMenuItem onSelect={onClose} data-testid="menu-close">
            <CircleOff />
            بستن آگهی
          </DropdownMenuItem>
        ) : null}
        <DropdownMenuItem variant="destructive" onSelect={onDelete} data-testid="menu-delete">
          <Trash />
          حذف
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

const STATE_BADGE: Record<MineRow['owner_state'], ReactNode> = {
  live: <Badge className="absolute top-3 right-3">منتشر شده</Badge>,
  in_review: (
    <Badge variant="outline" className={`absolute top-3 right-3 ${ON_PHOTO}`}>
      در انتظار بررسی
    </Badge>
  ),
  rejected: (
    <Badge variant="destructive" className="absolute top-3 right-3">
      رد شد
    </Badge>
  ),
  closed: <Badge className="absolute top-3 right-3 bg-foreground text-background">بسته شد</Badge>,
}

function OwnerLine({ row }: { row: MineRow }) {
  if (row.owner_state === 'rejected') {
    return (
      <span className="flex items-center gap-1 text-destructive">
        <CircleAlert className="size-3.5 flex-none" aria-hidden />
        {reasonText(row.reject_code)}
      </span>
    )
  }
  return <>{relativeTime(row.updated_at)}</>
}

// The card as its owner sees it: status in place of the bookmark's corner, and no bookmark (F20 rule).
function OwnerCard({ row, actions, busy, onEdit, onClose, onDelete, hideActions }: { row: MineRow; actions: ReactNode; busy: boolean; onEdit: () => void; onClose: () => void; onDelete: () => void; hideActions?: boolean }) {
  const card = row.card
  const closed = row.owner_state === 'closed'
  const to = row.owner_state === 'rejected' ? `/mine/${row.id}` : `/listing/${row.id}`
  const menu = (
    <div className="absolute top-2 left-2 z-10">
      <RowMenu row={row} onEdit={onEdit} onClose={onClose} onDelete={onDelete} />
    </div>
  )
  const foot = !hideActions && actions ? <div className="px-4 pb-4">{actions}</div> : null
  if (row.kind === 'wanted') {
    return (
      <div className="grid min-w-0 flex-none" data-testid="mine-row" data-state={row.owner_state} data-kind="wanted" aria-busy={busy}>
        <Card className="relative h-full gap-1 p-0">
          <Link to={to} className="flex min-w-0 flex-col gap-1 p-4 outline-none" data-testid="mine-link">
            <div className="mb-1">{closed ? <Badge className="bg-foreground text-background">بسته شد</Badge> : row.owner_state === 'in_review' ? <Badge variant="outline">در انتظار بررسی</Badge> : <Badge>منتشر شده</Badge>}</div>
            <div className="text-base leading-7 font-bold">
              دنبال خانه
              {card.price_max != null ? (
                <>
                  {' '}
                  · بودجه تا {fa(card.price_max)} <bdi dir="ltr">{currency(card.price_currency)}</bdi>
                </>
              ) : null}
            </div>
            <div className="truncate text-sm font-bold">{typeLabel(card)}</div>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <MapPin className="size-3.5 flex-none" aria-hidden />
              <span className="truncate">{cardPlace(card)}</span>
            </div>
            <div className="truncate text-[13px] text-muted-foreground">{relativeTime(row.updated_at)}</div>
          </Link>
          {foot}
          {menu}
        </Card>
      </div>
    )
  }
  return (
    <div className="grid min-w-0 flex-none" data-testid="mine-row" data-state={row.owner_state} data-kind="offer" aria-busy={busy}>
      <Card className="relative h-full gap-0 py-0">
        <Link to={to} className="grid min-w-0 rounded-t-xl outline-none" data-testid="mine-link">
          <Photo src={card.thumb} count={card.photo_count > 0 ? fa(card.photo_count) : undefined} dim={closed}>
            {STATE_BADGE[row.owner_state]}
          </Photo>
          <div className="flex min-w-0 flex-col gap-1 px-4 pt-3 pb-4">
            {card.price_amount == null ? <div className="text-base leading-7 font-bold text-muted-foreground">بدون قیمت</div> : <Price card={card} />}
            <div className="truncate text-sm font-bold">{cardTitle(card)}</div>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <MapPin className="size-3.5 flex-none" aria-hidden />
              <span className="truncate">
                <PlaceText text={cardPlace(card)} />
              </span>
            </div>
            <div className="truncate text-[13px] text-muted-foreground">
              <OwnerLine row={row} />
            </div>
          </div>
        </Link>
        {foot}
        {menu}
      </Card>
    </div>
  )
}

function Ask({ question, yes, no, busy, onYes, onNo }: { question: string; yes: string; no: string; busy: boolean; onYes: () => void; onNo: () => void }) {
  return (
    <div className="col-span-full grid min-w-0 flex-none" data-testid="owner-check">
      <Card className="gap-3 p-4">
        <p className="m-0 text-[15px] font-bold">{question}</p>
        <div className="flex items-center gap-2">
          <Button className="min-w-0 flex-1" disabled={busy} onClick={onYes} data-testid="check-yes">
            {yes}
          </Button>
          <Button variant="outline" className="min-w-0 flex-1" disabled={busy} onClick={onNo} data-testid="check-no">
            {no}
          </Button>
        </div>
      </Card>
    </div>
  )
}

function useRowUpdate() {
  const client = useQueryClient()
  return (next: MineRow) => {
    client.setQueryData<Pages>(LIST_KEY, (data) => (data ? { ...data, pages: data.pages.map((page) => ({ ...page, results: page.results.map((row) => (row.id === next.id ? next : row)) })) } : data))
  }
}

// F20: the list. Every state keeps the tab, as the flow draws it.
export default function MineScreen() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const online = useOnline()
  const update = useRowUpdate()
  const [editing, setEditing] = useState<MineRow | null>(null)
  const [deleting, setDeleting] = useState<MineRow | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const query = useInfiniteQuery({
    queryKey: LIST_KEY,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const result = await meFetch<MineList>(`/api/v1/me/listings/${pageParam ? `?cursor=${pageParam}` : ''}`)
      if (!result.ok && result.code === 'feature_off') closePosting('posting')
      return unwrap(Promise.resolve(result))
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 0,
  })
  const rows = query.data?.pages.flatMap((page) => page.results) ?? []
  const failed = !query.data && query.isError
  const empty = Boolean(query.data) && rows.length === 0 && !query.hasNextPage
  const slow = useAfter(3000, query.isPending)
  const ask = rows.find((row) => row.owner_check) ?? null

  useEffect(() => {
    if (query.data) track(empty ? 'mine_empty' : 'mine_view', { total: query.data.pages[0]?.total })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(query.data), empty])
  useEffect(() => {
    if (ask) track('mine_owner_check', { kind: ask.kind })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask?.id])

  useEffect(() => {
    const element = sentinel.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && query.hasNextPage && !query.isFetchingNextPage && !query.isFetchNextPageError) void query.fetchNextPage()
      },
      { root: scroller.current, rootMargin: '400px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.hasNextPage, query.isFetchingNextPage, query.isFetchNextPageError, rows.length])

  const status = async (row: MineRow, action: Action): Promise<boolean> => {
    setBusy(row.id)
    const result = await meFetch<MineRow>(`/api/v1/me/listings/${row.id}/status/`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ action }) })
    setBusy(null)
    if (result.ok) {
      track('mine_status', { action })
      update(result.data)
      return true
    }
    failure(result, () => void status(row, action), client)
    return false
  }

  // F20#3: closing also changes the channel post, and «برگردان» brings it back.
  const closeRow = async (row: MineRow) => {
    if (await status(row, 'close')) showToast({ message: 'آگهی بسته شد', icon: 'check', action: { label: 'برگردان', onClick: () => void status(row, 'restore') } })
  }

  const remove = async (row: MineRow) => {
    setBusy(row.id)
    const result = await meFetch(`/api/v1/me/listings/${row.id}/`, { method: 'DELETE' })
    setBusy(null)
    setDeleting(null)
    if (result.ok) {
      track('mine_deleted')
      client.setQueryData<Pages>(LIST_KEY, (data) => (data ? { ...data, pages: data.pages.map((page) => ({ ...page, results: page.results.filter((item) => item.id !== row.id), total: page.total - 1 })) } : data))
    } else {
      failure(result, () => void remove(row), client)
    }
  }

  const answer = async (row: MineRow, value: 'available' | 'taken') => {
    setBusy(row.id)
    const result = await meFetch<Availability>(`/api/v1/me/listings/${row.id}/availability/`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ answer: value }) })
    setBusy(null)
    if (result.ok) {
      track('mine_owner_answer', { value })
      void client.invalidateQueries({ queryKey: MINE_KEY })
    } else {
      failure(result, () => void answer(row, value), client)
    }
  }

  const actionsFor = (row: MineRow): ReactNode => {
    const working = busy === row.id
    if (row.can_renew) {
      return (
        <div className="flex flex-col gap-2" data-testid="row-expired">
          <p className="m-0 text-[13px] text-muted-foreground">آگهی شما بسته شد، چون تأیید نشد.</p>
          <Button className="w-full" disabled={working} onClick={() => void status(row, 'renew')} data-testid="row-renew">
            {working ? <Spinner /> : <RefreshCw />}
            تمدید
          </Button>
        </div>
      )
    }
    if (row.can_restore) {
      return (
        <Button variant="outline" className="w-full" disabled={working} onClick={() => void status(row, 'restore')} data-testid="row-restore">
          {working ? <Spinner /> : <Undo2 />}
          برگردان
        </Button>
      )
    }
    if (row.owner_state === 'rejected') {
      if (!FIXABLE.has(row.reject_code ?? '')) return null
      return <GoButton to={`/mine/${row.id}`}>اصلاح و ارسال دوباره</GoButton>
    }
    if (row.owner_state === 'live') {
      return (
        <div className="flex items-center gap-2">
          <Button className="min-w-0 flex-1" disabled={working} onClick={() => void closeRow(row)} data-testid="row-close">
            {working ? <Spinner /> : null}
            بستن آگهی
          </Button>
          <Button variant="outline" className="min-w-0 flex-1" disabled={working || !row.can_edit} onClick={() => setEditing(row)} data-testid="row-edit">
            ویرایش
          </Button>
        </div>
      )
    }
    return null
  }

  const grid = 'grid auto-rows-max grid-cols-1 gap-4 pt-1 md:grid-cols-4'
  let body: ReactNode
  if (query.isPending) {
    body = (
      <>
        {slow ? <SlowNote /> : null}
        <div className={`${grid} min-h-0 flex-1 overflow-hidden`} data-testid="mine-loading">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      </>
    )
  } else if (failed) {
    const code = query.error instanceof ApiFailureError ? query.error.failure.code : null
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title="بارگذاری نشد" testId={code === 'offline' ? 'mine-offline' : 'mine-failed'}>
          <Button className="w-full" onClick={() => void query.refetch()}>
            <RefreshCw />
            تلاش مجدد
          </Button>
        </StateBox>
      </div>
    )
  } else if (empty) {
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={FileText} title="هنوز آگهی‌ای ندارید" testId="mine-empty">
          <Button onClick={() => navigate('/post')} data-testid="mine-post">
            <Plus />
            ثبت آگهی
          </Button>
        </StateBox>
      </div>
    )
  } else {
    body = (
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto pb-4" data-testid="mine-scroll">
        <div className={grid}>
          {ask ? (
            <Ask
              question={ask.kind === 'wanted' ? `«${rowTitle(ask)}» هنوز دنبال خانه هستید؟` : `«${rowTitle(ask)}» این هفته هم موجود است؟`}
              yes={ask.kind === 'wanted' ? 'بله' : 'بله، موجود است'}
              no={ask.kind === 'wanted' ? 'خانه پیدا کردم' : 'بسته شد'}
              busy={busy === ask.id}
              onYes={() => void answer(ask, 'available')}
              onNo={() => void answer(ask, 'taken')}
            />
          ) : null}
          {rows.map((row) => (
            <OwnerCard key={row.id} row={row} busy={busy === row.id} hideActions={ask?.id === row.id} actions={actionsFor(row)} onEdit={() => setEditing(row)} onClose={() => void closeRow(row)} onDelete={() => setDeleting(row)} />
          ))}
          {query.isFetchingNextPage ? <CardSkeleton /> : null}
          {query.isFetchNextPageError ? (
            <Alert className="col-span-full" data-testid="more-failed">
              <CircleAlert />
              <AlertDescription className="flex w-full items-center justify-between gap-2">
                <span className="text-foreground">ادامه‌ی آگهی‌ها بارگذاری نشد</span>
                <Button variant="outline" size="sm" onClick={() => void query.fetchNextPage()}>
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          <div className="col-span-full">
            <Button variant="outline" className="w-full" onClick={() => navigate('/post')} data-testid="mine-new">
              <Plus />
              ثبت آگهی تازه
            </Button>
          </div>
        </div>
        <div ref={sentinel} className="h-px" />
      </div>
    )
  }

  return (
    <PostPage nav="me" back={() => navigate('/post')} testId="mine-screen">
      <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-3 md:px-6">
        <h1 className="sr-only">آگهی‌های من</h1>
        {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
        {body}
      </div>
      {/* F20#2 */}
      <AlertDialog open={editing !== null} onOpenChange={(open) => (open ? null : setEditing(null))}>
        <AlertDialogContent dir="rtl" data-testid="edit-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>ویرایش آگهی؟</AlertDialogTitle>
            <AlertDialogDescription>آگهی شما تا پایان ویرایش همین‌طور می‌ماند.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction data-testid="edit-go" onClick={() => editing && navigate(`/mine/${editing.id}/edit`)}>
              ویرایش آگهی
            </AlertDialogAction>
            <AlertDialogCancel>انصراف</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* F20#6 */}
      <AlertDialog open={deleting !== null} onOpenChange={(open) => (open ? null : setDeleting(null))}>
        <AlertDialogContent dir="rtl" data-testid="delete-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>آگهی حذف شود؟</AlertDialogTitle>
            {deleting ? (
              <AlertDialogDescription>
                {deleting.card.price_amount != null ? (
                  <>
                    {fa(deleting.card.price_amount)} <bdi dir="ltr">{currency(deleting.card.price_currency)}</bdi> ·{' '}
                  </>
                ) : null}
                <PlaceText text={rowTitle(deleting)} />
              </AlertDialogDescription>
            ) : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction variant="destructive" data-testid="delete-go" onClick={() => deleting && void remove(deleting)}>
              <Trash />
              حذف آگهی
            </AlertDialogAction>
            <AlertDialogCancel>انصراف</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PostPage>
  )
}

function useRow(id: number) {
  const client = useQueryClient()
  return useQuery({
    queryKey: [...MINE_KEY, 'row', id],
    // The row is already here when it came from the list, on any page of it.
    initialData: () => client.getQueryData<Pages>(LIST_KEY)?.pages.flatMap((page) => page.results).find((row) => row.id === id),
    queryFn: async () => {
      const list = await unwrap(meFetch<MineList>('/api/v1/me/listings/'))
      return list.results.find((row) => row.id === id) ?? null
    },
  })
}

function useForm(id: number) {
  // Once a form is open, `updated_at` is what the edit is based on. Only the person's own choice moves it, never a focus.
  return useQuery({ queryKey: [...MINE_KEY, 'form', id], queryFn: () => unwrap(meFetch<MineForm>(`/api/v1/me/listings/${id}/form/`)), staleTime: 0, gcTime: 0, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false })
}

function Loading({ testId }: { testId: string }) {
  return (
    <PostPage testId={testId}>
      <Stack>
        <CardSkeleton />
      </Stack>
    </PostPage>
  )
}

function GoneState({ onRetry, gone }: { onRetry: () => void; gone: boolean }) {
  const navigate = useNavigate()
  return (
    <PostPage back={() => navigate('/mine')} testId={gone ? 'edit-gone' : 'edit-failed'}>
      <div className="flex min-h-0 flex-1 items-center justify-center p-4">
        <StateBox icon={CircleAlert} title={gone ? 'این آگهی دیگر نیست' : 'بارگذاری نشد'}>
          <Button className="w-full" onClick={gone ? () => navigate('/mine') : onRetry}>
            {gone ? null : <RefreshCw />}
            {gone ? 'آگهی‌های من' : 'تلاش مجدد'}
          </Button>
        </StateBox>
      </div>
    </PostPage>
  )
}

function draftOf(form: MineForm, saved: ReturnType<typeof useSavedCity>): Draft {
  const known = saved && saved.city === form.city
  return fromForm(form, { country: form.country ?? saved?.country ?? 'CA', city: form.city ?? '', slug: known ? saved.slug : '', name_fa: known ? saved.name_fa : (form.city ?? '') })
}

// F20#2: the same form as a post, starting from what is up now.
export function MineEditScreen() {
  const { id } = useParams()
  const listingId = Number(id)
  const form = useForm(listingId)
  if (form.isPending) return <Loading testId="edit-loading" />
  if (!form.data) {
    const code = form.error instanceof ApiFailureError ? form.error.failure.code : null
    return <GoneState gone={code === 'listing_gone'} onRetry={() => void form.refetch()} />
  }
  return <EditSession key={listingId} id={listingId} opened={form.data} />
}

// Holds what the form opened with, so nothing but the person's choice replaces what they typed. `edit_conflict` asks:
// keep the text and send it over the newer version, or take the server's copy.
function EditSession({ id, opened }: { id: number; opened: MineForm }) {
  const navigate = useNavigate()
  const saved = useSavedCity()
  const [version, setVersion] = useState(0)
  const [base, setBase] = useState(opened.updated_at)
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [initial, setInitial] = useState(() => draftOf(opened, saved))

  const latest = async () => {
    setBusy(true)
    const result = await meFetch<MineForm>(`/api/v1/me/listings/${id}/form/`)
    setBusy(false)
    if (result.ok) return result.data
    if (result.code === 'listing_gone') {
      showToast({ message: 'این آگهی دیگر نیست', icon: 'alert' })
      navigate('/mine', { replace: true })
    } else {
      showToast({ message: 'بارگذاری نشد', icon: 'alert', overSheet: true })
    }
    return null
  }
  const keepMine = async () => {
    const form = await latest()
    if (!form) return
    track('edit_conflict_answer', { choice: 'mine' })
    setBase(form.updated_at)
    setConflict(false)
  }
  const useServer = async () => {
    const form = await latest()
    if (!form) return
    track('edit_conflict_answer', { choice: 'server' })
    setInitial(draftOf(form, saved))
    setVersion((row) => row + 1)
    setBase(form.updated_at)
    setConflict(false)
  }

  const editing = { id, updatedAt: base }
  const onConflict = () => setConflict(true)
  return (
    <>
      {initial.kind === 'wanted' ? <WantedForm key={version} initial={initial} editing={editing} onConflict={onConflict} /> : <OfferWizard key={version} initial={initial} editing={editing} onConflict={onConflict} />}
      {conflict ? (
        <ConfirmSheet
          title="این آگهی تغییر کرده است"
          testId="edit-conflict"
          onClose={() => setConflict(false)}
          foot={
            <div className="flex flex-col gap-2">
              <Button className="w-full" disabled={busy} onClick={() => void keepMine()} data-testid="conflict-keep">
                {busy ? <Spinner /> : null}
                نگه داشتن متن من
              </Button>
              <Button variant="outline" className="w-full" disabled={busy} onClick={() => void useServer()} data-testid="conflict-server">
                استفاده از نسخه‌ی تازه
              </Button>
            </div>
          }
        />
      ) : null}
    </>
  )
}

// F20#4, #12: why it was refused, and the way out when there is one. Price is the one problem fixed right here.
export function MineRejectedScreen() {
  const { id } = useParams()
  const listingId = Number(id)
  const navigate = useNavigate()
  const client = useQueryClient()
  const row = useRow(listingId)
  const form = useForm(listingId)
  const [price, setPrice] = useState('')
  const [blurred, setBlurred] = useState(false)
  const remove = useMutation({
    mutationFn: async () => {
      const result = await meFetch(`/api/v1/me/listings/${listingId}/`, { method: 'DELETE' })
      if (!result.ok) throw new ApiFailureError(result)
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: MINE_KEY })
      navigate('/mine', { replace: true })
    },
    onError: () => showToast({ message: 'ذخیره نشد', icon: 'alert' }),
  })
  const submit = useMutation({
    mutationFn: async () => {
      if (!form.data) throw new Error('no form')
      const result = await meFetch<PostResult>(`/api/v1/me/listings/${listingId}/`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ ...stripForm(form.data), price: amount(price), base_updated_at: form.data.updated_at }) })
      if (!result.ok) throw new ApiFailureError(result)
      return result.data
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: MINE_KEY })
      navigate('/mine', { replace: true })
    },
    onError: () => showToast({ message: 'ذخیره نشد', icon: 'alert' }),
  })

  if (row.isPending) return <Loading testId="rejected-loading" />
  const data = row.data
  if (!data) return <GoneState gone={!row.isError} onRetry={() => void row.refetch()} />
  const code = data.reject_code
  const fixable = FIXABLE.has(code ?? '')
  const needsPrice = code === 'required'
  const card = data.card
  const trash = (
    <Button variant="outline" size="icon" aria-label="حذف آگهی" disabled={remove.isPending} onClick={() => remove.mutate()} data-testid="rejected-delete">
      <Trash />
    </Button>
  )
  return (
    <PostPage
      back={() => navigate('/mine')}
      testId={fixable ? 'rejected-fix' : 'rejected-other'}
      foot={
        <FootColumn>
          {fixable ? (
            needsPrice ? (
              <Button className="min-w-0 flex-1" disabled={!priceOk(price) || submit.isPending || !form.data} onClick={() => submit.mutate()} data-testid="rejected-send">
                {submit.isPending ? <Spinner /> : null}
                اصلاح و ارسال دوباره
              </Button>
            ) : (
              <Button className="min-w-0 flex-1" onClick={() => navigate(`/mine/${listingId}/edit`)} data-testid="rejected-send">
                اصلاح و ارسال دوباره
              </Button>
            )
          ) : (
            <Button className="min-w-0 flex-1" onClick={() => navigate('/post')} data-testid="rejected-new">
              ثبت آگهی تازه
            </Button>
          )}
          {trash}
        </FootColumn>
      }
    >
      <div className="grid gap-3 md:grid-cols-2 md:gap-6 md:p-6">
        <Photo src={card.thumb} count={card.photo_count > 0 ? fa(card.photo_count) : undefined} className="md:rounded-xl">
          {STATE_BADGE.rejected}
        </Photo>
        <div className="flex min-w-0 flex-col gap-3 p-4 md:p-0">
          <Bad testId="rejected-reason">آگهی شما منتشر نشد، چون {reasonText(code)}.</Bad>
          {needsPrice ? <div className="text-lg font-bold text-muted-foreground">بدون قیمت</div> : null}
          <div className="text-[15px] font-bold">{cardTitle(card)}</div>
          <div className="flex items-center gap-1 text-sm text-muted-foreground">
            <MapPin className="size-3.5 flex-none" aria-hidden />
            <span className="min-w-0">
              <PlaceText text={cardPlace(card)} />
            </span>
          </div>
          {needsPrice ? (
            <div onBlur={() => setBlurred(true)}>
              <Labeled label="قیمت" htmlFor="rejected-price" error={!priceOk(price) && blurred ? 'قیمت را بنویسید.' : undefined}>
                <MoneyInput id="rejected-price" code={card.price_currency} placeholder="۲٬۲۰۰" value={price} invalid={!priceOk(price) && blurred} onChange={setPrice} />
              </Labeled>
            </div>
          ) : null}
        </div>
      </div>
    </PostPage>
  )
}

// The form as a body for PATCH: what MineForm adds is not part of a request.
function stripForm(form: MineForm) {
  const { photos, updated_at: _updated, ...rest } = form
  return { ...rest, photo_ids: photos.map((photo) => photo.photo_id) }
}
