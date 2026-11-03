import { ChevronDown, ChevronRight, MapPin, Search, SearchX, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { Card } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { StateBox } from '@/components/state-screen'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from '@/components/ui/input-group'
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { fa } from '@/lib/format'
import { areaNames, areasByCount, countMatches, facets, type View } from '@/lib/listing-view'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { Chip } from './filter-sheet'

const MAX_AREAS = 6
const TOP = 8

type Step = 'top' | 'all'

// F07. `onApply` gets the areas; from the row it applies to the list, from the filter sheet to that sheet's draft.
export function AreaSheet({ open, partial, onClose, cards, view, onApply, onSearchText, onChangeCity, currentSlug }: { open: boolean; partial?: boolean; onClose: () => void; cards: Card[]; view: View; onApply: (areas: string[]) => void; onSearchText?: (text: string) => void; onChangeCity?: (slug: string) => void; currentSlug?: string }) {
  const [areas, setAreas] = useState(view.areas)
  const [query, setQuery] = useState('')
  const [step, setStep] = useState<Step>('top')
  const cities = useCities()

  useEffect(() => {
    if (open) {
      setAreas(view.areas)
      setQuery('')
      setStep('top')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const rows = useMemo(() => areasByCount(cards, view.kind), [cards, view.kind])

  const countFor = (names: string[]) => countMatches(cards, { ...view, areas: names })
  // Chips and rows count one area from the other filters. Nothing to count when the fallback feed has more pages.
  const sections = useMemo(() => facets(cards, { ...view, areas: [] }), [cards, view])
  const rowCount = (name: string) => (partial ? undefined : (sections.areas[name] ?? 0))
  const toggle = (name: string) => {
    if (areas.includes(name)) return setAreas(areas.filter((item) => item !== name))
    if (areas.length >= MAX_AREAS) return void showToast({ message: `حداکثر ${fa(MAX_AREAS)} محله`, icon: 'alert', overSheet: true })
    track('area_selected')
    setAreas([...areas, name])
  }

  const text = query.trim().toLowerCase()
  // Names, plus the street part of a listing's area, which is how an intersection finds its neighbourhood.
  const hits = useMemo(() => {
    if (!text) return []
    const found = new Map<string, { name: string; detail: string }>()
    for (const card of cards) {
      if (card.kind !== view.kind) continue
      for (const name of areaNames(card)) {
        const inName = name.toLowerCase().includes(text)
        if (!inName && !card.area.toLowerCase().includes(text)) continue
        const detail = inName ? '' : (card.area.split(' · ')[1] ?? '')
        found.set(`${name}|${detail}`, { name, detail })
      }
    }
    return [...found.values()]
  }, [cards, text, view.kind])
  const otherCities = useMemo(() => {
    if (!text) return []
    return (cities.data?.countries ?? []).flatMap((country) => country.cities).filter((city) => city.slug !== currentSlug && (city.name_fa.includes(query.trim()) || city.name.toLowerCase().includes(text)))
  }, [cities.data, text, query, currentSlug])

  useEffect(() => {
    if (text && !hits.length && !otherCities.length) track('area_no_hit')
  }, [text, hits.length, otherCities.length])

  const finish = (next: string[]) => {
    onApply(next)
    onClose()
  }
  const foot = text ? null : (
    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={() => setAreas([])}>
        پاک کردن همه
      </Button>
      <Button className="min-w-0 flex-1" disabled={!partial && countFor(areas) === 0} onClick={() => finish(areas)}>
        {partial ? 'نمایش آگهی‌ها' : `نمایش ${fa(countFor(areas))} آگهی`}
      </Button>
    </div>
  )

  const searchBox = (
    <InputGroup>
      <InputGroupAddon>
        <Search />
      </InputGroupAddon>
      <InputGroupInput aria-label="جستجوی محله" placeholder="محله یا تقاطع…" value={query} onChange={(event) => setQuery(event.target.value)} />
      {query ? (
        <InputGroupAddon align="inline-end">
          <InputGroupButton size="icon-sm" aria-label="پاک کردن متن" onClick={() => setQuery('')}>
            <X />
          </InputGroupButton>
        </InputGroupAddon>
      ) : null}
    </InputGroup>
  )

  const label = (text_: string) => <p className="m-0 text-[13px] leading-5 font-bold text-muted-foreground">{text_}</p>

  return (
    <AppSheet
      open={open}
      full={step === 'all' || Boolean(text)}
      onClose={step === 'all' ? () => setStep('top') : onClose}
      label="محله"
      testId="area-sheet"
      title={
        step === 'all' ? (
          <span className="flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="برگشت" onClick={() => setStep('top')}>
              <ChevronRight className="size-5" />
            </Button>
            همه‌ی محله‌ها
          </span>
        ) : (
          'محله'
        )
      }
      foot={foot}
    >
      {searchBox}
      {text ? (
        hits.length || otherCities.length ? (
          <>
            {hits.length ? (
              <div className="flex flex-col gap-1" data-testid="area-hits">
                {label('محله')}
                <ItemGroup className="gap-0">
                  {hits.map((hit) => (
                    <Item key={hit.name + hit.detail} asChild className="px-0 disabled:opacity-50">
                      <button type="button" className="min-h-11 w-full text-start" disabled={rowCount(hit.name) === 0 && !areas.includes(hit.name)} onClick={() => finish(areas.includes(hit.name) ? areas : [...areas, hit.name].slice(0, MAX_AREAS))}>
                        <ItemMedia variant="icon">
                          <MapPin className="size-[18px]" />
                        </ItemMedia>
                        <ItemContent>
                          <ItemTitle>{hit.name}</ItemTitle>
                          {hit.detail ? <ItemDescription>{hit.detail}</ItemDescription> : null}
                        </ItemContent>
                        <ItemActions>
                          {rowCount(hit.name) !== undefined ? <span className="text-sm text-muted-foreground">{fa(rowCount(hit.name)!)} آگهی</span> : null}
                        </ItemActions>
                      </button>
                    </Item>
                  ))}
                </ItemGroup>
              </div>
            ) : null}
            {otherCities.length && onChangeCity ? (
              <div className="flex flex-col gap-1" data-testid="area-other-cities">
                {label('در شهرهای دیگر')}
                <ItemGroup className="gap-0">
                  {otherCities.map((city) => (
                    <Item key={city.slug} asChild className="px-0">
                      <button
                        type="button"
                        className="min-h-11 w-full text-start"
                        onClick={() => {
                          track('area_other_city')
                          onChangeCity(city.slug)
                          onClose()
                        }}
                      >
                        <ItemMedia variant="icon">
                          <MapPin className="size-[18px]" />
                        </ItemMedia>
                        <ItemContent>
                          <ItemTitle>{city.name_fa}</ItemTitle>
                        </ItemContent>
                        <ItemActions>
                          <span className="text-sm text-muted-foreground">تغییر شهر</span>
                        </ItemActions>
                      </button>
                    </Item>
                  ))}
                </ItemGroup>
              </div>
            ) : null}
          </>
        ) : (
          <StateBox icon={SearchX} title="محله‌ای با این نام پیدا نشد" testId="area-none">
            {onSearchText ? (
              <Button
                onClick={() => {
                  onSearchText(query.trim())
                  onClose()
                }}
              >
                <Search />
                همین را جستجو کنید
              </Button>
            ) : null}
          </StateBox>
        )
      ) : step === 'top' ? (
        <div className="flex flex-col gap-2">
          {label('محله‌ها')}
          <div className="flex flex-wrap items-center gap-2" data-testid="top-areas">
            <Chip label="همه‌جا" on={areas.length === 0} onClick={() => setAreas([])} />
            {rows.slice(0, TOP).map((name) => (
              <Chip key={name} label={name} count={rowCount(name)} on={areas.includes(name)} onClick={() => toggle(name)} />
            ))}
            {rows.length > TOP ? (
              <Button variant="ghost" size="sm" onClick={() => setStep('all')}>
                بیشتر
                <ChevronDown />
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="flex flex-col" data-testid="all-areas">
          {rows.map((name) => (
            <label key={name} className="flex min-h-11 items-center gap-2 md:min-h-9">
              <Checkbox aria-label={name} checked={areas.includes(name)} disabled={rowCount(name) === 0 && !areas.includes(name)} onCheckedChange={() => toggle(name)} />
              <span className="min-w-0 flex-1 text-sm">{name}</span>
              {rowCount(name) !== undefined ? <span className="text-sm text-muted-foreground">{fa(rowCount(name)!)}</span> : null}
            </label>
          ))}
        </div>
      )}
    </AppSheet>
  )
}
