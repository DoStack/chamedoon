import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeftRight, Ban, ChevronDown, ChevronLeft, CircleAlert, Globe, ListPlus, MapPin, Plus, RefreshCw, Route, Search, SearchX } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Item, ItemContent, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Skeleton } from '@/components/ui/skeleton'
import { track } from '@/lib/report'
import { courierFetch, postJson, unwrap } from './api'
import { placeKey } from './format'
import { Section, Segmented } from './parts'
import type { components } from '@/api/courier-schema.gen'

type Locations = components['schemas']['Locations']
type Country = components['schemas']['Country']
type City = components['schemas']['City']

export const LOCATIONS_KEY = ['courier', 'locations']

export function useLocations() {
  return useQuery({ queryKey: LOCATIONS_KEY, queryFn: () => unwrap(courierFetch<Locations>('locations/')), staleTime: 3_600_000 })
}

// `COUNTRY:slug`. Empty is «همه‌جا».
export function placeLabel(key: string, locations?: Locations, empty = 'همه‌جا') {
  if (!key) return empty
  const [country, slug] = key.split(':')
  const row = locations?.countries.find((item) => item.code === country)?.cities.find((item) => item.slug === slug)
  return row?.name_fa ?? (slug ?? '').replace(/-/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export type RouteValue = { from: string; to: string }

// With no list, the three pinned countries still take a typed city (C02.8).
const PINNED: Country[] = [
  { code: 'IR', name_fa: 'ایران', name_en: 'Iran', cities: [] },
  { code: 'CA', name_fa: 'کانادا', name_en: 'Canada', cities: [] },
  { code: 'DE', name_fa: 'آلمان', name_en: 'Germany', cities: [] },
]
const TOP_CITIES = 12
const SEARCH_FROM = 10

function Row({ icon: Icon, title, onClick, disabled, chevron, testId }: { icon: typeof MapPin; title: string; onClick?: () => void; disabled?: boolean; chevron?: boolean; testId?: string }) {
  return (
    <Item asChild>
      <button type="button" disabled={disabled} onClick={onClick} data-testid={testId} className={`w-full text-start${disabled ? ' opacity-50' : ''}`}>
        <ItemMedia variant="icon">
          <Icon />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>
            <bdi>{title}</bdi>
          </ItemTitle>
        </ItemContent>
        {chevron ? <ChevronLeft className="size-4 text-muted-foreground" aria-hidden /> : null}
      </button>
    </Item>
  )
}

function Rows({ children }: { children: React.ReactNode }) {
  return (
    <Card className="!gap-0 !px-4 !py-1">
      <ItemGroup className="!gap-0">{children}</ItemGroup>
    </Card>
  )
}

function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <InputGroup>
      <InputGroupAddon>
        <Search />
      </InputGroupAddon>
      <InputGroupInput placeholder="جستجوی شهر" value={value} onChange={(event) => onChange(event.target.value)} aria-label="جستجوی شهر" data-testid="city-search" />
    </InputGroup>
  )
}

type Props = {
  open: boolean
  side: 'from' | 'to'
  value: RouteValue
  onClose: () => void
  onDone: (value: RouteValue) => void
  allowAny?: boolean // «همه‌جا» is for the list, not for a post.
  frequent?: boolean
}

