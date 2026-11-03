import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BedDouble, ChevronDown, CircleAlert, Clock, House, Info, MapPin, Pencil, RefreshCw, Ruler, Search, SearchX, SlidersHorizontal, Wallet, X, type LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import type { AreaSearch, Card, SearchParse } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { saveFailedToast, showToast } from '@/components/app-toast'
import { Flag } from '@/components/flag'
import { CardSkeleton, ListingCard, WantedCard } from '@/components/listing-card'
import { MainNav } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Skeleton } from '@/components/ui/skeleton'
import { publicFetch, unwrap } from '@/lib/api'
import { useBack } from '@/lib/back'
import { findCity, saveCity, toSaved, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { countryName, fa } from '@/lib/format'
import { applyView, budgetLabel, countMatches, readView, typeTiles, waysOut } from '@/lib/listing-view'
import { track } from '@/lib/report'
import { clearRecents, fitsSize, fromSpec, RECENT_KEY, restoreRecents, saidChips, textSearchOn, toListParams, useParse, usePreview, useRecents, type SearchView } from '@/lib/text-search'
import { useCities } from '@/lib/use-cities'
import { cn } from '@/lib/utils'
import { OfflineBar, useOnline } from './global-states'

const PLACEHOLDERS = ['مثلاً: ۱ خوابه نزدیک مترو', 'مثلاً: اتاق مبله تا ۱٬۰۰۰', 'مثلاً: نورث یورک از دسامبر']
const SUGGESTIONS = ['۱ خوابه تا ۲٬۰۰۰', 'اتاق مبله', 'نزدیک مترو', '۲ خوابه ۸۰ متر']
const PLACEHOLDER_MS = 3_000
const TYPING_MS = 300
const PAGE = 30

// F08 opens from the search box of the list. Without text it is the box (#1, #2, #11), with text the results.
export function TextSearchScreen() {
  const [params] = useSearchParams()
  const city = useSavedCity()
  if (!textSearchOn()) return <Navigate to="/" replace />
  const text = params.get('text')?.trim() ?? ''
  if (!text) return <Compose city={city} initial={params.get('edit') ?? ''} />
  return <Results key={`${text}|${city?.slug ?? ''}`} text={text} city={city} />
}

function Row({ icon: Icon, title, detail, value, onClick, testId }: { icon: LucideIcon; title: string; detail?: string; value?: string; onClick: () => void; testId?: string }) {
  return (
    <button type="button" className="flex min-h-12 w-full items-center gap-3 rounded-md px-1 py-2 text-start hover:bg-accent" onClick={onClick} data-testid={testId}>
      <Icon className="size-5 flex-none text-muted-foreground" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold">{title}</span>
        {detail ? <span className="block truncate text-xs text-muted-foreground">{detail}</span> : null}
      </span>
      {value ? <span className="flex-none text-xs text-muted-foreground">{value}</span> : null}
    </button>
  )
}

function Section({ title, action, children, testId }: { title: string; action?: React.ReactNode; children: React.ReactNode; testId?: string }) {
  return (
    <section className="flex flex-col gap-2" data-testid={testId}>
      <div className="flex min-h-8 items-center justify-between gap-2">
        <h3 className="m-0 text-[13px] font-bold text-muted-foreground">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

function FilterEntry({ onClick }: { onClick: () => void }) {
  return <Row icon={SlidersHorizontal} title="جستجو با فیلتر" onClick={onClick} testId="search-with-filter" />
}

function useTiles(city: SavedCity | null, cards: Card[]) {
  const country = cards[0]?.country ?? city?.country ?? 'CA'
  const region = cards[0]?.region ?? ''
  return { country, tiles: useMemo(() => typeTiles(country, region), [country, region]) }
}

function Compose({ city, initial }: { city: SavedCity | null; initial: string }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const online = useOnline()
  const [text, setText] = useState(initial)
  const [typed, setTyped] = useState(initial.trim())
  const [hint, setHint] = useState(0)
  const [hideRecents, setHideRecents] = useState(false)
  const recents = useRecents()
  const feed = useFeed(city?.slug ?? '', city?.country ?? '')
  const preview = usePreview(online ? typed : '', city?.country ?? '', city?.city ?? '')
  const areaWord = typed.split(/\s+/).filter((word) => /\p{L}{2}/u.test(word)).pop() ?? ''
  const areas = useQuery({
    queryKey: ['search-areas', areaWord, city?.slug],
    enabled: online && Boolean(areaWord),
    retry: false,
    staleTime: 300_000,
    queryFn: ({ signal }) => unwrap(publicFetch<AreaSearch>(`/api/v1/public/areas/search/?${new URLSearchParams({ q: areaWord, country: city?.country ?? '', city: city?.city ?? '' })}`, { signal })),
  })
  const { country, tiles } = useTiles(city, feed.cards)

  useEffect(() => {
    const timer = setInterval(() => setHint((value) => (value + 1) % PLACEHOLDERS.length), PLACEHOLDER_MS)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    const timer = setTimeout(() => setTyped(text.trim()), TYPING_MS)
    return () => clearTimeout(timer)
  }, [text])

  const leave = () => navigate('/', { replace: true })
  useBack(leave)
  const open = (value: string) => {
    const query = value.trim()
    if (query) navigate(`/search?${new URLSearchParams({ text: query })}`)
  }

  // F08#11: gone at once, and the undo brings back the same set.
  const clear = async () => {
    setHideRecents(true)
    track('search_recents_cleared')
    const result = await clearRecents()
    if (!result.ok) {
      setHideRecents(false)
      saveFailedToast(() => void clear())
      return
    }
    client.setQueryData(RECENT_KEY, { results: [] })
    setHideRecents(false)
    showToast({
      message: 'جستجوهای اخیر پاک شد',
      icon: 'check',
      action: {
        label: 'برگردان',
        onClick: () =>
          void restoreRecents(result.data.cleared_at).then((restored) => {
            if (restored.ok) client.setQueryData(RECENT_KEY, restored.data)
            else saveFailedToast(() => void clear())
          }),
      },
    })
  }

  const shown = hideRecents ? [] : (recents.data?.results ?? [])
  const parsed = preview.data
  const said = parsed ? fromSpec(parsed.spec, parsed.area_labels) : null
  const count = (search: SearchView) => (feed.hasMore ? undefined : `${fa(countMatches(feed.cards.filter((card) => fitsSize(card, search.size)), search.view))} آگهی`)
  const base = readView(new URLSearchParams())
  const budget = said && budgetLabel(said.view)
  const sized = said && said.size.min != null
  const rooms = said?.view.types.length ? { ...said, size: { min: null, max: null }, view: { ...base, types: said.view.types } } : null
  const listings = said ? applyView(feed.cards, said.view).filter((card) => fitsSize(card, said.size)).slice(0, 2) : []
  const areaHits = (areas.data?.here ?? []).slice(0, 3)
  const typing = text.trim().length > 0

  return (
    <div className="flex h-full flex-col" data-testid="search-compose">
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pt-2 pb-4 md:px-6 md:pt-3">
        {online ? null : <OfflineBar onRetry={() => void feed.refresh()} />}
        <form
          className="flex flex-none items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            open(text)
          }}
        >
          <Button type="button" variant="ghost" size="icon" aria-label="بستن جستجو" onClick={leave}>
            <X className="size-5" />
          </Button>
          <div className="min-w-0 flex-1">
            <InputGroup>
              <InputGroupAddon>
                <Search />
              </InputGroupAddon>
              <InputGroupInput autoFocus aria-label="جستجو" dir="auto" enterKeyHint="search" placeholder={PLACEHOLDERS[hint]} value={text} onChange={(event) => setText(event.target.value)} />
            </InputGroup>
          </div>
        </form>
        {typing ? (
          <div className="flex flex-col gap-2" data-testid="search-live">
            <Row icon={Search} title={said ? saidChips(said, tiles, country).map((chip) => chip.label).join(' · ') || text : text} onClick={() => open(text)} testId="search-live-echo" />
            {areaHits.length ? (
              <Section title="محله، تقاطع، ایستگاه" testId="search-live-areas">
                {areaHits.map((hit) => (
                  <Row key={hit.id} icon={MapPin} title={hit.name_fa} detail={hit.matched} onClick={() => open(hit.name_fa)} />
                ))}
              </Section>
            ) : null}
            {said && (budget || sized || rooms) ? (
              <Section title="قیمت و اندازه" testId="search-live-counts">
                {budget ? <Row icon={Wallet} title={budget} value={count({ ...said, size: { min: null, max: null }, view: { ...base, min: said.view.min, max: said.view.max } })} onClick={() => open(text)} /> : null}
                {sized ? <Row icon={Ruler} title={`${fa(said.size.min ?? 0)} تا ${fa(said.size.max ?? 0)} متر`} value={count({ ...said, view: base })} onClick={() => open(text)} /> : null}
                {rooms ? <Row icon={BedDouble} title={saidChips(rooms, tiles, country)[0]?.label ?? text} value={count(rooms)} onClick={() => open(text)} /> : null}
              </Section>
            ) : null}
            {listings.length ? (
              <Section title="آگهی‌ها" testId="search-live-listings">
                {listings.map((card) => (
                  <Row key={card.id} icon={House} title={card.area} detail={card.price_monthly != null ? fa(card.price_monthly) : undefined} onClick={() => navigate(`/listing/${card.id}`)} />
                ))}
              </Section>
            ) : null}
          </div>
        ) : (
          <>
            <Section title="پیشنهادها" testId="search-suggestions">
              <div className="flex flex-wrap gap-2">
                {SUGGESTIONS.map((item) => (
                  <Button key={item} variant="secondary" size="sm" onClick={() => open(item)}>
                    {item}
                  </Button>
                ))}
              </div>
            </Section>
            {shown.length ? (
              <Section
                title="جستجوهای اخیر"
                testId="search-recents"
                action={
                  <Button variant="ghost" size="sm" onClick={() => void clear()}>
                    پاک کردن
                  </Button>
                }
              >
                {shown.map((row) => (
                  <Row key={row.text} icon={Clock} title={saidChips(fromSpec(row.spec, row.area_labels), tiles, country).map((chip) => chip.label).join(' · ') || row.text} onClick={() => open(row.text)} />
                ))}
              </Section>
            ) : null}
          </>
        )}
        {/* Open with no connection too (F08 rules). */}
        <FilterEntry onClick={leave} />
      </div>
    </div>
  )
}

function Note({ icon: Icon, children, action, testId }: { icon: LucideIcon; children: React.ReactNode; action: React.ReactNode; testId: string }) {
  return (
    <Alert className="flex-none" data-testid={testId}>
      <Icon />
      <AlertDescription className="flex w-full flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 flex-[1_1_260px] text-foreground">{children}</span>
        {action}
      </AlertDescription>
    </Alert>
  )
}

function CityPicker({ onPick }: { onPick: (next: SavedCity) => void }) {
  const cities = useCities()
  const navigate = useNavigate()
  return (
    <AppSheet open onClose={() => navigate(-1)} label="کدام شهر؟" title="کدام شهر؟" testId="search-city-sheet">
      <div className="flex flex-col gap-4">
        {cities.data?.countries.map((country) => (
          <section key={country.code} className="flex flex-col gap-2">
            <h3 className="m-0 flex items-center gap-2 text-sm font-bold">
              <Flag code={country.code} />
              {countryName(country.code)}
            </h3>
            <div className="flex flex-wrap gap-2">
              {country.cities.map((row) => (
                <Button key={row.slug} variant="outline" size="sm" onClick={() => onPick(toSaved(country, row))}>
                  {row.name_fa}
                </Button>
              ))}
            </div>
          </section>
        )) ?? (
          <div className="flex gap-2">
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-8 w-20" />
          </div>
        )}
      </div>
    </AppSheet>
  )
}

function Results({ text, city }: { text: string; city: SavedCity | null }) {
  const navigate = useNavigate()
  const online = useOnline()
  const parse = useParse(online ? text : '', city?.country ?? '', city?.city ?? '')
  const feed = useFeed(city?.slug ?? '', city?.country ?? '')
  const answer = parse.data
  // No answer at all (down, offline, rate limit): the plain text search the box did before this flow.
  const failed = parse.isError || !online
  const [search, setSearch] = useState<SearchView | null>(null)
  const [unknownDone, setUnknownDone] = useState(false)
  useEffect(() => {
    if (answer) {
      setSearch(fromSpec(answer.spec, answer.area_labels))
      track('search_answer', { status: answer.status, source: answer.source, cached: answer.cached })
    } else if (failed) {
      setSearch(fromSpec(fromSpecText(text), []))
      track('search_failed')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answer, failed])

  // Opened from a link there is nothing behind this page, so back goes to the box.
  useBack(() => (window.history.state?.idx > 0 ? navigate(-1) : navigate('/search', { replace: true })))
  const pending = !search
  const { country, tiles } = useTiles(city, feed.cards)
  const status = answer?.status
  const bare = status === 'capped' || status === 'not_understood'
  const chips = search && !bare && !failed ? saidChips(search, tiles, country) : []
  const toFilters = () => navigate(`/?${search ? toListParams(search) : ''}`)

  const remove = (chip: (typeof chips)[number]) => {
    if (!search) return
    const before = search
    setSearch(chip.clear(search))
    track('search_chip_removed', { chip: chip.id })
    showToast({ message: `${chip.label} حذف شد`, icon: 'check', action: { label: 'برگردان', onClick: () => setSearch(before) } })
  }

  const sized = useMemo(() => (search ? feed.cards.filter((card) => fitsSize(card, search.size)) : []), [feed.cards, search])
  const cards = useMemo(() => (search ? applyView(sized, search.view) : []), [sized, search])
  const [shown, setShown] = useState(PAGE)

  useEffect(() => {
    // Later pages of the fallback feed can hold the match.
    if (search && feed.hasData && cards.length === 0 && feed.hasMore && !feed.loadingMore && !feed.moreFailed) void feed.loadMore()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, feed.hasData, cards.length, feed.hasMore, feed.loadingMore, feed.moreFailed])

  const header = (
    <div className="flex flex-none items-center gap-2">
      <Button variant="outline" data-testid="search-city" onClick={() => navigate('/?change=1')}>
        <MapPin />
        {city?.name_fa ?? 'انتخاب شهر'}
        <ChevronDown />
      </Button>
      <div className="min-w-0 flex-1">
        <InputGroup>
          <InputGroupAddon>
            <Search />
          </InputGroupAddon>
          <InputGroupInput aria-label="جستجو" dir="auto" readOnly value={text} data-testid="search-box" onClick={() => navigate(`/search?${new URLSearchParams({ edit: text })}`)} />
        </InputGroup>
      </div>
    </div>
  )

  // Always there, even while the text is being read (F08 rules).
  const filterRow = (
    <div className="flex flex-none flex-wrap items-center gap-2" data-testid="filter-row">
      <Button variant="outline" size="sm" onClick={toFilters} data-testid="filter-button">
        <SlidersHorizontal />
        فیلتر
      </Button>
      {chips.length === 0 ? (
        <>
          <Button variant="outline" size="sm" onClick={toFilters}>
            نوع خانه
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={toFilters}>
            بودجه
            <ChevronDown />
          </Button>
          <Button variant="outline" size="sm" onClick={toFilters}>
            محله
            <ChevronDown />
          </Button>
        </>
      ) : null}
    </div>
  )

  const simple = status === 'simple'
  const said = pending ? (
    <div className="flex min-h-8 flex-none flex-wrap items-center gap-2" data-testid="search-parsing">
      <b className="text-[13px] text-muted-foreground">فهمیدم:</b>
      <Skeleton className="h-6 w-16" />
      <Skeleton className="h-6 w-14" />
      <Skeleton className="h-6 w-20" />
    </div>
  ) : chips.length ? (
    <div className="flex flex-none flex-wrap items-center gap-2" data-testid="search-said">
      <b className="text-[13px] text-muted-foreground">{simple ? 'جستجوی ساده:' : 'فهمیدم:'}</b>
      {simple ? null : (
        <Button variant="ghost" size="icon" aria-label="اصلاح" onClick={() => navigate(`/search?${new URLSearchParams({ edit: text })}`)}>
          <Pencil />
        </Button>
      )}
      {chips.map((chip) => (
        <Button key={chip.id} variant="secondary" size="sm" data-testid="search-chip" onClick={() => remove(chip)}>
          {chip.label}
          <X />
        </Button>
      ))}
    </div>
  ) : null

  const answerNote = answer ? <AnswerNote answer={answer} search={search} done={unknownDone} setDone={setUnknownDone} setSearch={setSearch} toFilters={toFilters} /> : null
  const failedNote = failed ? (
    <Note
      icon={CircleAlert}
      testId="search-failed"
      action={
        <Button variant="outline" size="sm" disabled={!online} onClick={() => void parse.refetch()}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      }
    >
      جستجوی متنی الان در دسترس نیست. فقط متن آگهی‌ها گشته شد.
    </Note>
  ) : null

  const ways = search && cards.length === 0 && !feed.hasMore ? widen(search, sized, feed.cards) : []
  const countBar = (
    <div className="flex flex-none items-center justify-between gap-2 text-sm" data-testid="count-bar">
      <b className="font-extrabold whitespace-nowrap">
        {fa(cards.length)}
        {feed.hasMore ? '+' : ''} آگهی
      </b>
    </div>
  )

  let body: React.ReactNode
  if (pending || feed.loading || status === 'no_city') {
    body = (
      <div className="grid min-h-0 flex-1 auto-rows-max grid-cols-1 gap-4 overflow-hidden pt-1 md:grid-cols-3">
        <CardSkeleton />
        <CardSkeleton />
        <CardSkeleton />
      </div>
    )
  } else if (feed.failed) {
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title="آگهی‌ها بارگذاری نشد" testId="list-failed">
          <Button className="w-full" onClick={() => void feed.refresh()}>
            <RefreshCw />
            تلاش مجدد
          </Button>
        </StateBox>
      </div>
    )
  } else if (cards.length === 0 && (feed.hasMore || feed.loadingMore)) {
    body = (
      <div className="grid min-h-0 flex-1 auto-rows-max grid-cols-1 gap-4 overflow-hidden pt-1 md:grid-cols-3" data-testid="seeking-more">
        <CardSkeleton />
      </div>
    )
  } else if (cards.length === 0) {
    // F08#10
    body = (
      <>
        {countBar}
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateBox icon={SearchX} title="آگهی‌ای با این مشخصات نیست" testId="search-empty">
            <div className="flex flex-wrap items-center justify-center gap-2" data-testid="ways-out">
              {ways.map((way) => (
                <Button key={way.label} variant="outline" size="sm" onClick={() => setSearch(way.search)}>
                  {way.label}
                </Button>
              ))}
              <Button variant="outline" size="sm" onClick={toFilters}>
                <SlidersHorizontal />
                جستجو با فیلتر
              </Button>
            </div>
          </StateBox>
        </div>
      </>
    )
  } else {
    body = (
      <>
        {countBar}
        <div
          className="min-h-0 flex-1 overflow-y-auto pb-4"
          data-testid="list-scroll"
          onScroll={(event) => {
            const box = event.currentTarget
            if (box.scrollTop + box.clientHeight < box.scrollHeight - 400) return
            if (shown < cards.length) setShown((value) => value + PAGE)
            else if (feed.hasMore && !feed.loadingMore && !feed.moreFailed) void feed.loadMore()
          }}
        >
          <div className="grid auto-rows-max grid-cols-1 gap-4 pt-1 md:grid-cols-3">
            {cards.slice(0, shown).map((card) => (search?.view.kind === 'wanted' ? <WantedCard key={card.id} card={card} /> : <ListingCard key={card.id} card={card} />))}
          </div>
        </div>
      </>
    )
  }

  return (
    <div className={cn('flex h-full flex-col')} data-testid="search-results" data-status={status ?? (failed ? 'failed' : 'parsing')}>
      <MainNav place="top" />
      <div className="flex min-h-0 flex-1 flex-col gap-2 px-4 pt-2 md:gap-3 md:px-6 md:pt-3">
        {online ? null : <OfflineBar onRetry={() => void feed.refresh()} />}
        {header}
        {filterRow}
        {said}
        {failedNote}
        {answerNote}
        {body}
      </div>
      <MainNav place="bottom" />
      {status === 'no_city' ? <CityPicker onPick={(next) => void saveCity(next)} /> : null}
    </div>
  )
}

function fromSpecText(text: string) {
  return { ...EMPTY_SPEC, q: text }
}

const EMPTY_SPEC = { kind: 'offer', types: [], min: null, max: null, size_min: null, size_max: null, area_ids: [], term: '', furnished: false, bills: false, owner: false, warm: false, reg: false, nowbs: false, gender: '', move: '', by: '', q: '' }

// F08#10: what one change would bring back, by real count. The list's ways, plus area and size, which only a text search sets.
function widen(search: SearchView, sized: Card[], all: Card[]) {
  const { view } = search
  const ways: Array<{ label: string; search: SearchView }> = []
  if (view.area_ids.length) {
    const next = { ...view, area_ids: [] }
    const count = countMatches(sized, next)
    if (count > 0) ways.push({ label: `بدون محله: ${fa(count)}`, search: { ...search, view: next } })
  }
  if (search.size.min != null || search.size.max != null) {
    const count = countMatches(all, view)
    if (count > 0) ways.push({ label: `بدون متراژ: ${fa(count)}`, search: { ...search, size: { min: null, max: null } } })
  }
  const top = Math.max(0, ...all.map((card) => card.price_monthly ?? 0))
  for (const way of waysOut(sized, view, top)) if (ways.length < 3) ways.push({ label: way.label, search: { ...search, view: way.view } })
  return ways.slice(0, 3)
}

function AnswerNote({ answer, search, done, setDone, setSearch, toFilters }: { answer: SearchParse; search: SearchView | null; done: boolean; setDone: (done: boolean) => void; setSearch: (next: SearchView) => void; toFilters: () => void }) {
  const cities = useCities()
  const filterButton = (label: string, primary: boolean) => (
    <Button variant={primary ? 'default' : 'outline'} size="sm" onClick={toFilters}>
      <SlidersHorizontal />
      {label}
    </Button>
  )
  switch (answer.status) {
    case 'unknown_area': {
      if (done || !answer.unknown_area || !search) return null
      const { text, suggestions } = answer.unknown_area
      return (
        <section className="flex flex-none flex-col gap-2" data-testid="search-unknown-area">
          <h3 className="m-0 text-[13px] font-bold text-muted-foreground">«{text}» را پیدا نکردم. منظورتان؟</h3>
          <div className="flex flex-wrap gap-2">
            {suggestions.map((row) => (
              <Button
                key={row.id}
                variant="outline"
                size="sm"
                onClick={() => {
                  setSearch({ ...search, view: { ...search.view, area_ids: [...search.view.area_ids, row.id] }, labels: [...search.labels, row] })
                  setDone(true)
                }}
              >
                {row.label}
              </Button>
            ))}
            <Button variant="outline" size="sm" onClick={() => setDone(true)}>
              بدون محله
            </Button>
          </div>
        </section>
      )
    }
    case 'simple':
      return (
        <Note icon={Info} testId="search-simple" action={filterButton('افزودن با فیلتر', false)}>
          {answer.ignored.map((word) => `«${word}»`).join(' و ')} در این جستجو حساب نشد.
        </Note>
      )
    case 'capped':
      return (
        <Note icon={CircleAlert} testId="search-capped" action={filterButton('جستجو با فیلتر', true)}>
          جستجوی متنی امروز به سقف رسید. فردا دوباره فعال می‌شود.
        </Note>
      )
    case 'not_understood':
      return (
        <Note icon={CircleAlert} testId="search-not-understood" action={filterButton('جستجو با فیلتر', true)}>
          متن را نفهمیدم. با فیلتر جستجو کنید.
        </Note>
      )
    case 'other_city': {
      const other = answer.other_city
      if (!other) return null
      const found = cities.data ? findCity(cities.data, other.slug) : null
      return (
        <Note
          icon={MapPin}
          testId="search-other-city"
          action={
            <Button
              size="sm"
              disabled={!found}
              onClick={() => {
                if (found) void saveCity(toSaved(found.country, found.row))
              }}
            >
              <MapPin />
              تغییر شهر
            </Button>
          }
        >
          {other.name_fa} را می‌گویید؟
        </Note>
      )
    }
    default:
      return null
  }
}
