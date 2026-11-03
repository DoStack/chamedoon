import { CheckCheck, ChevronDown, CircleAlert, Grid2x2, House, MapPin, RefreshCw, Rows3, Search, SearchX, SlidersHorizontal, Sparkles, TriangleAlert, X } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType, useSearchParams } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
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
import { staleHours, useFeed } from '@/lib/feed'
import { fa } from '@/lib/format'
import { appliedChips, applyView, clearFilters, FILTER_KEYS, readView, typeTiles, waysOut, writeView, type Sort, type View } from '@/lib/listing-view'
import { useCardOverrides } from '@/lib/overrides'
import { track } from '@/lib/report'
import { textSearchOn } from '@/lib/text-search'
import { isVersionAtLeast } from '@/lib/telegram'
import { cn } from '@/lib/utils'
import { AreaSheet } from './area-sheet'
import { CitySheet } from './city-sheet'
import { FilterSheet, type FilterSection } from './filter-sheet'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

const SORT_LABELS: Record<Sort, string> = { new: 'جدیدترین', value: 'ارزنده‌ترین', cheap: 'ارزان‌ترین', dear: 'گران‌ترین', date: 'نزدیک‌ترین به تاریخ ورود' }
const PAGE = 30
const SLOW_MS = 3000

function placeholder(city: SavedCity) {
  if (city.country === 'DE') return 'مثلاً: اتاق WG نزدیک U-Bahn'
  if (city.slug === 'vancouver') return 'مثلاً: ۱ خوابه نزدیک SkyTrain'
  return 'مثلاً: ۱ خوابه نزدیک مترو'
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
function readLastVisit(slug: string) {
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
  rememberListSearch(location.search)
  const [returning] = useState(() => ((navigationType === 'POP' || (location.state as { returning?: boolean } | null)?.returning) && leftAt?.key === memoryKey ? leftAt : null))
  const [shown, setShown] = useState(returning?.shown ?? PAGE)
  const lastVisit = useMemo(() => readLastVisit(city.slug), [city.slug])
  const overrides = useCardOverrides()

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
  const restored = useRef(!returning)
  useLayoutEffect(() => {
    if (restored.current || !scroller.current) return
    restored.current = true
    scroller.current.scrollTop = scrollTop.current
  })

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
        if (shown < cards.length) setShown((value) => value + PAGE)
        else if (feed.hasMore && !feed.loadingMore && !feed.moreFailed) void feed.loadMore()
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
    showToast({ message: 'همه‌ی فیلترها پاک شد', icon: 'check', action: { label: 'برگردان', onClick: () => applyFilters(before) } })
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

  const header = (
    <div className="flex flex-none items-center gap-2">
      <Button variant="outline" onClick={() => setCityOpen(true)} data-testid="city-button">
        <MapPin />
        {city.name_fa}
        <ChevronDown />
      </Button>
      <div className="min-w-0 flex-1">
        <InputGroup>
          <InputGroupAddon>
            <Search />
          </InputGroupAddon>
          <InputGroupInput aria-label="جستجو" placeholder={placeholder(city)} value={view.q} data-testid="list-search" {...(textSearchOn() ? { readOnly: true, onClick: () => navigate('/search') } : { onChange: (event: React.ChangeEvent<HTMLInputElement>) => update((next) => (event.target.value ? next.set('q', event.target.value) : next.delete('q'))) })} />
        </InputGroup>
      </div>
    </div>
  )

  const filterRow = (
    <div className={cn('flex flex-none items-center gap-2', chips.length ? 'flex-wrap' : 'overflow-x-auto whitespace-nowrap')} data-testid="filter-row">
      <Button variant="outline" size="sm" onClick={() => setSheet({ focus: null })} data-testid="filter-button">
        <SlidersHorizontal />
        فیلتر
        {chips.length ? <Badge>{fa(chips.length)}</Badge> : null}
      </Button>
      {chips.length ? (
        <>
          {chips.map((chip) => (
            <Button key={chip.id} variant="secondary" size="sm" data-testid="filter-chip" onClick={() => applyFilters(chip.clear(view))}>
              {chip.label}
              <X />
            </Button>
          ))}
          <Button variant="ghost" size="sm" onClick={clearAll}>
            پاک کردن همه
          </Button>
        </>
      ) : (
        <>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'type' })}>
            نوع خانه
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'budget' })}>
            بودجه
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAreasOpen(true)}>
            محله
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'mode' })}>
            نوع اجاره
            <ChevronDown />
          </Button>
          {country === 'DE' ? (
            <Button variant="outline" size="sm" onClick={() => setSheet({ focus: 'reg' })}>
              ملد
              <ChevronDown />
            </Button>
          ) : null}
        </>
      )}
    </div>
  )

  const countBar = (
    <div className="flex flex-none items-center justify-between gap-2 text-sm" data-testid="count-bar">
      {/* On the fallback feed with pages left this is a floor, not the total. */}
      <b className="font-extrabold whitespace-nowrap">
        {fa(cards.length)}
        {feed.hasMore ? '+' : ''} آگهی
      </b>
      <span className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => setSortOpen(true)}>
          {SORT_LABELS[view.sort]}
          <ChevronDown />
        </Button>
        {view.kind === 'offer' ? (
          <Button variant="ghost" size="icon" aria-label="تغییر نما" onClick={() => update((next) => (compact ? next.delete('view') : next.set('view', 'compact')))}>
            {compact ? <Rows3 className="size-5" /> : <Grid2x2 className="size-5" />}
          </Button>
        ) : null}
      </span>
    </div>
  )

  // Also when the browser says online but only the cached file answers (F25#1).
  const offlineBar = online && !feed.cacheOnly ? null : <OfflineBar onRetry={() => void feed.refresh()} />
  const newCount = lastVisit && view.kind === 'offer' ? feed.cards.filter((card) => card.kind === 'offer' && card.first_seen > lastVisit).length : 0
  const emptyCity = feed.hasData && feed.cards.length === 0 && !feed.hasMore
  const seekingMore = feed.hasData && cards.length === 0 && (feed.hasMore || feed.loadingMore)
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
            <Button className="w-full" onClick={() => void feed.refresh()}>
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
              <Button variant="outline" size="sm" onClick={() => void feed.loadMore()}>
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
                    <Button key={way.label} variant="outline" size="sm" onClick={() => applyFilters(way.view)}>
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
              <Button variant="outline" size="sm" onClick={() => void feed.refresh()}>
                <RefreshCw />
                تلاش مجدد
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {countBar}
        {newCount > 0 ? (
          <div className="flex flex-none">
            <Badge variant="secondary" data-testid="new-count">
              <Sparkles />
              {fa(newCount)} آگهی تازه از آخرین بازدید
            </Badge>
          </div>
        ) : null}
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
        <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto pb-4" data-testid="list-scroll" onScroll={(event) => (scrollTop.current = event.currentTarget.scrollTop)} onClickCapture={(event) => (scrollTop.current = event.currentTarget.scrollTop)}>
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
                  <Button variant="outline" size="sm" onClick={() => void feed.loadMore()}>
                    <RefreshCw />
                    تلاش مجدد
                  </Button>
                </AlertDescription>
              </Alert>
            ) : null}
            {atEnd ? (
              <Card className="col-span-full flex-row items-center gap-2 p-4" data-testid="list-end">
                <CheckCheck className="size-5" aria-hidden />
                <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">همه‌ی {fa(cards.length)} آگهی را دیدید</h3>
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
      <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 pt-2 md:gap-3 md:px-6 md:pt-3">
        {offlineBar}
        {header}
        {filterRow}
        {body}
      </div>
      <MainNav place="bottom" />
      <AppSheet open={sortOpen} onClose={() => setSortOpen(false)} label="مرتب‌سازی" title="مرتب‌سازی" testId="sort-sheet">
        <RadioGroup dir="rtl" value={view.sort} className="gap-0">
          {(Object.keys(SORT_LABELS) as Sort[]).map((sort) => {
            // Off until a date is set (F04 rules).
            const off = sort === 'date' && !view.move
            return (
              <label key={sort} className="flex min-h-12 items-center gap-3 py-1">
                <RadioGroupItem
                  value={sort}
                  aria-label={SORT_LABELS[sort]}
                  disabled={off}
                  onClick={() => {
                    update((next) => (sort === 'new' ? next.delete('sort') : next.set('sort', sort)))
                    setSortOpen(false)
                  }}
                />
                <span className={cn('text-[15px] font-semibold', off && 'text-muted-foreground')}>{SORT_LABELS[sort]}</span>
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
