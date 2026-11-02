// SPIKE P0: throwaway, delete before P1.
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import './index.css'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { applyFilters, facetCounts, type Filters, type Listing } from './facets'
import { initTelegram, tgReport } from './tg'

const SNAPSHOT = `${import.meta.env.BASE_URL}feeds/sample.v1.json`
const MapRoute = lazy(() => import('./MapRoute'))

async function loadSnapshot() {
  const r = await fetch(SNAPSHOT)
  return (await r.json()) as { listings: Listing[] }
}

function ListScreen() {
  const { data, isLoading } = useQuery({ queryKey: ['snapshot'], queryFn: loadSnapshot })
  const [filters, setFilters] = useState<Filters>({})
  const all = data?.listings ?? []
  const rows = useMemo(() => applyFilters(all, filters), [all, filters])
  const counts = useMemo(() => facetCounts(all, filters), [all, filters])
  const toggle = (v: string) =>
    setFilters((f) => ({ ...f, property_type: f.property_type?.includes(v) ? f.property_type.filter((x) => x !== v) : [...(f.property_type ?? []), v] }))
  return (
    <div className="app-shell">
      <header className="app-top border-b bg-card p-3 flex items-center justify-between gap-2">
        <span className="font-bold" data-testid="count">{isLoading ? '…' : `${rows.length} آگهی`}</span>
        <Sheet>
          <SheetTrigger asChild>
            <Button variant="secondary" size="sm">فیلترها</Button>
          </SheetTrigger>
          <SheetContent side="bottom">
            <SheetHeader><SheetTitle>نوع ملک</SheetTitle></SheetHeader>
            <div className="flex flex-wrap gap-2 p-4">
              {Object.entries(counts.property_type ?? {}).sort().map(([v, n]) => (
                <Button key={v} size="sm" variant={filters.property_type?.includes(v) ? 'default' : 'outline'} onClick={() => toggle(v)}>{v} ({n})</Button>
              ))}
            </div>
          </SheetContent>
        </Sheet>
      </header>
      <main className="flex-1 overflow-auto p-3 grid gap-3 content-start">
        {rows.slice(0, 30).map((l) => (
          <Card key={l.id} size="sm">
            <CardHeader><CardTitle>{l.area}</CardTitle></CardHeader>
            <CardContent className="grid gap-2">
              <div className="font-bold">{l.price_amount ?? 'بپرسید'} <bdi dir="ltr">{l.price_currency}</bdi></div>
              <div className="flex flex-wrap gap-1">
                <Badge variant="secondary">{l.property_type}</Badge>
                <Badge variant="outline">{l.furnished}</Badge>
              </div>
              <p className="text-sm text-muted-foreground line-clamp-2">{l.summary_fa}</p>
            </CardContent>
          </Card>
        ))}
      </main>
      <nav className="app-bottom border-t bg-card p-2 flex justify-around">
        <Button variant="ghost" size="sm" asChild><Link to="/map">نقشه</Link></Button>
        <Button variant="ghost" size="sm" asChild><Link to="/tg">Telegram</Link></Button>
        <Button variant="ghost" size="sm" asChild><Link to="/bench">Bench</Link></Button>
      </nav>
    </div>
  )
}

function TgScreen() {
  const [report, setReport] = useState<any>(null)
  useEffect(() => {
    const tick = () => { const r = tgReport(); setReport(r); (window as any).__tg = r }
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="app-shell">
      <header className="app-top border-b bg-card p-3 font-bold" data-testid="top">وضعیت تلگرام</header>
      <main className="flex-1 overflow-auto p-3"><pre dir="ltr" className="text-xs whitespace-pre-wrap" data-testid="report">{JSON.stringify(report, null, 1)}</pre></main>
      <nav className="app-bottom border-t bg-card p-2 flex justify-center" data-testid="bottom"><Button size="sm" asChild><Link to="/">فهرست</Link></Button></nav>
    </div>
  )
}

function pct(xs: number[], p: number) {
  const s = [...xs].sort((a, b) => a - b)
  return +s[Math.min(s.length - 1, Math.floor(s.length * p))].toFixed(2)
}

function BenchScreen() {
  const [out, setOut] = useState<any>(null)
  useEffect(() => {
    ;(async () => {
      const t0 = performance.now()
      const r = await fetch(SNAPSHOT, { cache: 'no-store' })
      const text = await r.text()
      const t1 = performance.now()
      const snap = JSON.parse(text) as { listings: Listing[] }
      const t2 = performance.now()
      const all = snap.listings
      const facetFirst = (() => { const a = performance.now(); facetCounts(all, {}); return performance.now() - a })()
      const types = ['room', 'studio', 'apartment', 'house', 'basement']
      const areas = [...new Set(all.map((l) => l.area))]
      const each: number[] = []
      for (let i = 0; i < 50; i++) {
        const f: Filters = {
          property_type: i % 2 ? [types[i % 5]] : [types[i % 5], types[(i + 2) % 5]],
          furnished: i % 3 ? [] : ['yes'],
          price: i % 4 ? ['800-1200', '1200-1600'] : [],
          area: i % 5 ? [] : [areas[i % areas.length]],
        }
        const a = performance.now()
        facetCounts(all, f)
        applyFilters(all, f)
        each.push(performance.now() - a)
      }
      const entry = performance.getEntriesByName(new URL(SNAPSHOT, location.href).href).pop() as PerformanceResourceTiming | undefined
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming
      const result = {
        url: SNAPSHOT,
        listings: all.length,
        contentEncoding: r.headers.get('content-encoding'),
        contentType: r.headers.get('content-type'),
        cacheControl: r.headers.get('cache-control'),
        xVercelCache: r.headers.get('x-vercel-cache'),
        transferBytes: entry?.transferSize ?? null,
        encodedBytes: entry?.encodedBodySize ?? null,
        decodedBytes: entry?.decodedBodySize ?? null,
        downloadMs: +(t1 - t0).toFixed(1),
        parseMs: +(t2 - t1).toFixed(1),
        facetFirstMs: +facetFirst.toFixed(1),
        filterChange: { n: each.length, p50: pct(each, 0.5), p95: pct(each, 0.95), max: pct(each, 1) },
        domContentLoadedMs: +nav.domContentLoadedEventEnd.toFixed(0),
      }
      ;(window as any).__bench = result
      setOut(result)
    })()
  }, [])
  return (
    <div className="app-shell">
      <main className="flex-1 overflow-auto p-3"><pre dir="ltr" className="text-xs" data-testid="bench">{JSON.stringify(out, null, 1)}</pre></main>
    </div>
  )
}

initTelegram()
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient()}>
    <BrowserRouter basename="/app/v2-spike">
      <Suspense fallback={null}>
        <Routes>
          <Route path="/" element={<ListScreen />} />
          <Route path="/tg" element={<TgScreen />} />
          <Route path="/bench" element={<BenchScreen />} />
          <Route path="/map" element={<MapRoute />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </QueryClientProvider>,
)