// C02. Full height on a phone. Back goes up one step, then closes with the route unchanged.
export function RouteSheet({ open, side: startSide, value, onClose, onDone, allowAny = true, frequent = true }: Props) {
  const locations = useLocations()
  const [side, setSide] = useState(startSide)
  const [draft, setDraft] = useState(value)
  const [country, setCountry] = useState<Country | null>(null)
  const [query, setQuery] = useState('')
  const [typing, setTyping] = useState(false)
  const [same, setSame] = useState(false)

  useEffect(() => {
    if (!open) return
    setSide(startSide)
    setDraft(value)
    setCountry(null)
    setQuery('')
    setTyping(false)
    setSame(false)
    track('courier.c02.open', { side: startSide })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const add = useMutation({
    mutationFn: (name: string) => unwrap(postJson<City>('cities/', { country: country!.code, name })),
    onSuccess: (city) => choose(`${country!.code}:${city.slug}`),
    onError: () => track('courier.c02.city_invalid'),
  })

  const countries = locations.data?.countries?.length ? locations.data.countries : PINNED
  const other = side === 'from' ? draft.to : draft.from

  const choose = (key: string) => {
    if (key && key === other) {
      setSame(true)
      track('courier.c02.same_city')
      return
    }
    setSame(false)
    const next = side === 'from' ? { ...draft, from: key } : { ...draft, to: key }
    setDraft(next)
    setCountry(null)
    setQuery('')
    setTyping(false)
    add.reset()
    if (side === 'from' && !next.to) setSide('to')
    else onDone(next)
  }

  const cities = useMemo(() => {
    if (!country) return []
    const needle = query.trim().toLowerCase()
    if (!needle) return country.cities.slice(0, TOP_CITIES)
    return country.cities.filter((city) => city.name_fa.includes(query.trim()) || city.name_en.toLowerCase().includes(needle))
  }, [country, query])

  const back = country ? () => (setCountry(null), setQuery(''), setTyping(false), setSame(false), add.reset()) : onClose
  const name = query.trim()
  const title = country ? `کدام شهر ${country.name_fa}؟` : side === 'to' && draft.from ? 'به کدام کشور؟' : 'مسیر'
  const failed = locations.isError
  return (
    <AppSheet open={open} onClose={onClose} onBack={back} label="مسیر" title={title} testId="route-sheet" full>
      <Segmented
        label="مبدا یا مقصد"
        value={side}
        options={[
          { value: 'from', label: `از ${draft.from ? placeLabel(draft.from, locations.data) : ''}`.trim() },
          { value: 'to', label: `به ${draft.to ? placeLabel(draft.to, locations.data) : ''}`.trim() },
        ]}
        onChange={(next) => {
          setSide(next)
          setCountry(null)
          setSame(false)
        }}
      />
      {same ? (
        <Alert variant="destructive" data-testid="same-city">
          <CircleAlert />
          <AlertDescription>مقصد باید با مبدا فرق داشته باشد. شهر دیگری انتخاب کنید.</AlertDescription>
        </Alert>
      ) : null}
      {failed ? (
        <Alert data-testid="locations-failed">
          <CircleAlert />
          <AlertDescription className="flex w-full items-center justify-between gap-2">
            فهرست شهرها بارگذاری نشد
            <Button variant="outline" size="sm" onClick={() => void locations.refetch()}>
              <RefreshCw />
              تلاش مجدد
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {!country ? (
        <>
          {frequent && side === 'from' && !draft.from && locations.data?.frequent?.length ? (
            <Section title="مسیرهای پرتکرار">
              <Rows>
                {locations.data.frequent.map((row) => (
                  <Row key={`${placeKey(row.origin)}-${placeKey(row.destination)}`} icon={Route} title={`${row.origin.name_fa} ← ${row.destination.name_fa}`} onClick={() => onDone({ from: placeKey(row.origin), to: placeKey(row.destination) })} testId="frequent-route" />
                ))}
              </Rows>
            </Section>
          ) : null}
          <Section title={side === 'from' ? 'از کدام کشور؟' : 'به کدام کشور؟'}>
            {locations.isLoading ? (
              <div className="flex flex-col gap-2" data-testid="locations-loading">
                <Skeleton className="h-11 w-full" />
                <Skeleton className="h-11 w-full" />
                <Skeleton className="h-11 w-full" />
              </div>
            ) : (
              <Rows>
                {allowAny ? <Row icon={Globe} title="همه‌جا" onClick={() => choose('')} testId="anywhere" /> : null}
                {countries.map((row) => (
                  <Row key={row.code} icon={MapPin} title={row.name_fa} chevron onClick={() => setCountry(row)} testId={`country-${row.code}`} />
                ))}
              </Rows>
            )}
          </Section>
        </>
      ) : (
        <>
          {country.cities.length >= SEARCH_FROM || query || failed || typing || !country.cities.length ? <SearchBox value={query} onChange={setQuery} /> : null}
          {cities.length ? (
            <Rows>
              {cities.map((city) => {
                const key = `${country.code}:${city.slug}`
                return <Row key={key} icon={key === other ? Ban : MapPin} title={city.name_fa} disabled={key === other} onClick={() => choose(key)} testId={`city-${city.slug}`} />
              })}
            </Rows>
          ) : null}
          {name && !cities.length ? (
            <div data-testid="city-not-listed">
              <StateBox icon={SearchX} title={`${name} در فهرست نیست`}>
                {add.isError ? <p className="text-sm text-destructive" data-testid="city-invalid">اسم شهر را نفهمیدم؛ دوباره بنویسید یا از فهرست انتخاب کنید.</p> : null}
                <Button disabled={add.isPending} onClick={() => add.mutate(name)} data-testid="city-add">
                  <Plus />
                  اضافه کردن {name}
                </Button>
              </StateBox>
            </div>
          ) : null}
          {!name && !typing ? (
            <Button variant="ghost" className="w-full" onClick={() => setTyping(true)} data-testid="city-type">
              <ListPlus />
              شهر من در فهرست نیست
            </Button>
          ) : null}
        </>
      )}
    </AppSheet>
  )
}

// «از» and «به» above every courier list, with the swap between them.
export function RouteBar({ value, onOpen, onSwap, empty = 'همه‌جا' }: { value: RouteValue; onOpen: (side: 'from' | 'to') => void; onSwap?: () => void; empty?: string }) {
  const locations = useLocations()
  const end = (side: 'from' | 'to', word: string, key: string) => (
    <Button variant="outline" className="min-w-0 flex-1 justify-between" onClick={() => onOpen(side)} aria-label={`${word === 'از' ? 'مبدا' : 'مقصد'}: ${placeLabel(key, locations.data, empty)}`} data-testid={`route-${side}`}>
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="text-[13px] font-normal text-muted-foreground">{word}</span>
        <bdi className="truncate">{placeLabel(key, locations.data, empty)}</bdi>
      </span>
      <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
    </Button>
  )
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      {end('from', 'از', value.from)}
      {onSwap ? (
        <Button variant="outline" size="icon" onClick={onSwap} aria-label="جابه‌جا کردن مبدا و مقصد" data-testid="route-swap">
          <ArrowLeftRight />
        </Button>
      ) : null}
      {end('to', 'به', value.to)}
    </div>
  )
}
