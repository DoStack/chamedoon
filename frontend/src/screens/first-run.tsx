import { ChevronLeft, CircleAlert, ListPlus, RefreshCw, WifiOff } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import type { CityRow, CountryRow } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { Flag } from '@/components/flag'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Toggle } from '@/components/ui/toggle'
import { ApiFailureError } from '@/lib/api'
import { useBack } from '@/lib/back'
import { saveCity, toSaved, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { countryName, fa } from '@/lib/format'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { SlowNote, useAfter } from './global-states'
import { WaitlistSheet } from './waitlist-sheet'

export const WAITLIST_ROW = 'شهر من در فهرست نیست'

export function RowButton({ title, value, flag, icon: Icon, onClick, side }: { title: string; value?: string; flag?: string; icon?: typeof ListPlus; onClick: () => void; side?: React.ReactNode }) {
  return (
    <Item asChild className="px-0">
      <button type="button" onClick={onClick} className="min-h-14 w-full text-start">
        {flag ? (
          <ItemMedia>
            <Flag code={flag} />
          </ItemMedia>
        ) : null}
        {Icon ? (
          <ItemMedia variant="icon">
            <Icon className="size-[18px]" />
          </ItemMedia>
        ) : null}
        <ItemContent>
          <ItemTitle>{title}</ItemTitle>
        </ItemContent>
        <ItemActions>
          {value ? <span className="text-sm text-muted-foreground">{value}</span> : null}
          {side}
          <ChevronLeft className="size-4" aria-hidden />
        </ItemActions>
      </button>
    </Item>
  )
}

// F25#8, or F01#11 when the connection is the cause.
function CitiesFailed({ error, retry }: { error: unknown; retry: () => void }) {
  const code = error instanceof ApiFailureError ? error.failure.code : null
  const offline = code === 'offline' || code === 'network_error' || code === 'timeout'
  return (
    <StateScreen icon={offline ? WifiOff : CircleAlert} title={offline ? 'اتصال برقرار نشد' : 'بارگذاری نشد'} description={offline ? 'اتصال اینترنت خود را بررسی کنید.' : undefined} testId="cities-failed">
      <Button onClick={retry}>
        <RefreshCw />
        تلاش مجدد
      </Button>
    </StateScreen>
  )
}

export function Column({ children, testId, onClickCapture }: { children: React.ReactNode; testId?: string; onClickCapture?: React.MouseEventHandler }) {
  return (
    <div className="h-full overflow-y-auto" data-testid={testId} onClickCapture={onClickCapture}>
      <div className="mx-auto flex w-full max-w-[560px] flex-col gap-4 p-4 md:p-6">{children}</div>
    </div>
  )
}

const H1 = ({ children }: { children: React.ReactNode }) => <h1 className="m-0 text-2xl leading-8 font-extrabold">{children}</h1>

// The second tap of a double tap lands on whatever replaced the row under the finger, so it must not choose a city.
const REPEAT_MS = 500
const REPEAT_PX = 24
let lastTap = { x: 0, y: 0, at: 0 }

function rememberTap(event: React.MouseEvent) {
  lastTap = { x: event.clientX, y: event.clientY, at: Date.now() }
}

function dropRepeatTap(event: React.MouseEvent) {
  if (Date.now() - lastTap.at < REPEAT_MS && Math.abs(event.clientX - lastTap.x) < REPEAT_PX && Math.abs(event.clientY - lastTap.y) < REPEAT_PX) {
    event.preventDefault()
    event.stopPropagation()
  }
}

// F25#4
export function RowsLoading() {
  const slow = useAfter(3000, true)
  return (
    <div className="flex flex-col gap-2" data-testid="rows-loading">
      {slow ? <SlowNote /> : null}
      {Array.from({ length: 3 }, (_, i) => (
        <Skeleton key={i} className="h-14 w-full" />
      ))}
    </div>
  )
}

// F01#1
export function CountryScreen() {
  const cities = useCities()
  const navigate = useNavigate()
  if (cities.isError) return <CitiesFailed error={cities.error} retry={() => void cities.refetch()} />
  return (
    <Column testId="country-screen" onClickCapture={rememberTap}>
      <H1>کشور شما؟</H1>
      {cities.isPending ? <RowsLoading /> : null}
      {cities.data?.countries.map((country) => (
        <Card key={country.code} className="gap-0 px-4 py-0">
          <RowButton title={countryName(country.code)} flag={country.code} onClick={() => navigate(`/choose?country=${country.code}`)} />
        </Card>
      ))}
    </Column>
  )
}

// Cities that are drawn as parts of one metro (F01#3). The contract has no field for this, so it lives here for now.
const MULTI_PART = new Set(['cologne'])
const PART_NAMES: Record<string, string> = { Köln: 'کلن', Düsseldorf: 'دوسلدورف', Bonn: 'بن' }

// F01#3, #14: live counts from the city's snapshot. With no snapshot the metro opens whole.
function PartsSheet({ row, country, onPick, onClose }: { row: CityRow; country: string; onPick: (parts: string[]) => void; onClose: () => void }) {
  const feed = useFeed(row.slug, country)
  const parts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const card of feed.cards) if (card.kind === 'offer') counts.set(card.city, (counts.get(card.city) ?? 0) + 1)
    return [...counts.entries()]
  }, [feed.cards])
  const [off, setOff] = useState<string[]>([])
  useEffect(() => {
    if (feed.failed) onPick([])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feed.failed])
  const on = parts.filter(([name]) => !off.includes(name))
  const total = on.reduce((sum, [, count]) => sum + count, 0)
  return (
    <AppSheet
      open
      onClose={onClose}
      label={row.name_fa}
      title={row.name_fa}
      testId="parts-sheet"
      foot={
        <Button className="w-full" disabled={on.length === 0 || feed.loading} onClick={() => onPick(on.length === parts.length ? [] : on.map(([name]) => name))}>
          نمایش {fa(total)} آگهی
        </Button>
      }
    >
      <div className="flex flex-wrap gap-2">
        {parts.map(([name, count]) => (
          <Toggle
            key={name}
            variant="outline"
            pressed={!off.includes(name)}
            onPressedChange={(pressed) => setOff(pressed ? off.filter((item) => item !== name) : [...off, name])}
            className="gap-1.5 px-3 text-sm data-[state=on]:border-primary data-[state=on]:bg-accent data-[state=on]:text-accent-foreground"
          >
            {PART_NAMES[name] ?? name}
            <span className="text-xs font-normal">{fa(count)}</span>
          </Toggle>
        ))}
      </div>
    </AppSheet>
  )
}

