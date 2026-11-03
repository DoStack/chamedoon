import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { BellOff, BellRing, CircleAlert, RefreshCw, SearchX } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import type { AlertListings, AlertRow } from '@/api/types'
import { CardSkeleton, ListingCard } from '@/components/listing-card'
import { MainNav } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ALERTS_KEY, alertsFetch } from '@/lib/alerts'
import { filterChips, viewOfFilters } from '@/lib/alert-view'
import { ApiFailureError, unwrap } from '@/lib/api'
import { useBack } from '@/lib/back'
import { fa } from '@/lib/format'
import { t } from '@/lib/locale'
import { queryClient } from '@/lib/query'
import { track } from '@/lib/report'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

// F16#6 and F16#12. Reading changes nothing on the server; the count clears once the list has really been shown.
export default function AlertListingsScreen() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const online = useOnline()
  const alertId = Number(id)
  const back = () => (location.key !== 'default' ? navigate(-1) : navigate('/saved', { replace: true }))
  useBack(back)
  const alert = useQuery({ queryKey: ['alert', alertId], queryFn: () => unwrap(alertsFetch<AlertRow>(`/api/v1/me/alerts/${alertId}/`)), enabled: Number.isInteger(alertId), staleTime: 0 })
  const listings = useInfiniteQuery({
    queryKey: ['alert-listings', alertId],
    enabled: Number.isInteger(alertId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => unwrap(alertsFetch<AlertListings>(`/api/v1/me/alerts/${alertId}/listings/${pageParam ? `?cursor=${pageParam}` : ''}`)),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 0,
  })
  const failure = alert.error ?? listings.error
  const code = failure instanceof ApiFailureError ? failure.failure.code : null
  const gone = !Number.isInteger(alertId) || code === 'alert_gone'
  const pending = alert.isPending || listings.isPending
  const slow = useAfter(3000, pending && !gone)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const rows = listings.data?.pages.flatMap((page) => page.results) ?? []
  const total = listings.data?.pages[0]?.total ?? 0

  const viewed = useRef(false)
  useEffect(() => {
    if (!listings.data || viewed.current) return
    viewed.current = true
    track('alert_open', { total })
    void alertsFetch<null>(`/api/v1/me/alerts/${alertId}/viewed/`, { method: 'POST' }).then((result) => {
      if (result.ok) void queryClient.invalidateQueries({ queryKey: ALERTS_KEY })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(listings.data)])

  useEffect(() => {
    if (gone) track('alert_not_found')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gone])

  useEffect(() => {
    const element = sentinel.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && listings.hasNextPage && !listings.isFetchingNextPage && !listings.isFetchNextPageError) void listings.fetchNextPage()
      },
      { root: scroller.current, rootMargin: '400px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listings.hasNextPage, listings.isFetchingNextPage, listings.isFetchNextPageError, rows.length])

  const grid = 'grid auto-rows-max grid-cols-1 gap-4 pt-1 md:grid-cols-3'
  let body: React.ReactNode
  if (gone) {
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={BellOff} title={t('این گوش به زنگ دیگر نیست')} testId="alert-gone">
          <Button onClick={back}>{t('برگشت')}</Button>
        </StateBox>
      </div>
    )
  } else if (pending) {
    body = (
      <>
        {slow ? <SlowNote /> : null}
        <div className={`${grid} min-h-0 flex-1 overflow-hidden`} data-testid="alert-listings-loading">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      </>
    )
  } else if (!alert.data || !listings.data) {
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title={t('بارگذاری نشد')} testId="alert-listings-failed">
          <Button
            className="w-full"
            onClick={() => {
              void alert.refetch()
              void listings.refetch()
            }}
          >
            <RefreshCw />
            {t('تلاش مجدد')}
          </Button>
        </StateBox>
      </div>
    )
  } else {
    const chips = filterChips(viewOfFilters(alert.data.filters), alert.data.country)
    body = (
      <>
        {chips.length ? (
          <div className="flex flex-none flex-wrap gap-2" data-testid="alert-filters">
            {chips.map((chip) => (
              <Badge key={chip.id} variant="secondary">
                {chip.label}
              </Badge>
            ))}
          </div>
        ) : null}
        <div className="flex flex-none items-center justify-between gap-2 text-sm" data-testid="alert-count">
          <b className="font-extrabold whitespace-nowrap">{t('{n} آگهی تازه', { n: fa(total) })}</b>
          <Badge variant="secondary">
            <BellRing />
            {t('گوش به زنگ روشن')}
          </Badge>
        </div>
        {rows.length === 0 ? (
          <div className="flex min-h-0 flex-1 items-center justify-center">
            <StateBox icon={SearchX} title={t('آگهی تازه‌ای نیست')} testId="alert-listings-empty">
              <Button onClick={back}>{t('برگشت')}</Button>
            </StateBox>
          </div>
        ) : (
          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto pb-4" data-testid="alert-listings">
            <div className={grid}>
              {rows.map((card) => (
                <ListingCard key={card.id} card={card} />
              ))}
              {listings.isFetchingNextPage ? <CardSkeleton /> : null}
              {listings.isFetchNextPageError ? (
                <Alert className="col-span-full" data-testid="more-failed">
                  <CircleAlert />
                  <AlertDescription className="flex w-full items-center justify-between gap-2">
                    <span className="text-foreground">{t('ادامه‌ی آگهی‌ها بارگذاری نشد')}</span>
                    <Button variant="outline" size="sm" onClick={() => void listings.fetchNextPage()}>
                      <RefreshCw />
                      {t('تلاش مجدد')}
                    </Button>
                  </AlertDescription>
                </Alert>
              ) : null}
            </div>
            <div ref={sentinel} className="h-px" />
          </div>
        )}
      </>
    )
  }

  return (
    <div className="flex h-full flex-col" data-testid="alert-listings-screen">
      <MainNav place="top" active="saved" />
      <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-3 md:px-6">
        {online ? null : (
          <OfflineBar
            onRetry={() => {
              void alert.refetch()
              void listings.refetch()
            }}
          />
        )}
        {body}
      </div>
      <MainNav place="bottom" active="saved" />
    </div>
  )
}
