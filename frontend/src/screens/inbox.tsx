import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query'
import { BellOff, BellRing, BookmarkX, CheckCheck, CircleAlert, CircleCheck, CircleOff, CircleX, Clock, Plus, RefreshCw, type LucideIcon } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { NotificationList, NotificationRead, NotificationRow } from '@/api/types'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ACCOUNT_KEY, accountFetch } from '@/lib/account'
import { unwrap, type ApiFailure } from '@/lib/api'
import { useBack } from '@/lib/back'
import { useAlertEntry } from '@/lib/features'
import { fa, relativeTime } from '@/lib/format'
import { legacyHomeUrl } from '@/lib/links'
import { t } from '@/lib/locale'
import { queryClient } from '@/lib/query'
import { track } from '@/lib/report'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

const KEY = ['notifications']
type Pages = InfiniteData<NotificationList, string | null>

const ICONS: Record<NotificationRow['notification_kind'], LucideIcon> = {
  alert_match: BellRing,
  listing_live: CircleCheck,
  listing_held: Clock,
  listing_rejected: CircleX,
  listing_expired: CircleOff,
  saved_closed: BookmarkX,
}

// The code names of a post's fields (F18). Any other code: the line stays, without a reason.
const REJECT_REASONS: Record<string, string> = {
  price: 'قیمت نوشته نشده است',
  not_housing: 'آگهی خانه نیست',
  incomplete: 'اطلاعات آگهی کامل نیست',
}

// The client writes the text, from the kind and the fields of the row.
export function lineOf(row: NotificationRow) {
  const when = relativeTime(row.created_at)
  switch (row.notification_kind) {
    case 'alert_match':
      return { title: t('{n} آگهی تازه برای «{title}»', { n: fa(row.match_count ?? 0), title: row.title }), detail: when }
    case 'listing_live':
      return { title: t('آگهی شما منتشر شد: {title}', { title: row.title }), detail: when }
    case 'listing_held':
      return { title: t('آگهی شما در انتظار بررسی است: {title}', { title: row.title }), detail: when }
    case 'listing_rejected': {
      const reason = row.reject_code ? REJECT_REASONS[row.reject_code] : undefined
      return { title: t('آگهی شما رد شد: {title}', { title: row.title }), detail: reason ? `${t(reason)} · ${when}` : when }
    }
    case 'listing_expired':
      return { title: t('آگهی شما بسته شد، چون تأیید نشد.'), detail: `${row.title} · ${when}` }
    case 'saved_closed':
      return { title: t('آگهی نشان‌شده بسته شد: {title}', { title: row.title }), detail: when }
  }
}

// F17 rules: each line goes to its own place, and a place that is gone answers with F02#4 or F16#12.
export function targetOf(row: NotificationRow): { path: string } | { legacy: string } {
  if (row.notification_kind === 'alert_match') return { path: `/alerts/${row.alert_id ?? 'gone'}` }
  if (row.target_state === 'gone' || !row.listing_id) return { path: '/listing/gone' }
  if (row.notification_kind === 'listing_live' || row.notification_kind === 'saved_closed') return { path: `/listing/${row.listing_id}` }
  return { legacy: 'mine/' } // F18 and F20 stay on the old pages until P4.
}

const markRead = (body: { ids?: number[] }) => accountFetch<NotificationRead>('/api/v1/me/notifications/read/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

function writeRead(ids: number[] | null, unread: (before: number) => number) {
  const stamp = new Date().toISOString()
  queryClient.setQueryData<Pages>(KEY, (data) =>
    data && {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        unread: unread(page.unread),
        results: page.results.map((row) => (!row.read_at && (ids === null || ids.includes(row.id)) ? { ...row, read_at: stamp } : row)),
      })),
    },
  )
}

const APP_WIDE = new Set(['maintenance', 'user_blocked', 'session_expired', 'auth_invalid', 'auth_outside', 'miniapp_off', 'feature_off'])

function readFailed(failure: ApiFailure, retry: () => void) {
  track('inbox_read_failed', { code: failure.code })
  if (APP_WIDE.has(failure.code)) return
  if (failure.code === 'rate_limited') return rateLimitedToast(retry, 'inbox')
  saveFailedToast(retry, 'inbox')
}

// F17#4: optimistic, back to how it was on a failure.
function readAll() {
  const before = queryClient.getQueryData<Pages>(KEY)
  writeRead(null, () => 0)
  track('inbox_read_all')
  showToast({ id: 'inbox', message: t('همه‌ی اعلان‌ها خوانده شد'), icon: 'check' })
  void markRead({}).then((result) => {
    if (result.ok) return void queryClient.invalidateQueries({ queryKey: ACCOUNT_KEY })
    queryClient.setQueryData(KEY, before)
    readFailed(result, readAll)
  })
}

