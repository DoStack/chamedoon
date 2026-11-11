import { useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { Bell, CheckCheck, CircleAlert, Luggage, Package, Plus, RefreshCw, Route, SearchX } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { isVersionAtLeast } from '@/lib/telegram'
import { useFeature } from './access'
import { AlertButton, AlertSheet } from './alert-sheet'
import { courierFetch, errorCode, patchJson, unwrap } from './api'
import { FilterRow, FilterSheet, query, type Filters, type SheetKind } from './filters'
import { nounFor, placeKey, routeProps, SIZES, upcomingMonths, type Side } from './format'
import { H1, Page, Stack, useMe } from './page'
import { CardGrid, RequestCardView, RequestSkeleton, Segmented } from './parts'
import { RouteBar, RouteSheet, useLocations } from './route-sheet'
import { ActiveChips, FilterSheetV4, MetaRow, RouteBarV4 } from './list-v4'
import { Checking, FailedScreen, OfflineBar, SlowNote, useAfter, useOnline } from './states'
import type { components } from '@/api/courier-schema.gen'

type RequestList = components['schemas']['RequestList']

const PAGE = 20
const PLACE = /^[A-Za-z]{2}:[\w-]+$/
const PULL_PX = 70

export const listUrl = (params: { side?: string; from?: string; to?: string }) => `/${query(params)}`

// Bad values in the URL drop out, as `parse_filters` drops them.
function readParams(params: URLSearchParams) {
  const side = params.get('side')
  const from = params.get('from') ?? ''
  const to = params.get('to') ?? ''
  const month = params.get('month') ?? ''
  const size = params.get('size') ?? ''
  return {
    side: side === 'DEMAND' || side === 'SUPPLY' ? (side as Side) : null,
    from: PLACE.test(from) ? from : '',
    to: PLACE.test(to) ? to : '',
    month: upcomingMonths().includes(month) ? month : '',
    size: SIZES.DEMAND.some((row) => row.code === size) ? size : '',
    cat: /^[A-Z_]{2,32}$/.test(params.get('cat') ?? '') ? (params.get('cat') as string) : '',
    ids: /^[\d,]+$/.test(params.get('ids') ?? '') ? (params.get('ids') as string) : '',
  }
}

// C01.1: one question, first direct open only. A link never asks.
function RoleChoice({ onPick }: { onPick: (side: Side) => void }) {
  useEffect(() => track('courier_role_view'), [])
  const row = (side: Side, Icon: typeof Package, title: string, text: string) => (
    <Card className="!gap-0 !px-4 !py-1">
      <ItemGroup className="!gap-0">
        <Item asChild>
          <button type="button" className="w-full text-start" onClick={() => onPick(side)} data-testid={`role-${side}`}>
            <ItemMedia variant="icon">
              <Icon />
            </ItemMedia>
            <ItemContent>
              <ItemTitle>{title}</ItemTitle>
              <ItemDescription>{text}</ItemDescription>
            </ItemContent>
          </button>
        </Item>
      </ItemGroup>
    </Card>
  )
  return (
    <Page tab={null} testId="role-choice">
      <Stack center>
        <H1>بسته دارید یا مسافرید؟</H1>
        {row('DEMAND', Package, 'بسته دارم', 'دنبال مسافرم')}
        {row('SUPPLY', Luggage, 'مسافرم', 'جا دارم')}
      </Stack>
    </Page>
  )
}

function SideTabs({ value, onChange }: { value: Side; onChange: (side: Side) => void }) {
  return (
    <Segmented
      label="طرف بازار"
      value={value}
      onChange={onChange}
      options={[
        {
          value: 'DEMAND',
          label: (
            <>
              <Package />
              بسته دارم
            </>
          ),
        },
        {
          value: 'SUPPLY',
          label: (
            <>
              <Luggage />
              مسافرم
            </>
          ),
        },
      ]}
    />
  )
}

// C03.6: pull down at the top, from Bot API 7.7. Below it, vertical swipes close the app, so the list has a button.
function usePull(onPull: () => void) {
  const start = useRef<number | null>(null)
  return {
    onTouchStart: (event: React.TouchEvent<HTMLDivElement>) => {
      start.current = event.currentTarget.parentElement?.scrollTop === 0 ? event.touches[0].clientY : null
    },
    onTouchEnd: (event: React.TouchEvent<HTMLDivElement>) => {
      if (start.current !== null && event.changedTouches[0].clientY - start.current > PULL_PX) onPull()
      start.current = null
    },
  }
}

// C03 and C01: the home list. Side, route and filters live in the URL; the last ones are saved, and never decide a link.
export function RoutesScreen() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const me = useMe()
  const locations = useLocations()
  const url = readParams(params)
  const prefs = me.data?.prefs
  const online = useOnline()
  const [localSide, setLocalSide] = useState<Side | null>(null)
  const direct = !url.side && !url.from && !url.to && !url.ids
  const side: Side = url.side ?? localSide ?? (prefs?.side as Side) ?? 'DEMAND'
  const from = direct ? (prefs?.last_origin ?? '') : url.from
  const to = direct ? (prefs?.last_destination ?? '') : url.to
  const routeBarV4 = useFeature('v4_route_bar')
  const filterV4 = useFeature('v4_filter_sheet')
  const smartOn = useFeature('v4_smart_search')
  // The category filter exists only with the v4 sheet; a link carrying one is ignored without it.
  const cat = filterV4 ? url.cat : ''
  const filters: Filters = { month: url.month, size: url.size }
  const [v4Sheet, setV4Sheet] = useState(false)
  const [route, setRoute] = useState<'from' | 'to' | null>(null)
  const [sheet, setSheet] = useState<SheetKind | null>(null)
  const [alert, setAlert] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)

  const savePrefs = useMutation({
    mutationFn: (body: Record<string, string>) => unwrap(patchJson('me/prefs/', body)),
    // Optional: the list carries on, and the next change saves again.
    onError: () => {
      track('courier.c01.pref_save_failed')
      track('courier_prefs_save_failed', { surface: 'routes' })
    },
  })

  const update = (next: Partial<{ side: Side; from: string; to: string; month: string; size: string; cat: string }>) => {
    const merged = { side, from, to, ...filters, cat, ...next }
    setParams(new URLSearchParams(Object.entries(merged).filter(([, value]) => value) as [string, string][]), { replace: true })
    const body: Record<string, string> = {}
    if (next.side) body.side = next.side
    if ('from' in next) body.last_origin = next.from ?? ''
    if ('to' in next) body.last_destination = next.to ?? ''
    if (Object.keys(body).length) savePrefs.mutate(body)
  }

  const list = useInfiniteQuery({
    queryKey: ['courier', 'requests', side, from, to, url.month, url.size, cat, url.ids],
    enabled: me.isSuccess || me.isError,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => unwrap(courierFetch<RequestList>(`requests/${query({ side, origin: from, destination: to, month: url.month, size: url.size, category: cat, ids: url.ids, offset: pageParam, limit: PAGE })}`, { signal })),
    getNextPageParam: (last) => last.next_offset ?? undefined,
    placeholderData: undefined,
  })
  const slow = useAfter(5000, list.isLoading)
  const rows = list.data?.pages.flatMap((page) => page.items) ?? []
  const total = list.data?.pages[0]?.total ?? 0
  const noun = nounFor(side)
  const hasFilters = Boolean(url.month || url.size || cat)
  const moreFailed = list.isFetchNextPageError
  const refreshing = list.isRefetching && !list.isFetchingNextPage
  const canPull = isVersionAtLeast('7.7')
  const refresh = useCallback(() => {
    track('courier.c03.refresh')
    track('courier_route_refresh')
    void list.refetch()
  }, [list])
  const pull = usePull(refresh)

  // A same-city pair in the URL has no list: the destination drops, and the sheet says why next time.
  const code = list.isError && !list.data ? errorCode(list.error) : null
  useEffect(() => {
    if (code === 'same_city') update({ to: '' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const node = sentinel.current
    if (!node || !list.hasNextPage || moreFailed) return
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !list.isFetchingNextPage) {
        track('courier_route_load_more', { loaded: rows.length, total })
        void list.fetchNextPage()
      }
    })
    seen.observe(node)
    return () => seen.disconnect()
  }, [list, moreFailed])

  useEffect(() => {
    if (list.isSuccess) track(total === 0 ? 'courier.c03.route_empty' : total === 1 ? 'courier.c03.list_one' : 'courier.c03.list_view', { side })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.isSuccess, total, side])

  useEffect(() => {
    if (!list.isSuccess) return
    const props = { side, ...routeProps(from, to), month: url.month || null, size: url.size || null, total, by_ids: Boolean(url.ids) }
    track('courier_route_list_view', props)
    if (total === 0) track('courier_route_no_result', { ...props, has_filters: hasFilters })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.isSuccess, side, from, to, url.month, url.size, url.ids])

  useEffect(() => {
    if (code === 'offline' || code === 'network_error' || code === 'timeout') track('courier_offline_shown', { surface: 'route_list', code })
  }, [code])

  useEffect(() => {
    if (moreFailed) track('courier_route_load_more_failed', { loaded: rows.length })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moreFailed])

  if (me.isLoading) return <Checking />
  if (direct && !localSide && me.isSuccess && !prefs?.side) {
    return (
      <RoleChoice
        onPick={(picked) => {
          track('courier.c01.role', { side: picked })
          track('courier_role_chosen', { side: picked })
          setLocalSide(picked)
          update({ side: picked })
        }}
      />
    )
  }

  const draft = { side, from, to, month: url.month, size: url.size }
  const countBar = routeBarV4 ? null : (
    <div className="flex min-h-10 flex-none items-center justify-between gap-2 text-sm" data-testid="count-bar">
      <b className="font-extrabold whitespace-nowrap" data-testid="count">
        {fa(total)} {noun}
      </b>
      <AlertButton draft={draft} onOpen={() => setAlert(true)} />
    </div>
  )

  const body = () => {
    if (list.isLoading) {
      return (
        <>
          <div className="flex min-h-10 flex-none items-center justify-between">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-5 w-40" />
          </div>
          {slow ? <SlowNote /> : null}
          <CardGrid>
            {Array.from({ length: 6 }, (_, index) => (
              <RequestSkeleton key={index} />
            ))}
          </CardGrid>
        </>
      )
    }
    if (list.isError && !list.data) {
      if (code === 'offline' || code === 'network_error' || code === 'timeout') {
        return (
          <StateBox icon={CircleAlert} title="فهرست بارگذاری نشد" testId="list-failed">
            <Button
              onClick={() => {
                track('courier_retry_tap', { where: 'route_list' })
                refresh()
              }}
            >
              <RefreshCw />
              تلاش مجدد
            </Button>
          </StateBox>
        )
      }
      return <FailedScreen code={code ?? 'server_error'} onRetry={refresh} />
    }
    if (!rows.length) {
      if (hasFilters) {
        return (
          <StateBox icon={SearchX} title={`${noun}ی با این فیلترها نیست`} testId="filter-empty">
            <div className="flex w-full flex-col gap-2">
              <Button
                className="w-full"
                onClick={() => {
                  track('courier_filter_clear', { surface: 'empty', side })
                  update({ month: '', size: '', cat: '' })
                }}
                data-testid="filters-clear"
              >
                پاک کردن فیلترها
              </Button>
              <Button variant="outline" className="w-full" onClick={() => setAlert(true)}>
                <Bell />
                گوش به زنگ
              </Button>
            </div>
          </StateBox>
        )
      }
      return (
        <StateBox icon={Route} title={`هنوز ${noun}ی برای این مسیر نیست`} testId="route-empty">
          <div className="flex w-full flex-col gap-2">
            <Button className="w-full" onClick={() => setAlert(true)} data-testid="empty-alert">
              <Bell />
              گوش به زنگ
            </Button>
            <Button
              variant="outline"
              className="w-full"
              onClick={() => {
                track('courier_route_empty_post_tap', { side })
                navigate(`/post/${side === 'DEMAND' ? 'package' : 'trip'}${query({ from, to })}`)
              }}
              data-testid="empty-post"
            >
              <Plus />
              {side === 'DEMAND' ? 'ثبت بسته' : 'ثبت سفر'}
            </Button>
          </div>
        </StateBox>
      )
    }
    return (
      <>
        {countBar}
        {!canPull ? (
          <Button variant="ghost" size="sm" className="self-start" onClick={refresh} data-testid="list-refresh">
            <RefreshCw />
            به‌روزرسانی
          </Button>
        ) : null}
        {refreshing ? (
          <div className="flex min-h-7 flex-none justify-center" data-testid="list-refreshing">
            <Spinner />
          </div>
        ) : null}
        <CardGrid>
          {rows.map((row) => (
            <RequestCardView key={row.id} card={row} onOpen={() => navigate(`/request/${row.id}`, { state: { list: location.search } })} />
          ))}
          {list.isFetchingNextPage
            ? Array.from({ length: 2 }, (_, index) => <RequestSkeleton key={index} />)
            : null}
          {moreFailed ? (
            <Alert className="col-span-full" data-testid="more-failed">
              <CircleAlert />
              <AlertDescription className="flex w-full items-center justify-between gap-2">
                ادامه‌ی فهرست بارگذاری نشد
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    track('courier_retry_tap', { where: 'route_list_more' })
                    void list.fetchNextPage()
                  }}
                >
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          {!list.hasNextPage && !moreFailed ? (
            <Card className="col-span-full !p-4" data-testid="list-end">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 font-bold">
                  <CheckCheck className="size-5 shrink-0" aria-hidden />
                  {total === 1 ? `همین یک ${noun} است` : `همه‌ی ${fa(total)} ${noun} را دیدید`}
                </span>
                <Button size="sm" onClick={() => setAlert(true)}>
                  <Bell />
                  گوش به زنگ
                </Button>
              </div>
            </Card>
          ) : null}
        </CardGrid>
        <div ref={sentinel} className="h-px flex-none" aria-hidden />
      </>
    )
  }

  const empty = !list.isLoading && (!rows.length || (list.isError && !list.data))
  return (
    <Page tab="routes" testId="routes">
      <div ref={scroller} className={`flex flex-col gap-2 px-4 pt-2 pb-4 md:gap-3 md:px-6 md:pt-3 ${empty ? ' min-h-full' : ''}`} {...(canPull ? pull : {})}>
        {!online && rows.length ? <OfflineBar onRetry={refresh} /> : null}
        {routeBarV4 ? (
          <>
            <RouteBarV4
              value={{ from, to }}
              filters={[url.month, url.size, cat].filter(Boolean).length}
              onOpen={setRoute}
              onSwap={() => {
                track('courier_route_swap', routeProps(to, from))
                update({ from: to, to: from })
              }}
              onSearch={() => (smartOn ? navigate('/search') : setRoute('from'))}
              onFilter={() => (filterV4 ? setV4Sheet(true) : setSheet('all'))}
            />
            <MetaRow
              side={side}
              total={list.isSuccess ? total : null}
              loading={list.isLoading}
              draft={draft}
              onSide={(next) => {
                track('courier_route_side_change', { side: next })
                update({ side: next, month: '', size: '', cat: '' })
              }}
              onAlert={() => setAlert(true)}
            />
            <ActiveChips side={side} value={{ month: url.month, size: url.size, cat }} categoryName={(code) => locations.data?.categories.find((row) => row.code === code)?.name_fa ?? code} onChange={update} />
          </>
        ) : (
          <>
        <div className="flex flex-none flex-col gap-2 md:flex-row md:items-center md:gap-3">
          <div className="flex-none md:w-[280px]">
            <SideTabs
              value={side}
              onChange={(next) => {
                track('courier_route_side_change', { side: next })
                update({ side: next, month: '', size: '', cat: '' })
              }}
            />
          </div>
          <RouteBar
            value={{ from, to }}
            onOpen={setRoute}
            onSwap={() => {
              track('courier_route_swap', routeProps(to, from))
              update({ from: to, to: from })
            }}
          />
        </div>
        <FilterRow side={side} value={filters} onOpen={setSheet} onChange={update} />
          </>
        )}
        {!from && !to && locations.data?.frequent?.length ? (
          <div className="flex flex-none gap-2 overflow-x-auto whitespace-nowrap" data-testid="frequent-chips">
            {locations.data.frequent.map((row) => (
              <Button
                key={`${placeKey(row.origin)}-${placeKey(row.destination)}`}
                variant="outline"
                size="sm"
                onClick={() => {
                  track('courier_route_frequent_picked', { surface: 'chips', ...routeProps(placeKey(row.origin), placeKey(row.destination)) })
                  update({ from: placeKey(row.origin), to: placeKey(row.destination) })
                }}
              >
                <bdi>{`${row.origin.name_fa} ← ${row.destination.name_fa}`}</bdi>
              </Button>
            ))}
          </div>
        ) : null}
        {empty ? <div className="flex flex-1 items-center justify-center">{body()}</div> : body()}
      </div>
      <RouteSheet open={route !== null} side={route ?? 'from'} value={{ from, to }} onClose={() => setRoute(null)} onDone={(next) => (setRoute(null), update(next))} />
      <FilterSheet open={sheet !== null} kind={sheet ?? 'all'} side={side} from={from} to={to} value={filters} onClose={() => setSheet(null)} onApply={(next) => (setSheet(null), update(next))} />
      {filterV4 ? <FilterSheetV4 open={v4Sheet} side={side} from={from} to={to} value={{ month: url.month, size: url.size, cat }} categories={locations.data?.categories ?? []} onClose={() => setV4Sheet(false)} onApply={(next) => (setV4Sheet(false), update(next))} /> : null}
      <AlertSheet open={alert} draft={draft} onClose={() => setAlert(false)} onRoute={(next) => update(next)} />
    </Page>
  )
}
