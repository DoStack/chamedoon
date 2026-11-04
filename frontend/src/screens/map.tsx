import { useQuery } from '@tanstack/react-query'
import { ChevronDown, CircleAlert, House, List, LocateFixed, MapPin, MapPinOff, Minus, Plus, RefreshCw, Search, SearchX, SlidersHorizontal, X, ZoomIn } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import type { Card, CityAreas } from '@/api/types'
import { showToast } from '@/components/app-toast'
import { CardSkeleton, ListingCard } from '@/components/listing-card'
import { MainNav, rememberListSearch } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Skeleton } from '@/components/ui/skeleton'
import { getMeta } from '@/lib/access'
import { useAlertEntry } from '@/lib/features'
import { publicFetch, unwrap } from '@/lib/api'
import { useBack } from '@/lib/back'
import { findCity, saveCity, toSaved, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { fa } from '@/lib/format'
import { appliedChips, applyView, clearFilters, FILTER_KEYS, readView, typeTiles, writeView, type View } from '@/lib/listing-view'
import { useCardOverrides } from '@/lib/overrides'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { useCities } from '@/lib/use-cities'
import { cn } from '@/lib/utils'
import { CitySheet } from './city-sheet'
import { FilterSheet } from './filter-sheet'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'
import { readLastVisit } from './list'
import { MapCanvas, type MapBox, type MapHandle, type MapPin as Pin, type MapPoint } from './map-canvas'

const AlertEntry = lazy(() => import('./alert-entry'))

const PAGE = 30
const SLOW_MS = 3000
// Further out than this the pins of a metro lie on top of each other (F09#7).
const MIN_PIN_ZOOM = 7
const MAX_CARDS = 600
const SHEET = ['peek', 'half', 'full'] as const
type Sheet = (typeof SHEET)[number]
const DRAG = 30
const SHEET_HEIGHT: Record<Sheet, string> = { peek: '', half: 'h-[200px]', full: 'h-[70%]' }

function useAreas(city: SavedCity) {
  return useQuery({
    queryKey: ['areas', city.country, city.slug],
    retry: false,
    staleTime: 300_000,
    queryFn: () => unwrap(publicFetch<CityAreas>(`/api/v1/public/areas/?${new URLSearchParams({ country: city.country, city: city.slug })}`, { timeoutMs: 8_000 })),
  })
}

const inBox = (point: MapPoint, box: MapBox) => point.lat >= box.south && point.lat <= box.north && point.lng >= box.west && point.lng <= box.east

function MapScreen({ city, styleUrl }: { city: SavedCity; styleUrl: string }) {
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  rememberListSearch(location.search)
  const view = useMemo<View>(() => ({ ...readView(params), kind: 'offer' }), [params])
  const feed = useFeed(city.slug, city.country)
  const alertEntry = useAlertEntry()
  const areas = useAreas(city)
  const online = useOnline()
  const overrides = useCardOverrides()
  const lastVisit = useMemo(() => readLastVisit(city.slug), [city.slug])
  const canvas = useRef<MapHandle>(null)

  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(false)
  const [zoom, setZoom] = useState<number | null>(null)
  const [moved, setMoved] = useState(false)
  const [region, setRegion] = useState<MapBox | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [sheet, setSheet] = useState<Sheet>('half')
  const [shown, setShown] = useState(PAGE)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [cityOpen, setCityOpen] = useState(false)
  const [me, setMe] = useState<MapPoint | null>(null)

  const cards = useMemo(() => applyView(feed.cards, view).map((card) => overrides[card.id] ?? card), [feed.cards, view, overrides])
  const country = feed.cards[0]?.country ?? city.country
  const placeRegion = feed.cards[0]?.region ?? ''
  const chips = appliedChips(view, typeTiles(country, placeRegion), country)

  // The fallback feed comes in pages, and a pin's count needs them all.
  useEffect(() => {
    if (feed.hasMore && !feed.loadingMore && !feed.moreFailed && feed.cards.length < MAX_CARDS) void feed.loadMore()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.hasMore, feed.loadingMore, feed.moreFailed, feed.cards.length])

  const points = useMemo(() => {
    const found = new Map<number, MapPoint & { name: string }>()
    for (const row of areas.data?.areas ?? []) if (row.lat != null && row.lng != null) found.set(row.id, { lat: row.lat, lng: row.lng, name: row.name_fa })
    return found
  }, [areas.data])

  const onMap = useMemo(() => cards.filter((card) => card.area_id != null && points.has(card.area_id)), [cards, points])
  const offMapCount = cards.length - onMap.length

  const pins = useMemo<Pin[]>(() => {
    const byArea = new Map<number, Card[]>()
    for (const card of onMap) byArea.set(card.area_id!, [...(byArea.get(card.area_id!) ?? []), card])
    return [...byArea].map(([id, rows]) => {
      const point = points.get(id)!
      return {
        id,
        lat: point.lat,
        lng: point.lng,
        // No « · » here: in this font it reads as a Persian zero beside the count.
        label: `${point.name} ${fa(rows.length)}`,
        selected: id === selected,
        seen: Boolean(lastVisit) && rows.every((card) => card.first_seen <= lastVisit!),
      }
    })
  }, [onMap, points, selected, lastVisit])

  // What to frame: the areas with results, or the whole metro when nothing matches.
  const frame = useMemo(() => (pins.length ? pins : [...points.values()]), [pins, points])
  const mapGone = failed || areas.isError
  const zoomedOut = !mapGone && zoom !== null && zoom < MIN_PIN_ZOOM && points.size > 0

  useEffect(() => {
    if (selected !== null && !pins.some((pin) => pin.id === selected)) setSelected(null)
  }, [pins, selected])

  useEffect(() => {
    if (ready) track('map_view', { city: city.slug, pins: pins.length })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, city.slug])

  const slow = useAfter(SLOW_MS, !ready && !mapGone)
  useEffect(() => {
    if (slow) track('map_slow')
  }, [slow])

  const inRegion = useMemo(() => (region ? onMap.filter((card) => inBox(points.get(card.area_id!)!, region)) : onMap), [region, onMap, points])
  const wholeCity = mapGone || zoomedOut
  const listed = wholeCity ? cards : selected !== null ? onMap.filter((card) => card.area_id === selected) : inRegion
  const visible = listed.slice(0, shown)
  const more = feed.hasMore ? '+' : ''
  const title = selected !== null && !wholeCity ? `${fa(listed.length)}${more} آگهی` : `${fa(listed.length)}${more} آگهی ${wholeCity ? `در ${city.name_fa}` : 'در این ناحیه'}`
  const emptyCity = feed.hasData && feed.cards.length === 0 && !feed.hasMore
  const emptyFilters = feed.hasData && !emptyCity && cards.length === 0 && !feed.hasMore
  const emptyRegion = Boolean(region) && !wholeCity && listed.length === 0 && !emptyFilters && !emptyCity
  const showOffMap = !wholeCity && selected === null && offMapCount > 0 && feed.hasData

  useEffect(() => {
    if (emptyRegion) track('map_empty')
  }, [emptyRegion])
  useEffect(() => {
    if (emptyFilters) track('map_filter_empty')
  }, [emptyFilters])
  useEffect(() => {
    if (showOffMap) track('map_off_map', { count: offMapCount })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showOffMap])

  const fail = (event: 'map_failed' | 'map_unsupported') => {
    track(event)
    setFailed(true)
    setSheet('peek')
  }

  const retry = () => {
    setFailed(false)
    setReady(false)
    setZoom(null)
    setAttempt((value) => value + 1)
    if (areas.isError) void areas.refetch()
  }

  const toList = () => navigate({ pathname: '/', search: location.search })
  // Back drops the pin, then the region, then returns to the list (BRIEF).
  useBack(selected !== null ? () => setSelected(null) : region ? () => setRegion(null) : toList)

  const pick = (id: number) => {
    track('pin_selected', { area: id })
    setShown(PAGE)
    setSelected(id === selected ? null : id)
    setSheet('half')
  }

  const searchHere = () => {
    const box = canvas.current?.box()
    if (!box) return
    track('map_search_here')
    setRegion(box)
    setSelected(null)
    setMoved(false)
    setShown(PAGE)
  }

  const zoomOutOfEmpty = () => {
    track('map_zoom_out_empty')
    setRegion(null)
    setMoved(false)
    canvas.current?.fit()
  }

  const zoomBackIn = () => {
    track('map_zoom_chip')
    setMoved(false)
    canvas.current?.fit()
  }

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

  const clearAll = () => {
    const before = view
    track('filters_cleared')
    applyFilters(clearFilters(view))
    showToast({ message: 'همه‌ی فیلترها پاک شد', icon: 'check', action: { label: 'برگردان', onClick: () => { track('filters_restored'); applyFilters(before) } } })
  }

  // City keeps type and budget and clears areas; country clears all (F03).
  const pickCity = (next: SavedCity) => {
    update((draft) => {
      draft.delete('city')
      draft.delete('areas')
      draft.delete('parts')
      if (next.country !== city.country) FILTER_KEYS.filter((key) => key !== 'sort').forEach((key) => draft.delete(key))
    })
    void saveCity(next)
    track('city_changed', { city: next.slug })
  }

  // Bot API 8.0. The permission prompt is Telegram's own (F09#4).
  const locations = isVersionAtLeast('8.0') ? getWebApp()?.LocationManager : undefined
  const denied = () => {
    track('location_denied')
    showToast({ message: 'دسترسی به مکان خاموش است', icon: 'alert', action: { label: 'تنظیمات', onClick: () => locations?.openSettings() } })
  }
  const locate = () => {
    track('map_locate_tap')
    locations?.init(() => {
      if (!locations.isLocationAvailable || (locations.isAccessRequested && !locations.isAccessGranted)) return denied()
      locations.getLocation((data) => {
        if (!data) return denied()
        const point = { lat: data.latitude, lng: data.longitude }
        track('location_granted')
        setMe(point)
        canvas.current?.flyTo(point)
      })
    })
  }

  const grab = (start: number, end: number) => {
    const at = SHEET.indexOf(sheet)
    const distance = end - start
    const next = Math.abs(distance) < DRAG ? (sheet === 'peek' ? 'half' : 'peek') : SHEET[Math.min(SHEET.length - 1, Math.max(0, at + (distance < 0 ? 1 : -1)))]
    track('map_sheet', { size: next })
    setSheet(next)
  }
  const dragFrom = useRef(0)

  let sheetBody: React.ReactNode
  if (feed.loading) {
    sheetBody = (
      <>
        <Skeleton className="h-5 w-32 flex-none" />
        <div className={cn('grid grid-cols-2 gap-3 overflow-hidden md:grid-cols-1', sheet === 'peek' && 'hidden md:grid')}>
          <CardSkeleton />
          <CardSkeleton />
        </div>
      </>
    )
  } else if (feed.failed) {
    sheetBody = (
      <StateBox icon={CircleAlert} title="آگهی‌ها بارگذاری نشد" testId="map-feed-failed">
        <Button variant="outline" onClick={() => { track('retry_tap', { screen: 'map_feed' }); void feed.refresh() }}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      </StateBox>
    )
  } else if (emptyCity) {
    sheetBody = (
      <>
        <b className="flex-none text-[15px] font-extrabold">{fa(0)} آگهی</b>
        <StateBox icon={House} title={`هنوز آگهی‌ای در ${city.name_fa} نیست`} testId="map-city-empty">
          <Button variant="outline" onClick={() => setCityOpen(true)}>
            شهر دیگر
          </Button>
        </StateBox>
      </>
    )
  } else if (emptyFilters) {
    sheetBody = (
      <>
        <b className="flex-none text-[15px] font-extrabold">{fa(0)} آگهی</b>
        <StateBox icon={SearchX} title="آگهی‌ای با این فیلترها نیست" testId="map-filter-empty">
          <Button variant="outline" onClick={clearAll}>
            پاک کردن همه
          </Button>
        </StateBox>
      </>
    )
  } else if (emptyRegion) {
    sheetBody = (
      <>
        <b className="flex-none text-[15px] font-extrabold">{fa(0)} آگهی</b>
        <StateBox icon={MapPinOff} title="در این ناحیه آگهی‌ای نیست" testId="map-empty">
          <Button variant="outline" onClick={zoomOutOfEmpty}>
            کوچک‌نمایی
          </Button>
        </StateBox>
      </>
    )
  } else {
    sheetBody = (
      <>
        <div className="flex flex-none flex-wrap items-center gap-2">
          <b className="text-[15px] font-extrabold" data-testid="map-count">
            {title}
          </b>
          {selected !== null && !wholeCity ? <span className="text-[13px] text-muted-foreground">{points.get(selected)?.name}</span> : null}
        </div>
        {showOffMap ? (
          <Alert className="flex-none" data-testid="map-off-map">
            <MapPinOff />
            <AlertDescription className="flex w-full items-center justify-between gap-2">
              <span className="text-foreground">{fa(offMapCount)} آگهی بدون محله</span>
              <Button variant="outline" size="sm" onClick={() => { track('map_to_list', { from: 'off_map' }); toList() }}>
                دیدن در فهرست
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        <div className={cn('min-h-0 flex-1 overflow-y-auto pb-2', sheet === 'peek' && 'hidden md:block')} data-testid="map-cards">
          <div className="grid auto-rows-max grid-cols-2 gap-3 md:grid-cols-1">
            {visible.map((card) => (
              <div key={card.id} className={cn('relative grid min-w-0', selected === card.area_id && 'rounded-[14px] ring-2 ring-primary')}>
                <ListingCard card={card} closed={Boolean(overrides[card.id]?.closed)} from="map" />
              </div>
            ))}
          </div>
          {listed.length > shown ? (
            <div className="flex justify-center pt-2">
              <Button variant="outline" size="sm" onClick={() => { track('map_more', { shown }); setShown((value) => value + PAGE) }}>
                بیشتر
              </Button>
            </div>
          ) : null}
        </div>
      </>
    )
  }

  const chipOverMap = zoomedOut ? (
    <Button variant="outline" size="sm" className="bg-background shadow-md" data-testid="map-zoom-in-chip" onClick={zoomBackIn}>
      <ZoomIn />
      بزرگ‌نمایی کنید
    </Button>
  ) : moved && ready && !mapGone ? (
    <Button variant="outline" size="sm" className="bg-background shadow-md" data-testid="map-search-here" onClick={searchHere}>
      <RefreshCw />
      جستجو در این ناحیه
    </Button>
  ) : null

  return (
    <div className="flex h-full flex-col">
      <MainNav place="top" />
      <div className="flex flex-none flex-col gap-2 px-4 pt-2 pb-2 md:gap-3 md:px-6 md:pt-3 md:pb-3">
        {online && !feed.cacheOnly ? null : <OfflineBar onRetry={retry} />}
        <div className="flex items-center gap-2">
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
              <InputGroupInput aria-label="جستجو" value={view.q} onChange={(event) => update((next) => (event.target.value ? next.set('q', event.target.value) : next.delete('q')))} onBlur={(event) => { if (event.target.value) track('search_submit', { query_length: event.target.value.length, from: 'map_box' }) }} />
            </InputGroup>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2" data-testid="filter-row">
          {/* F09 rules: the alert chip comes first on the map, and saves for the whole city (F15#9). */}
          {alertEntry && view.kind === 'offer' ? (
            <Suspense fallback={null}>
              <AlertEntry view={view} city={city} count={cards.length} fromMap />
            </Suspense>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => setFiltersOpen(true)} data-testid="filter-button">
            <SlidersHorizontal />
            فیلتر
            {chips.length ? <Badge>{fa(chips.length)}</Badge> : null}
          </Button>
          {chips.map((chip) => (
            <Button key={chip.id} variant="secondary" size="sm" data-testid="filter-chip" onClick={() => { track('filter_chip_removed', { chip: chip.id.split('-')[0], from: 'map' }); applyFilters(chip.clear(view)) }}>
              {chip.label}
              <X />
            </Button>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="relative min-h-0 min-w-0 flex-1 bg-secondary" data-testid="map-area">
          {mapGone ? (
            <div className="flex h-full items-center justify-center p-4">
              <StateBox icon={CircleAlert} title="نقشه بارگیری نشد" testId="map-failed">
                <Button variant="outline" onClick={() => { track('retry_tap', { screen: 'map' }); retry() }}>
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </StateBox>
            </div>
          ) : (
            <>
              <MapCanvas
                key={`${attempt}-${city.slug}`}
                ref={canvas}
                styleUrl={styleUrl}
                pins={zoomedOut ? [] : pins}
                points={frame}
                me={me}
                onReady={() => setReady(true)}
                onFail={fail}
                onMoved={() => setMoved(true)}
                onZoom={setZoom}
                onPin={pick}
                onBackground={() => setSelected(null)}
              />
              {!ready ? (
                <div className="absolute inset-0 flex flex-col gap-2 p-4" data-testid="map-loading">
                  <Skeleton className="min-h-0 flex-1" />
                  {slow ? <SlowNote /> : null}
                </div>
              ) : (
                <div className="absolute top-3 left-3 z-[1] flex gap-1 md:flex-col">
                  <Button variant="outline" size="icon" aria-label="بزرگ‌نمایی" className="bg-background shadow-sm" onClick={() => canvas.current?.zoomIn()}>
                    <Plus />
                  </Button>
                  <Button variant="outline" size="icon" aria-label="کوچک‌نمایی" className="bg-background shadow-sm" onClick={() => canvas.current?.zoomOut()}>
                    <Minus />
                  </Button>
                  {locations ? (
                    <Button variant="outline" size="icon" aria-label="مکان من" className="bg-background shadow-sm" data-testid="map-locate" onClick={locate}>
                      <LocateFixed />
                    </Button>
                  ) : null}
                </div>
              )}
              {chipOverMap ? <div className="absolute top-3 right-3 z-[1] whitespace-nowrap md:right-auto md:left-1/2 md:-translate-x-1/2">{chipOverMap}</div> : null}
            </>
          )}
          {selected === null ? (
            <div className="absolute bottom-7 left-3 z-[1] md:hidden">
              <Button className="rounded-full shadow-lg" onClick={() => { track('map_to_list', { from: 'button' }); toList() }} data-testid="map-to-list">
                <List />
                فهرست
              </Button>
            </div>
          ) : null}
        </div>

        <div
          className={cn(
            'relative z-[2] -mt-4 flex flex-none flex-col gap-2 rounded-t-2xl bg-background px-4 pt-2 pb-2 shadow-[0_-1px_0_var(--border)]',
            SHEET_HEIGHT[sheet],
            'md:order-first md:mt-0 md:h-auto md:w-[340px] md:gap-3 md:rounded-none md:border-e md:px-4 md:pt-3 md:shadow-none',
          )}
          data-testid="map-sheet"
          data-size={sheet}
        >
          <button
            type="button"
            aria-label="اندازه‌ی برگه"
            className="flex h-5 w-full flex-none touch-none items-center justify-center md:hidden"
            data-testid="map-sheet-handle"
            onPointerDown={(event) => {
              dragFrom.current = event.clientY
              event.currentTarget.setPointerCapture(event.pointerId)
            }}
            onPointerUp={(event) => grab(dragFrom.current, event.clientY)}
          >
            <i className="h-1 w-10 rounded bg-input" />
          </button>
          {sheetBody}
        </div>
      </div>
      <MainNav place="bottom" />
      <FilterSheet open={filtersOpen} focus={null} onClose={() => setFiltersOpen(false)} cards={feed.cards} view={view} country={country} region={placeRegion} partial={feed.hasMore} onApply={applyFilters} />
      <CitySheet open={cityOpen} current={city} onClose={() => setCityOpen(false)} onPick={pickCity} />
    </div>
  )
}

// The route's chunk. The list's own rule for which city: a link's, else the saved one.
export function Component() {
  const [params] = useSearchParams()
  const location = useLocation()
  const saved = useSavedCity()
  const cities = useCities()
  const styleUrl = getMeta()?.map_style_url
  const linked = params.get('city')
  const known = linked && cities.data ? findCity(cities.data, linked) : null
  const city = known ? toSaved(known.country, known.row) : !linked ? saved : null
  if (linked && cities.isPending) return <div className="h-full" data-testid="checking" />
  // No map here, or no city yet: the list knows what to do.
  if (!styleUrl || !city) return <Navigate to={{ pathname: '/', search: location.search }} replace />
  return <MapScreen key={city.slug} city={city} styleUrl={styleUrl} />
}