function Line({ row, onOpen }: { row: NotificationRow; onOpen: () => void }) {
  const Icon = ICONS[row.notification_kind] ?? BellRing
  const line = lineOf(row)
  return (
    <button type="button" onClick={onOpen} data-testid="inbox-row" data-kind={row.notification_kind} className="flex min-h-[56px] w-full items-center gap-3 py-3 text-start outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
      <Icon className="size-[18px] flex-none" aria-hidden />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="text-sm font-medium break-words">{line.title}</span>
        <span className="text-[13px] text-muted-foreground">{line.detail}</span>
      </span>
      {row.read_at ? null : <i role="img" aria-label={t('تازه')} data-testid="unread-dot" className="size-2 flex-none rounded-full bg-primary" />}
    </button>
  )
}

// F17.
export default function InboxScreen() {
  const navigate = useNavigate()
  const here = useLocation()
  const online = useOnline()
  const canCreate = useAlertEntry()
  useBack(() => (here.key !== 'default' ? navigate(-1) : navigate('/account', { replace: true })))
  const query = useInfiniteQuery({
    queryKey: KEY,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => unwrap(accountFetch<NotificationList>(`/api/v1/me/notifications/${pageParam ? `?cursor=${pageParam}` : ''}`)),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 0,
  })
  const rows = query.data?.pages.flatMap((page) => page.results) ?? []
  const unread = query.data?.pages[0]?.unread ?? 0
  const slow = useAfter(3000, query.isPending)
  const sentinel = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (query.data) track('inbox_view', { unread })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(query.data)])
  useEffect(() => {
    if (query.isError && !query.data) track('inbox_failed')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.isError])
  useEffect(() => {
    const element = sentinel.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && query.hasNextPage && !query.isFetchingNextPage && !query.isFetchNextPageError) {
          track('inbox_more')
          void query.fetchNextPage()
        }
      },
      { root: scroller.current, rootMargin: '400px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.hasNextPage, query.isFetchingNextPage, query.isFetchNextPageError, rows.length])

  const open = (row: NotificationRow) => {
    track(row.target_state === 'gone' ? 'inbox_target_gone' : row.notification_kind === 'listing_rejected' ? 'inbox_reject_open' : 'inbox_open', { kind: row.notification_kind })
    // Reading is on the way out, and a lost read only leaves the dot for next time.
    if (!row.read_at) {
      writeRead([row.id], (before) => Math.max(0, before - 1))
      void markRead({ ids: [row.id] }).then((result) => {
        if (result.ok) void queryClient.invalidateQueries({ queryKey: ACCOUNT_KEY })
      })
    }
    const target = targetOf(row)
    if ('legacy' in target) window.location.assign(legacyHomeUrl(target.legacy))
    else navigate(target.path)
  }

  let body: React.ReactNode
  if (query.isPending) {
    body = (
      <div className="flex flex-col gap-3" data-testid="inbox-loading">
        {slow ? <SlowNote /> : null}
        <Skeleton className="h-64 w-full" />
      </div>
    )
  } else if (!query.data) {
    body = (
      <div className="flex min-h-[420px] flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title={t('بارگذاری نشد')} testId="inbox-failed">
          <Button className="w-full" onClick={() => void query.refetch()}>
            <RefreshCw />
            {t('تلاش مجدد')}
          </Button>
        </StateBox>
      </div>
    )
  } else if (rows.length === 0) {
    // F17#5
    body = (
      <div className="flex min-h-[520px] flex-1 items-center justify-center">
        <StateBox icon={BellOff} title={t('اعلانی ندارید')} testId="inbox-empty">
          {canCreate ? (
            <Button variant="outline" onClick={() => navigate('/')}>
              <Plus />
              {t('گوش به زنگ جدید')}
            </Button>
          ) : null}
        </StateBox>
      </div>
    )
  } else {
    body = (
      <Card className="gap-0 divide-y divide-border px-4 py-1" data-testid="inbox-list">
        {rows.map((row) => (
          <Line key={row.id} row={row} onOpen={() => open(row)} />
        ))}
        {query.isFetchingNextPage ? <Skeleton className="my-3 h-12 w-full" /> : null}
        {query.isFetchNextPageError ? (
          <Alert className="my-3" data-testid="more-failed">
            <CircleAlert />
            <AlertDescription className="flex w-full items-center justify-between gap-2">
              <span className="text-foreground">{t('ادامه‌ی اعلان‌ها بارگذاری نشد')}</span>
              <Button variant="outline" size="sm" onClick={() => void query.fetchNextPage()}>
                <RefreshCw />
                {t('تلاش مجدد')}
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
      </Card>
    )
  }

  return (
    <div ref={scroller} className="h-full overflow-y-auto" data-testid="inbox-screen">
      <div className="mx-auto flex min-h-full w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
        {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
        <div className="flex items-center justify-between gap-2">
          <h1 className="m-0 text-2xl leading-8 font-extrabold">{t('اعلان‌ها')}</h1>
          {unread > 0 ? (
            <Button variant="ghost" size="sm" data-testid="read-all" onClick={readAll}>
              <CheckCheck />
              {t('خواندن همه')}
            </Button>
          ) : null}
        </div>
        {body}
        <div ref={sentinel} className="h-px" />
      </div>
    </div>
  )
}
