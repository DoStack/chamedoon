import { ArrowDownUp, CheckCheck, ChevronDown, CircleAlert, Grid2x2, House, Map as MapIcon, MapPin, RefreshCw, Rows3, Search, SearchX, SlidersHorizontal, TriangleAlert, X } from 'lucide-react'
import { useDragScroll } from '@/lib/use-drag-scroll'
import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType, useSearchParams } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { CompactBar } from '@/components/compact-bar'
import { CardSkeleton, ListingCard, WantedCard } from '@/components/listing-card'
import { MainNav, rememberListSearch } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { saveCity, type SavedCity } from '@/lib/city'
import { useAlertEntry, useMapOn } from '@/lib/features'
import { staleHours, useFeed } from '@/lib/feed'
import { cityLabel, fa } from '@/lib/format'
import { appliedChips, applyView, clearFilters, FILTER_KEYS, readView, typeTiles, waysOut, writeView, type Sort, type View } from '@/lib/listing-view'
import { useCardOverrides } from '@/lib/overrides'
import { appDir, t } from '@/lib/locale'
import { track } from '@/lib/report'
import { textSearchOn } from '@/lib/text-search'
import { isVersionAtLeast } from '@/lib/telegram'
import { cn } from '@/lib/utils'
import { AreaSheet } from './area-sheet'
import { CitySheet } from './city-sheet'
import { FilterSheet, type FilterSection } from './filter-sheet'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

// Only fetched once alerts are open. Off, the count bar is what it was.
const AlertEntry = lazy(() => import('./alert-entry'))

const SORT_LABELS: Record<Sort, string> = { new: 'جدیدترین', value: 'ارزنده‌ترین', cheap: 'ارزان‌ترین', dear: 'گران‌ترین', date: 'نزدیک‌ترین به تاریخ ورود' }
const PAGE = 30
const SLOW_MS = 3000
// How far back past the point it collapsed before the full header returns, so a slow scroll around that point never flickers.
const COLLAPSE_SLACK = 16
// The compact row over the list (py-2 around a 36px box, and its border), plus a little air under it.
const COMPACT_HEIGHT = 53 + 4
const COMPACT_IDS = { city: 'compact-city', search: 'compact-search', filter: 'compact-filter' }

function placeholder(city: SavedCity) {
  if (city.country === 'DE') return t('مثلاً: اتاق WG نزدیک U-Bahn')
  if (city.slug === 'vancouver') return t('مثلاً: ۱ خوابه نزدیک SkyTrain')
  return t('مثلاً: ۱ خوابه نزدیک مترو')
}

function usePullToRefresh(ref: React.RefObject<HTMLElement | null>, onRefresh: () => void, enabled: boolean) {
  const start = useRef<number | null>(null)
  useEffect(() => {
    const element = ref.current
    if (!enabled || !element) return
    const down = (event: TouchEvent) => {
      start.current = element.scrollTop <= 0 ? event.touches[0].clientY : null
    }
    const move = (event: TouchEvent) => {
      if (start.current !== null && event.touches[0].clientY - start.current > 70) {
        start.current = null
        onRefresh()
      }
    }
    element.addEventListener('touchstart', down, { passive: true })
    element.addEventListener('touchmove', move, { passive: true })
    return () => {
      element.removeEventListener('touchstart', down)
      element.removeEventListener('touchmove', move)
    }
  }, [ref, onRefresh, enabled])
}

const LAST_VISIT = (slug: string) => `chamedoon.visit.${slug}`
const lastVisits = new Map<string, string | null>()

// F02#1: read once per city per session, then this visit becomes the next base.
export function readLastVisit(slug: string) {
  if (lastVisits.has(slug)) return lastVisits.get(slug)!
  let value: string | null = null
  try {
    value = localStorage.getItem(LAST_VISIT(slug))
    localStorage.setItem(LAST_VISIT(slug), new Date().toISOString())
  } catch {
    // No storage: no base to compare with.
  }
  lastVisits.set(slug, value)
  return value
}

// Where the list was left. Coming back to the same city and filters returns there (F04#1).
let leftAt: { key: string; top: number; shown: number } | null = null

