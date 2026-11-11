import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ArrowLeft, CalendarDays, History, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Flag } from '@/components/flag'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { useFeature } from './access'
import { courierFetch, unwrap } from './api'
import { query } from './filters'
import { monthLabel, nounFor, sizeName, upcomingMonths, type Side } from './format'
import { Page, Stack, useMe } from './page'
import { categoryIcon } from './parts'
import { placeLabel, useLocations } from './route-sheet'
import { parseCourier, type Parsed } from './smart-parse'
import type { components } from '@/api/courier-schema.gen'

type Facets = components['schemas']['Facets']

const RECENT = 'courier.smart.recent'
const TYPING_MS = 300

function readRecent(): Array<{ from: string; to: string }> {
  try {
    const rows = JSON.parse(localStorage.getItem(RECENT) ?? '[]')
    return Array.isArray(rows) ? rows.filter((row) => typeof row?.from === 'string' && typeof row?.to === 'string').slice(0, 4) : []
  } catch {
    return []
  }
}

function saveRecent(from: string, to: string) {
  if (!from || !to) return
  try {
    const rows = [{ from, to }, ...readRecent().filter((row) => row.from !== from || row.to !== to)].slice(0, 4)
    localStorage.setItem(RECENT, JSON.stringify(rows))
  } catch {
    // Storage can be blocked; recent searches are a convenience.
  }
}

const country = (key: string) => key.split(':')[0] ?? ''

function Understood({ icon, children, onClear, testId }: { icon: ReactNode; children: ReactNode; onClear: () => void; testId: string }) {
  return (
    <button type="button" className="inline-flex items-center gap-1 rounded-full bg-accent px-2.5 py-1.5 text-xs leading-none font-semibold text-accent-foreground" onClick={onClear} data-testid={testId} aria-label="حذف">
      {icon}
      {children}
      <X className="size-3.5" aria-hidden />
    </button>
  )
}

// v4_smart_search: one line, read by rules into the list's own route and filters.
export function SmartSearchScreen() {
  const on = useFeature('v4_smart_search')
  const navigate = useNavigate()
  const me = useMe()
  const locations = useLocations()
  const [text, setText] = useState('')
  const [typed, setTyped] = useState('')
  const [dropped, setDropped] = useState<Array<keyof Parsed>>([])
  useEffect(() => {
    const timer = setTimeout(() => setTyped(text), TYPING_MS)
    return () => clearTimeout(timer)
  }, [text])
  useEffect(() => setDropped([]), [typed])

  const parsed = useMemo(() => {
    const raw = parseCourier(typed, locations.data?.countries ?? [], locations.data?.categories ?? [])
    const months = upcomingMonths()
    if (raw.month && !months.includes(raw.month)) raw.month = ''
    for (const key of dropped) raw[key] = '' as never
    return raw
  }, [typed, locations.data, dropped])
  const side: Side = parsed.side || ((me.data?.prefs?.side as Side) ?? 'DEMAND')
  const anything = Boolean(parsed.from || parsed.to || parsed.month || parsed.cat || parsed.size)

  const facets = useQuery({
    queryKey: ['courier', 'facets', side, parsed.from, parsed.to, parsed.month, parsed.size, parsed.cat],
    queryFn: () => unwrap(courierFetch<Facets>(`requests/facets/${query({ side, origin: parsed.from, destination: parsed.to, month: parsed.month, size: parsed.size, category: parsed.cat })}`)),
    enabled: on && anything && parsed.from !== parsed.to,
    placeholderData: keepPreviousData,
    retry: false,
  })

  useEffect(() => {
    if (typed) track('courier_smart_search_parsed', { length: typed.length, from: Boolean(parsed.from), to: Boolean(parsed.to), month: Boolean(parsed.month), category: Boolean(parsed.cat), size: Boolean(parsed.size) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed])

  if (!on) return <Navigate to="/" replace />

  const show = (from = parsed.from, to = parsed.to, extra: Partial<Parsed> = parsed) => {
    track('courier_smart_search_show', { total: facets.data?.total ?? null })
    saveRecent(from, to)
    navigate(`/${query({ side, from, to, month: extra.month, size: extra.size, cat: extra.cat })}`)
  }
  const drop = (key: keyof Parsed) => setDropped([...dropped, key])
  const months = upcomingMonths()
  const cat = locations.data?.categories.find((row) => row.code === parsed.cat)
  const CatIcon = categoryIcon(parsed.cat)
  const recent = readRecent()

  return (
    <Page tab={null} back="/" testId="smart-search">
      <Stack center gap="gap-3">
        <div className="flex h-12 items-center gap-2 rounded-xl border-2 border-primary bg-card px-3">
          <Sparkles className="size-[18px] flex-none text-primary" aria-hidden />
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
            placeholder="مثلاً: از تورنتو به تهران تا آخر نوامبر، مدارک"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && anything && show()}
            aria-label="جستجوی هوشمند"
            data-testid="smart-input"
          />
          {text ? (
            <button type="button" className="flex text-muted-foreground" onClick={() => setText('')} aria-label="پاک کردن">
              <X className="size-[18px]" />
            </button>
          ) : null}
        </div>
        {anything ? (
          <Card className="!gap-2 !rounded-2xl !p-3" data-testid="smart-understood">
            <span className="text-xs text-muted-foreground">فهمیدم:</span>
            <div className="flex flex-wrap gap-1.5">
              {parsed.from ? (
                <Understood icon={<Flag code={country(parsed.from)} />} onClear={() => drop('from')} testId="smart-from">
                  <bdi>{placeLabel(parsed.from, locations.data)}</bdi>
                </Understood>
              ) : null}
              {parsed.to ? (
                <Understood icon={<Flag code={country(parsed.to)} />} onClear={() => drop('to')} testId="smart-to">
                  <bdi>{placeLabel(parsed.to, locations.data)}</bdi>
                </Understood>
              ) : null}
              {parsed.month ? (
                <Understood icon={<CalendarDays className="size-3.5" aria-hidden />} onClear={() => drop('month')} testId="smart-month">
                  {monthLabel(parsed.month, parsed.month === months[0])}
                </Understood>
              ) : null}
              {cat ? (
                <Understood icon={<CatIcon className="size-3.5" aria-hidden />} onClear={() => drop('cat')} testId="smart-cat">
                  {cat.name_fa}
                </Understood>
              ) : null}
              {parsed.size ? (
                <Understood icon={null} onClear={() => drop('size')} testId="smart-size">
                  {sizeName(side, parsed.size)}
                </Understood>
              ) : null}
            </div>
          </Card>
        ) : null}
        {anything ? (
          <Button size="lg" className="w-full" onClick={() => show()} data-testid="smart-show">
            {facets.data ? `نمایش ${fa(facets.data.total)} ${nounFor(side)}` : 'نمایش نتایج'}
          </Button>
        ) : null}
        {recent.length ? (
          <div className="flex flex-col" data-testid="smart-recent">
            {recent.map((row) => (
              <button key={`${row.from}-${row.to}`} type="button" className="flex items-center gap-2 border-b py-3 text-sm font-semibold last:border-b-0" onClick={() => show(row.from, row.to, {})}>
                <History className="size-4 text-muted-foreground" aria-hidden />
                <Flag code={country(row.from)} />
                <bdi>{placeLabel(row.from, locations.data)}</bdi>
                <ArrowLeft className="size-4 text-muted-foreground" aria-hidden />
                <Flag code={country(row.to)} />
                <bdi>{placeLabel(row.to, locations.data)}</bdi>
              </button>
            ))}
          </div>
        ) : null}
      </Stack>
    </Page>
  )
}
