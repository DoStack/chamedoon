import { useQuery } from '@tanstack/react-query'
import { useDragScroll } from '@/lib/use-drag-scroll'
import { CircleAlert, House, List, LocateFixed, MapPinOff, Minus, Plus, RefreshCw, SearchX, ZoomIn } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useNavigationType, useSearchParams } from 'react-router-dom'
import type { Card as CardData, CityAreas } from '@/api/types'
import { BookmarkButton } from '@/components/bookmark'
import { showToast } from '@/components/app-toast'
import { CompactBar } from '@/components/compact-bar'
import { ON_PHOTO, Photo, Price } from '@/components/listing-card'
import { MainNav, rememberListSearch } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { getMeta } from '@/lib/access'
import { publicFetch, unwrap } from '@/lib/api'
import { useBack } from '@/lib/back'
import { findCity, saveCity, toSaved, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { FLAGS } from '@/lib/flags'
import { cardTitle, cityLabel, fa, placeLine } from '@/lib/format'
import { appliedChips, applyView, clearFilters, FILTER_KEYS, readView, typeTiles, writeView, type View } from '@/lib/listing-view'
import { t } from '@/lib/locale'
import { useCardOverrides } from '@/lib/overrides'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { useCities } from '@/lib/use-cities'
import { BARE_SCROLL } from '@/lib/use-strip'
import { CitySheet } from './city-sheet'
import { FilterSheet } from './filter-sheet'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'
import { readLastVisit } from './list'
import { MapCanvas, type MapHandle, type MapPin as Pin, type MapPoint } from './map-canvas'

const SLOW_MS = 3000
// Further out than this the pins of a metro lie on top of each other (F09#7).
const MIN_PIN_ZOOM = 7
const MAX_CARDS = 600
const MAP_IDS = { city: 'city-button', search: 'map-search', filter: 'filter-button' }

function useAreas(city: SavedCity) {
  return useQuery({
    queryKey: ['areas', city.country, city.slug],
    retry: false,
    staleTime: 300_000,
    queryFn: () => unwrap(publicFetch<CityAreas>(`/api/v1/public/areas/?${new URLSearchParams({ country: city.country, city: city.slug })}`, { timeoutMs: 8_000 })),
  })
}

// The pin left open, so back from its listing lands on the same card.
let leftPin: { slug: string; area: number } | null = null

// One listing, low enough to leave the map in view: the picture at the start and the words beside it.
function PinCard({ card }: { card: CardData }) {
  return (
    <Card className="relative h-full flex-row gap-0 overflow-hidden py-0 shadow-lg" data-testid="map-card">
      <Link to={`/listing/${card.id}`} className="flex min-w-0 flex-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50" data-testid="listing-card" onClick={() => track('listing_open', { listing_id: card.id, from: 'map' })}>
        <Photo src={card.thumb} count={card.photo_count > 1 ? fa(card.photo_count) : undefined} className="aspect-auto w-24 self-stretch" />
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 p-3">
          <Price card={card} />
          <div className="truncate text-sm font-bold">{cardTitle(card)}</div>
          <div className="truncate text-[13px] text-muted-foreground">{placeLine(card)}</div>
        </div>
      </Link>
      {FLAGS.bookmark ? <BookmarkButton id={card.id} className={`absolute top-1 start-1 ${ON_PHOTO}`} /> : null}
    </Card>
  )
}

function MapScreen({ city, styleUrl }: { city: SavedCity; styleUrl: string }) {
  const dragRef = useDragScroll()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const navigationType = useNavigationType()
  rememberListSearch(location.search)
  const view = useMemo<View>(() => ({ ...readView(params), kind: 'offer' }), [params])
  const feed = useFeed(city.slug, city.country)
  const areas = useAreas(city)
  const online = useOnline()
  const overrides = useCardOverrides()
  const lastVisit = useMemo(() => readLastVisit(city.slug), [city.slug])
  const canvas = useRef<MapHandle>(null)

  const [attempt, setAttempt] = useState(0)
  const [failed, setFailed] = useState(false)
  const [ready, setReady] = useState(false)
  const [zoom, setZoom] = useState<number | null>(null)
  const [selected, setSelected] = useState<number | null>(() => (navigationType === 'POP' && leftPin?.slug === city.slug ? leftPin.area : null))
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [cityOpen, setCityOpen] = useState(false)
  const [me, setMe] = useState<MapPoint | null>(null)

  const openPin = useRef(selected)
  openPin.current = selected
  useEffect(() => () => void (leftPin = openPin.current === null ? null : { slug: city.slug, area: openPin.current }), [city.slug])

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
    const byArea = new Map<number, CardData[]>()
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

  // A pin that the filters took away, once there is an answer to tell by.
  useEffect(() => {
    if (selected !== null && feed.hasData && areas.data && !pins.some((pin) => pin.id === selected)) setSelected(null)
  }, [pins, selected, feed.hasData, areas.data])

  useEffect(() => {
    if (ready) track('map_view', { city: city.slug, pins: pins.length })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, city.slug])

  const slow = useAfter(SLOW_MS, !ready && !mapGone)
  useEffect(() => {
    if (slow) track('map_slow')
  }, [slow])

  const picked = selected !== null && !zoomedOut && !mapGone ? onMap.filter((card) => card.area_id === selected) : []
  const emptyCity = feed.hasData && feed.cards.length === 0 && !feed.hasMore
  const emptyFilters = feed.hasData && !emptyCity && cards.length === 0 && !feed.hasMore
  // The map leaves these out, and only the list has them.
  const showOffMap = !mapGone && !zoomedOut && selected === null && offMapCount > 0 && feed.hasData

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
  }

  const retry = () => {
    setFailed(false)
    setReady(false)
    setZoom(null)
    setAttempt((value) => value + 1)
    if (areas.isError) void areas.refetch()
  }

  // A pin open on the map is that area on the list: the «محله» filter, cleared there like any other. The other filters stay.
  const toList = (from: 'button' | 'off_map' | 'back') => {
    const area = selected !== null ? points.get(selected) : undefined
    track('map_to_list', area ? { from, area: selected } : { from })
    const search = new URLSearchParams(location.search)
    if (area) {
      search.delete('areas')
      search.append('areas', area.name)
    }
    navigate({ pathname: '/', search: search.toString() }, { state: { returning: true } })
  }
  // Back does what «لیست» does.
  useBack(() => toList('back'))

  const pick = (id: number) => {
    track('pin_selected', { area: id })
    setSelected(id === selected ? null : id)
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

  const applyFilters = (next: View) => setParams((previous) => writeView(previous, next), { replace: true })

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

  // The states that used to live in the list under the map, now one card over it.
  let state: React.ReactNode = null
  if (feed.failed) {
    state = (
      <StateBox icon={CircleAlert} title="آگهی‌ها بارگذاری نشد" testId="map-feed-failed">
        <Button variant="outline" onClick={() => { track('retry_tap', { screen: 'map_feed' }); void feed.refresh() }}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      </StateBox>
    )
  } else if (emptyCity) {
    state = (
      <StateBox icon={House} title={`هنوز آگهی‌ای در ${city.name_fa} نیست`} testId="map-city-empty">
        <Button variant="outline" onClick={() => setCityOpen(true)}>
          شهر دیگر
        </Button>
      </StateBox>
    )
  } else if (emptyFilters) {
    state = (
      <StateBox icon={SearchX} title="آگهی‌ای با این فیلترها نیست" testId="map-filter-empty">
        <Button variant="outline" onClick={clearAll}>
          پاک کردن همه
        </Button>
      </StateBox>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <MainNav place="top" />
      <div className="flex flex-none flex-col gap-2 px-4 py-2 md:px-6">
        {online && !feed.cacheOnly ? null : <OfflineBar onRetry={retry} />}
        <CompactBar
          city={cityLabel(city)}
          onCity={() => setCityOpen(true)}
          input={{ value: view.q, onChange: (event) => update((next) => (event.target.value ? next.set('q', event.target.value) : next.delete('q'))), onBlur: (event) => { if (event.target.value) track('search_submit', { query_length: event.target.value.length, from: 'map_box' }) } }}
          filters={chips.length}
          onFilter={() => setFiltersOpen(true)}
          ids={MAP_IDS}
        />
      </div>

      <div className="relative min-h-0 flex-1 bg-secondary" data-testid="map-area">
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
            <MapCanvas key={`${attempt}-${city.slug}`} ref={canvas} styleUrl={styleUrl} pins={zoomedOut ? [] : pins} points={frame} me={me} onReady={() => setReady(true)} onFail={fail} onZoom={setZoom} onPin={pick} onBackground={() => setSelected(null)} />
            {!ready ? (
              <div className="absolute inset-0 flex flex-col gap-2 p-4" data-testid="map-loading">
                <Skeleton className="min-h-0 flex-1" />
                {slow ? <SlowNote /> : null}
              </div>
            ) : (
              <div className="absolute top-3 left-3 z-[1] flex flex-col gap-1">
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
            {zoomedOut ? (
              <div className="absolute top-3 left-1/2 z-[1] -translate-x-1/2 whitespace-nowrap">
                <Button variant="outline" size="sm" className="bg-background shadow-md" data-testid="map-zoom-in-chip" onClick={() => { track('map_zoom_chip'); canvas.current?.fit() }}>
                  <ZoomIn />
                  بزرگ‌نمایی کنید
                </Button>
              </div>
            ) : null}
          </>
        )}

        {/* Lets the map take every touch that is not on one of its children. */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[2] flex flex-col items-center gap-2 p-3 md:px-6">
          <div className="pointer-events-auto flex flex-wrap items-center justify-center gap-2">
            <Button className="rounded-full shadow-lg" data-testid="map-to-list" onClick={() => toList('button')}>
              <List />
              {t('لیست')}
            </Button>
            {showOffMap ? (
              <Button variant="outline" size="sm" className="rounded-full bg-background shadow-md" data-testid="map-off-map" onClick={() => toList('off_map')}>
                <MapPinOff />
                {fa(offMapCount)} آگهی بدون محله
              </Button>
            ) : null}
          </div>
          {picked.length ? (
            // One card. A second peeks in from the side when the area has more, and a swipe brings it.
            <div ref={dragRef} className={`pointer-events-auto flex w-full max-w-md snap-x snap-mandatory gap-2 overflow-x-auto ${BARE_SCROLL}`} data-testid="map-cards">
              {picked.map((card) => (
                <div key={card.id} className={`flex-none snap-center ${picked.length > 1 ? 'w-[calc(100%-1.5rem)]' : 'w-full'}`}>
                  <PinCard card={card} />
                </div>
              ))}
            </div>
          ) : state ? (
            <Card className="pointer-events-auto w-full max-w-md gap-0 py-2 shadow-lg" data-testid="map-state">
              {state}
            </Card>
          ) : null}
        </div>
      </div>
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