export function ListScreen({ city }: { city: SavedCity }) {
  const dragRef = useDragScroll()
  const [params, setParams] = useSearchParams()
  const view = useMemo(() => readView(params), [params])
  const compact = params.get('view') === 'compact'
  const feed = useFeed(city.slug, city.country)
  const online = useOnline()
  const [sortOpen, setSortOpen] = useState(false)
  // `?change=1`: «تغییر» on the toast of a first open from a link (F01#8).
  const [cityOpen, setCityOpen] = useState(() => params.get('change') === '1')
  const memoryKey = `${city.slug}?${params}`
  const navigationType = useNavigationType()
  const location = useLocation()
  const navigate = useNavigate()
  const alertEntry = useAlertEntry()
  rememberListSearch(location.search)
  const [returning] = useState(() => ((navigationType === 'POP' || (location.state as { returning?: boolean } | null)?.returning) && leftAt?.key === memoryKey ? leftAt : null))
  const [shown, setShown] = useState(returning?.shown ?? PAGE)
  const lastVisit = useMemo(() => readLastVisit(city.slug), [city.slug])
  const overrides = useCardOverrides()
  const mapOn = useMapOn()
  // Fetched ahead, so the map screen still opens when the connection drops. The map library itself loads only on it.
  useEffect(() => {
    if (mapOn) void import('./map')
  }, [mapOn])

  const cards = useMemo(() => applyView(feed.cards, view).map((card) => overrides[card.id] ?? card), [feed.cards, view, overrides])
  const visible = cards.slice(0, shown)
  const stale = staleHours(feed.generatedAt)
  const country = feed.cards[0]?.country ?? city.country
  const region = feed.cards[0]?.region ?? ''
  const tiles = useMemo(() => typeTiles(country, region), [country, region])
  const chips = appliedChips(view, tiles, country)
  const top = Math.max(0, ...feed.cards.map((card) => card.price_monthly ?? 0))
  const ways = cards.length === 0 && !feed.hasMore ? waysOut(feed.cards, view, top) : []
  const slow = useAfter(SLOW_MS, feed.loading)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const pull = isVersionAtLeast('7.7')
  const [pulled, setPulled] = useState(false)

  const scrollTop = useRef(returning?.top ?? 0)
  const position = useRef({ key: memoryKey, shown })
  position.current = { key: memoryKey, shown }
  useEffect(() => () => void (leftAt = { ...position.current, top: scrollTop.current }), [])
  const restored = useRef(false)
  useLayoutEffect(() => {
    // The header scrolls too, so the scroller is there before the cards are. Wait for them, or the top is clamped to nothing.
    if (restored.current || !scroller.current || !visible.length) return
    restored.current = true
    scroller.current.scrollTop = returning ? scrollTop.current : compactTop()
    scrolled(scroller.current) // Before the paint, so the full rows never flash.
  })

  // The full header scrolls away with the list, and once the search row has gone, the compact row lies over the top. Nothing
  // in the flow changes, so the list cannot jump. The list opens already scrolled past the full rows, the count row just under
  // the compact one: scrolling up brings them back, and scrolling down takes them away before the cards move.
  const searchRow = useRef<HTMLDivElement>(null)
  const countRow = useRef<HTMLDivElement>(null)
  const compactTop = () => {
    const row = searchRow.current
    const at = row ? row.offsetTop + row.offsetHeight : 0
    const count = countRow.current
    return count ? Math.max(at, count.offsetTop - COMPACT_HEIGHT) : at
  }
  const [collapsed, setCollapsed] = useState(false)
  const isCollapsed = useRef(false)
  const collapseTracked = useRef(false)
  const scrolled = (element: HTMLDivElement) => {
    scrollTop.current = element.scrollTop
    const row = searchRow.current
    const at = row ? row.offsetTop + row.offsetHeight : 0
    const next = isCollapsed.current ? element.scrollTop > at - COLLAPSE_SLACK : element.scrollTop >= at
    if (next === isCollapsed.current) return
    isCollapsed.current = next
    setCollapsed(next)
    if (next && !collapseTracked.current) {
      collapseTracked.current = true
      track('header_collapse')
    }
  }

  // New results start from their top, under the compact row.
  const shownKey = useRef(memoryKey)
  useEffect(() => {
    if (shownKey.current === memoryKey) return
    shownKey.current = memoryKey
    scroller.current?.scrollTo({ top: compactTop() })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memoryKey])

  const refresh = () => {
    setPulled(true)
    track('feed_refreshed')
    void feed.refresh().finally(() => setPulled(false))
  }
  usePullToRefresh(scroller, refresh, pull)

  useEffect(() => {
    if (feed.hasData) track('feed_view', { city: city.slug, count: cards.length })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.hasData, city.slug])

  useEffect(() => {
    if (feed.hasData && cards.length === 0 && feed.hasMore && !feed.loadingMore && !feed.moreFailed) void feed.loadMore()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.hasData, cards.length, feed.hasMore, feed.loadingMore, feed.moreFailed])

  useEffect(() => {
    if (feed.failed) track('feed_failed', { code: feed.failure?.code })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.failed])

  useEffect(() => {
    if (feed.failure?.code === 'rate_limited' && feed.hasData) rateLimitedToast(() => void feed.refresh())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.failure])

  useEffect(() => {
    const element = sentinel.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return
        if (shown < cards.length) {
          track('feed_more', { shown })
          setShown((value) => value + PAGE)
        } else if (feed.hasMore && !feed.loadingMore && !feed.moreFailed) {
          track('feed_more', { shown, fetch: true })
          void feed.loadMore()
        } else if (!feed.hasMore) track('feed_end', { count: cards.length })
      },
      { root: scroller.current, rootMargin: '400px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown, cards.length, feed.hasMore, feed.loadingMore, feed.moreFailed])

  const update = (change: (next: URLSearchParams) => void) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        change(next)
        return next
      },
      { replace: true },
    )

  const applyFilters = (next: View) => {
    setShown(PAGE)
    setParams((previous) => writeView(previous, next), { replace: true })
  }

  // F05#8: undo brings back exactly what was on.
  const clearAll = () => {
    const before = view
    track('filters_cleared')
    applyFilters(clearFilters(view))
    showToast({ message: 'همه‌ی فیلترها پاک شد', icon: 'check', action: { label: 'برگردان', onClick: () => { track('filters_restored'); applyFilters(before) } } })
  }

  const [sheet, setSheet] = useState<{ focus: FilterSection | null } | null>(null)
  const [areasOpen, setAreasOpen] = useState(false)

  // City keeps type and budget and clears areas; country clears all (F03). Telegram-side save failure gets F25#10.
  const pickCity = (next: SavedCity) => {
    const areas = params.getAll('areas')
    const before = new URLSearchParams(params)
    update((draft) => {
      draft.delete('city')
      draft.delete('areas')
      draft.delete('parts')
      if (next.country !== city.country) FILTER_KEYS.filter((key) => key !== 'sort').forEach((key) => draft.delete(key))
    })
    setShown(PAGE)
    const save = () =>
      void saveCity(next).then((saved) => {
        if (!saved) {
          track('prefs_failed')
          saveFailedToast(save)
        }
      })
    save()
    track('city_changed', { city: next.slug })
    if (areas.length && next.country === city.country) {
      track('filters_reset')
      showToast({
        message: 'فیلترهای محله پاک شد',
        icon: 'check',
        action: {
          label: 'برگردان',
          onClick: () => {
            setParams(before, { replace: true })
          },
        },
      })
    }
  }

  const openSearch = () => {
    if (textSearchOn()) return navigate('/search')
    // Typing here would change the list under the row. The full box takes it, at the top.
    scroller.current?.scrollTo({ top: 0 })
    searchRow.current?.querySelector('input')?.focus()
  }

  const toMap = () => {
    track('map_open', { from: isCollapsed.current ? 'compact' : 'list' })
    navigate({ pathname: '/map', search: location.search })
  }

  const compactBar = collapsed ? (
    <div className="absolute inset-x-0 top-0 z-10 border-b border-border bg-background px-4 py-2 duration-150 animate-in fade-in md:px-6" data-testid="compact-header">
      <CompactBar city={cityLabel(city)} onCity={() => setCityOpen(true)} input={{ readOnly: true, value: view.q, onClick: openSearch }} filters={chips.length} onFilter={() => setSheet({ focus: null })} ids={COMPACT_IDS}>
        {mapOn && view.kind === 'offer' ? (
          <Button variant="outline" size="icon" aria-label={t('نقشه')} className="flex-none" data-testid="compact-map" onClick={toMap}>
            <MapIcon />
          </Button>
        ) : null}
      </CompactBar>
    </div>
  ) : null

  const header = (
    <div ref={searchRow} className="flex flex-none items-center gap-2">
      <Button variant="outline" onClick={() => setCityOpen(true)} data-testid="city-button">
        <MapPin />
        {cityLabel(city)}
        <ChevronDown />
      </Button>
      <div className="min-w-0 flex-1">
        <InputGroup>
          <InputGroupAddon>
            <Search />
          </InputGroupAddon>
          <InputGroupInput aria-label="جستجو" placeholder={placeholder(city)} value={view.q} data-testid="list-search" {...(textSearchOn() ? { readOnly: true, onClick: () => navigate('/search') } : { onChange: (event: React.ChangeEvent<HTMLInputElement>) => update((next) => (event.target.value ? next.set('q', event.target.value) : next.delete('q'))), onBlur: (event: React.FocusEvent<HTMLInputElement>) => { if (event.target.value) track('search_submit', { query_length: event.target.value.length, from: 'list_box' }) } })} />
        </InputGroup>
      </div>
    </div>
  )

  const filterRow = (
    <div ref={dragRef} className={cn('flex flex-none items-center gap-2', chips.length ? 'flex-wrap' : 'overflow-x-auto whitespace-nowrap')} data-testid="filter-row">
      <Button variant="outline" size="sm" onClick={() => setSheet({ focus: null })} data-testid="filter-button">
        <SlidersHorizontal />
        {t('فیلتر')}
        {chips.length ? <Badge>{fa(chips.length)}</Badge> : null}
      </Button>
      {chips.length ? (
        <>
          {chips.map((chip) => (
            <Button key={chip.id} variant="secondary" size="sm" data-testid="filter-chip" onClick={() => { track('filter_chip_removed', { chip: chip.id.split('-')[0], from: 'list' }); applyFilters(chip.clear(view)) }}>
              {chip.label}
              <X />
            </Button>
          ))}
          <Button variant="ghost" size="sm" onClick={clearAll}>
            {t('پاک کردن همه')}
          </Button>
        </>
      ) : (
        <>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'type' })}>
            {t('نوع خانه')}
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'budget' })}>
            {t('بودجه')}
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAreasOpen(true)}>
            {t('محله')}
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'mode' })}>
            {t('نوع اجاره')}
            <ChevronDown />
          </Button>
          {country === 'DE' ? (
            <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'reg' })}>
              {t('ملد')}
              <ChevronDown />
            </Button>
          ) : null}
        </>
      )}
    </div>
  )

  const newCount = lastVisit && view.kind === 'offer' ? feed.cards.filter((card) => card.kind === 'offer' && card.first_seen > lastVisit).length : 0
  // One row: the count, with what is new since the last visit, then the controls. Nothing wraps, 360px up.
  const countBar = (
    <div ref={countRow} className="flex min-h-8 flex-none items-center justify-between gap-2 text-sm" data-testid="count-bar">
      <span className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
        {/* On the fallback feed with pages left this is a floor, not the total. */}
        <b className="font-extrabold">
          {t('{n} آگهی', { n: `${fa(cards.length)}${feed.hasMore ? '+' : ''}` })}
        </b>
        {newCount > 0 && cards.length > 0 ? (
          <span className="truncate text-xs font-semibold text-primary" data-testid="new-count">
            {t('{n} تازه', { n: fa(newCount) })}
          </span>
        ) : null}
      </span>
      <span className="flex flex-none items-center gap-0.5">
        {alertEntry && view.kind === 'offer' ? (
          <Suspense fallback={null}>
            <AlertEntry view={view} city={city} count={cards.length} />
          </Suspense>
        ) : null}
        {mapOn && view.kind === 'offer' ? (
          <Button variant="ghost" size="sm" className="h-8" data-testid="map-button" onClick={toMap}>
            <MapIcon />
            {t('نقشه')}
          </Button>
        ) : null}
        <Button variant="ghost" size="sm" className="h-8" aria-label={t(SORT_LABELS[view.sort])} data-testid="sort-button" onClick={() => setSortOpen(true)}>
          <ArrowDownUp />
          {/* The label shows once there is room for it; a phone gets the icon and the label is still read out. */}
          <span className="sr-only max-w-32 truncate min-[460px]:not-sr-only">{t(SORT_LABELS[view.sort])}</span>
        </Button>
        {view.kind === 'offer' ? (
          <Button variant="ghost" size="icon" aria-label={t('تغییر نما')} onClick={() => { track('view_toggle', { compact: !compact }); update((next) => (compact ? next.delete('view') : next.set('view', 'compact'))) }}>
            {compact ? <Rows3 className="size-5" /> : <Grid2x2 className="size-5" />}
          </Button>
        ) : null}
      </span>
    </div>
  )

  // Also when the browser says online but only the cached file answers (F25#1).
  const offlineBar = online && !feed.cacheOnly ? null : <OfflineBar onRetry={() => void feed.refresh()} />
  const emptyCity = feed.hasData && feed.cards.length === 0 && !feed.hasMore
  const seekingMore = feed.hasData && cards.length === 0 && (feed.hasMore || feed.loadingMore)
  const noMatch = feed.hasData && !emptyCity && !seekingMore && cards.length === 0
  useEffect(() => {
    if (noMatch) track('list_no_result')
    if (emptyCity) track('city_empty', { city: city.slug })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noMatch, emptyCity])
  const grid = cn('grid auto-rows-max gap-4 pt-1', compact ? 'grid-cols-2 md:grid-cols-4' : 'grid-cols-1 md:grid-cols-3')

  let body: React.ReactNode
  if (feed.loading) {
    body = (
      <>
        <div className="flex min-h-10 flex-none items-center justify-between" data-testid="first-load">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-5 w-40" />
        </div>
        {slow ? <SlowNote /> : null}
        <div className={cn(grid, 'min-h-0 flex-1 overflow-hidden')}>
          {Array.from({ length: 6 }, (_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      </>
    )
  } else if (feed.failed) {
    // F04#9
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title="آگهی‌ها بارگذاری نشد" testId="list-failed">
          <div className="flex w-full flex-col gap-2">
            <Button className="w-full" onClick={() => { track('retry_tap', { screen: 'list' }); void feed.refresh() }}>
              <RefreshCw />
              تلاش مجدد
            </Button>
            <Button variant="outline" className="w-full" onClick={() => setCityOpen(true)}>
              شهر دیگر
            </Button>
          </div>
        </StateBox>
      </div>
    )
  } else if (seekingMore) {
    // The loaded pages hold no match, but later ones can: F05#7 only after the last page.
    body = (
      <>
        {countBar}
        {feed.moreFailed ? (
          <Alert className="flex-none" data-testid="more-failed">
            <CircleAlert />
            <AlertDescription className="flex w-full items-center justify-between gap-2">
              <span className="text-foreground">ادامه‌ی آگهی‌ها بارگذاری نشد</span>
              <Button variant="outline" size="sm" onClick={() => { track('retry_tap', { screen: 'list_more' }); void feed.loadMore() }}>
                <RefreshCw />
                تلاش مجدد
              </Button>
            </AlertDescription>
          </Alert>
        ) : (
          <div className={cn(grid, 'min-h-0 flex-1 overflow-hidden')} data-testid="seeking-more">
            <CardSkeleton />
            <CardSkeleton />
          </div>
        )}
      </>
    )
  } else if (emptyCity) {
    // F01#10
    body = (
      <>
        {countBar}
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateBox icon={House} title={`هنوز آگهی‌ای در ${city.name_fa} نیست`} testId="city-empty">
            <Button variant="outline" className="w-full" onClick={() => setCityOpen(true)}>
              شهر دیگر
            </Button>
          </StateBox>
        </div>
      </>
    )
  } else if (cards.length === 0) {
    // F05#7
    body = (
      <>
        {countBar}
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateBox icon={SearchX} title="آگهی‌ای با این فیلترها نیست" testId="filter-empty">
            <div className="flex w-full flex-col gap-2">
              {ways.length ? (
                <div className="flex flex-wrap items-center justify-center gap-2" data-testid="ways-out">
                  {ways.map((way) => (
                    <Button key={way.label} variant="outline" size="sm" onClick={() => { track('filter_way_out', { from: 'list' }); applyFilters(way.view) }}>
                      {way.label}
                    </Button>
                  ))}
                </div>
              ) : null}
              <Button variant="outline" className="w-full" onClick={clearAll}>
                پاک کردن همه
              </Button>
            </div>
          </StateBox>
        </div>
      </>
    )
  } else {
    const atEnd = shown >= cards.length && !feed.hasMore
    body = (
      <>
        {stale ? (
          <Alert className="flex-none" data-testid="stale-note">
            <TriangleAlert />
            <AlertDescription className="flex w-full items-center justify-between gap-2">
              <span className="text-foreground">این فهرست از {fa(stale)} ساعت پیش به‌روز نشده است.</span>
              <Button variant="outline" size="sm" onClick={() => { track('retry_tap', { screen: 'list_stale' }); void feed.refresh() }}>
                <RefreshCw />
                تلاش مجدد
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {countBar}
        {!pull ? (
          <div className="flex flex-none justify-center">
            <Button variant="outline" size="sm" onClick={refresh} data-testid="refresh-button">
              <RefreshCw />
              به‌روزرسانی
            </Button>
          </div>
        ) : null}
        {pulled ? (
          <div className="flex min-h-7 flex-none justify-center" data-testid="refreshing">
            <Spinner />
          </div>
        ) : null}
        <div className="flex-none">
          <div className={grid}>
            {visible.map((card) => (view.kind === 'wanted' ? <WantedCard key={card.id} card={card} /> : <ListingCard key={card.id} card={card} closed={Boolean(overrides[card.id]?.closed)} />))}
            {feed.loadingMore ? (
              <>
                <CardSkeleton />
                <CardSkeleton />
              </>
            ) : null}
            {feed.moreFailed ? (
              <Alert className="col-span-full" data-testid="more-failed">
                <CircleAlert />
                <AlertDescription className="flex w-full items-center justify-between gap-2">
                  <span className="text-foreground">ادامه‌ی آگهی‌ها بارگذاری نشد</span>
                  <Button variant="outline" size="sm" onClick={() => { track('retry_tap', { screen: 'list_more' }); void feed.loadMore() }}>
                    <RefreshCw />
                    تلاش مجدد
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}
            {atEnd ? (
              <Card className="col-span-full flex-row items-center gap-2 p-4" data-testid="list-end">
                <CheckCheck className="size-5" aria-hidden />
                <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">{t('همه‌ی {n} آگهی را دیدید', { n: fa(cards.length) })}</h3>
              </Card>
            ) : null}
          </div>
          <div ref={sentinel} className="h-px" />
        </div>
      </>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <MainNav place="top" />
      {offlineBar ? <div className="flex-none px-4 pt-2 md:px-6">{offlineBar}</div> : null}
      <div className="relative min-h-0 flex-1">
        {compactBar}
        <div ref={scroller} className="flex h-full flex-col gap-2 overflow-y-auto px-4 pt-2 pb-4 md:gap-3 md:px-6 md:pt-3" data-testid="list-scroll" onScroll={(event) => scrolled(event.currentTarget)} onClickCapture={(event) => (scrollTop.current = event.currentTarget.scrollTop)}>
          {header}
          {filterRow}
          {body}
        </div>
      </div>
      <MainNav place="bottom" />
      <AppSheet open={sortOpen} onClose={() => setSortOpen(false)} label={t('مرتب‌سازی')} title={t('مرتب‌سازی')} testId="sort-sheet">
        <RadioGroup dir={appDir()} value={view.sort} className="gap-0">
          {(Object.keys(SORT_LABELS) as Sort[]).map((sort) => {
            // Off until a date is set (F04 rules).
            const off = sort === 'date' && !view.move
            return (
              <label key={sort} className="flex min-h-12 items-center gap-3 py-1">
                <RadioGroupItem
                  value={sort}
                  aria-label={t(SORT_LABELS[sort])}
                  disabled={off}
                  onClick={() => {
                    track('sort_changed', { sort })
                    update((next) => (sort === 'new' ? next.delete('sort') : next.set('sort', sort)))
                    setSortOpen(false)
                  }}
                />
                <span className={cn('text-[15px] font-semibold', off && 'text-muted-foreground')}>{t(SORT_LABELS[sort])}</span>
              </label>
            )
          })}
        </RadioGroup>
      </AppSheet>
      <FilterSheet open={sheet !== null} focus={sheet?.focus ?? null} onClose={() => setSheet(null)} cards={feed.cards} view={view} country={country} region={region} partial={feed.hasMore} onApply={applyFilters} />
      {areasOpen ? (
        <AreaSheet
          open
          partial={feed.hasMore}
          onClose={() => setAreasOpen(false)}
          cards={feed.cards}
          view={view}
          currentSlug={city.slug}
          onApply={(areas) => applyFilters({ ...view, areas })}
          onSearchText={(q) => applyFilters({ ...view, q })}
          onChangeCity={(slug) => update((next) => next.set('city', slug))}
        />
      ) : null}
      <CitySheet
        open={cityOpen}
        current={city}
        onClose={() => {
          setCityOpen(false)
          if (params.has('change')) update((next) => next.delete('change'))
        }}
        onPick={pickCity}
      />
    </div>
  )
}