export function CityList({ country, onPick, onWaitlist }: { country: CountryRow; onPick: (row: CityRow) => void; onWaitlist: () => void }) {
  return (
    <Card className="gap-0 px-4 py-0">
      <ItemGroup className="gap-0">
        {country.cities.map((row) => (
          <RowButton key={row.slug} title={row.name_fa} flag={country.code} onClick={() => onPick(row)} />
        ))}
        <RowButton title={WAITLIST_ROW} value="لیست انتظار" icon={ListPlus} onClick={onWaitlist} />
      </ItemGroup>
    </Card>
  )
}

// F01#2. A link with only a country starts here too (F01 rules).
export function CityScreen() {
  const cities = useCities()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const code = params.get('country')
  const [waitlist, setWaitlist] = useState(false)
  const [parts, setParts] = useState<CityRow | null>(null)
  useBack(() => navigate('/', { replace: true }))
  if (cities.isError) return <CitiesFailed error={cities.error} retry={() => void cities.refetch()} />
  const country = cities.data?.countries.find((row) => row.code === code)

  const choose = (row: CityRow, chosen: string[] = []) => {
    void saveCity(toSaved(country!, row))
    track('city_chosen', { city: row.slug })
    navigate(chosen.length ? `/?${chosen.map((part) => `parts=${encodeURIComponent(part)}`).join('&')}` : '/', { replace: true })
  }
  const pickListed = (typed: string) => {
    for (const candidate of cities.data?.countries ?? []) {
      const row = candidate.cities.find((city) => city.name_fa === typed || city.name.toLowerCase() === typed.toLowerCase())
      if (row) {
        void saveCity(toSaved(candidate, row))
        navigate('/', { replace: true })
        return
      }
    }
    setWaitlist(false)
  }

  return (
    <Column testId="city-screen" onClickCapture={dropRepeatTap}>
      <H1>کدام شهر؟</H1>
      {cities.isPending ? <RowsLoading /> : null}
      {country ? <CityList country={country} onPick={(row) => (MULTI_PART.has(row.slug) ? setParts(row) : choose(row))} onWaitlist={() => setWaitlist(true)} /> : null}
      <WaitlistSheet open={waitlist} country={code ?? ''} onClose={() => setWaitlist(false)} onPickListed={pickListed} />
      {parts && country ? <PartsSheet row={parts} country={country.code} onClose={() => setParts(null)} onPick={(chosen) => choose(parts, chosen)} /> : null}
    </Column>
  )
}

// F02#5
export function CityGoneScreen({ saved, onPick }: { saved: SavedCity; onPick: (next: SavedCity) => void }) {
  const cities = useCities()
  const [waitlist, setWaitlist] = useState(false)
  useEffect(() => track('city_inactive', { city: saved.slug }), [saved.slug])
  if (cities.isError) return <CitiesFailed error={cities.error} retry={() => void cities.refetch()} />
  return (
    <Column testId="city-gone">
      <Alert>
        <CircleAlert />
        <AlertDescription>{saved.name_fa} دیگر در چمدون نیست. شهر دیگری انتخاب کنید.</AlertDescription>
      </Alert>
      <H1>کدام شهر؟</H1>
      <Card className="gap-2 px-4 py-3">
        {cities.data?.countries.map((country) => (
          <div key={country.code}>
            <p className="m-0 text-[13px] leading-5 font-bold text-muted-foreground">{countryName(country.code)}</p>
            <ItemGroup className="gap-0">
              {country.cities.map((row) => (
                <RowButton key={row.slug} title={row.name_fa} flag={country.code} onClick={() => onPick(toSaved(country, row))} />
              ))}
            </ItemGroup>
          </div>
        ))}
        <RowButton title={WAITLIST_ROW} value="لیست انتظار" icon={ListPlus} onClick={() => setWaitlist(true)} />
      </Card>
      <WaitlistSheet open={waitlist} country="" onClose={() => setWaitlist(false)} onPickListed={() => setWaitlist(false)} />
    </Column>
  )
}

