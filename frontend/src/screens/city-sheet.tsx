import { Check, ChevronRight, CircleAlert, ListPlus, MapPinOff, RefreshCw, Search } from 'lucide-react'
import { useState } from 'react'
import type { CityRow, CountryRow } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { Flag } from '@/components/flag'
import { StateBox } from '@/components/state-screen'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { ItemGroup } from '@/components/ui/item'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { toSaved, type SavedCity } from '@/lib/city'
import { countryName, fa } from '@/lib/format'
import { useCities } from '@/lib/use-cities'
import { RowButton, RowsLoading, WAITLIST_ROW } from './first-run'
import { WaitlistSheet } from './waitlist-sheet'

const LONG_LIST = 8

// F03#1, #2, #5, #6: one sheet with a country step. A country with more than 8 active cities gets a search.
export function CitySheet({ open, current, onClose, onPick }: { open: boolean; current: SavedCity; onClose: () => void; onPick: (next: SavedCity) => void }) {
  const cities = useCities()
  const [step, setStep] = useState<'cities' | 'countries'>('cities')
  const [countryCode, setCountryCode] = useState(current.country)
  const [query, setQuery] = useState('')
  const [waitlist, setWaitlist] = useState<string | null>(null)
  const country = cities.data?.countries.find((row) => row.code === countryCode)

  const close = () => {
    setStep('cities')
    setQuery('')
    setCountryCode(current.country)
    onClose()
  }
  const pick = (from: CountryRow, row: CityRow) => {
    onPick(toSaved(from, row))
    close()
  }
  const toWaitlist = (typed = '') => setWaitlist(typed)

  const long = (country?.cities.length ?? 0) > LONG_LIST
  const shown = country?.cities.filter((row) => !query || row.name_fa.includes(query) || row.name.toLowerCase().includes(query.toLowerCase())) ?? []

  return (
    <>
      <AppSheet
        open={open && waitlist === null}
        onClose={step === 'countries' ? () => setStep('cities') : close}
        label={step === 'countries' ? 'کشور' : 'شهر'}
        testId="city-sheet"
        title={
          step === 'countries' ? (
            'کشور'
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setStep('countries')}>
              <ChevronRight />
              کشور
            </Button>
          )
        }
      >
        {cities.isPending ? (
          <RowsLoading />
        ) : !cities.data ? (
          // F25#8
          <StateBox icon={CircleAlert} title="بارگذاری نشد" testId="cities-failed">
            <Button className="w-full" onClick={() => void cities.refetch()}>
              <RefreshCw />
              تلاش مجدد
            </Button>
          </StateBox>
        ) : step === 'countries' ? (
          <ItemGroup className="gap-0">
            {cities.data?.countries.map((row) => (
              <RowButton
                key={row.code}
                title={countryName(row.code)}
                flag={row.code}
                value={`${fa(row.cities.length)} شهر`}
                side={row.code === countryCode ? <Check className="size-4" aria-hidden /> : null}
                onClick={() => {
                  setCountryCode(row.code)
                  setStep('cities')
                }}
              />
            ))}
            <RowButton title="کشور من در فهرست نیست" value="لیست انتظار" icon={ListPlus} onClick={() => toWaitlist()} />
          </ItemGroup>
        ) : country ? (
          <>
            <h2 className="m-0 text-lg leading-[26px] font-extrabold">{countryName(country.code)}</h2>
            {long ? (
              <InputGroup>
                <InputGroupAddon>
                  <Search />
                </InputGroupAddon>
                <InputGroupInput placeholder="جستجوی شهر" value={query} onChange={(event) => setQuery(event.target.value)} />
              </InputGroup>
            ) : null}
            {shown.length ? (
              <RadioGroup dir="rtl" value={current.slug} className={long ? 'grid-cols-2 gap-0' : 'gap-0'}>
                {shown.map((row) => (
                  <label key={row.slug} className="flex min-h-12 items-center gap-3 py-1">
                    <RadioGroupItem value={row.slug} aria-label={row.name_fa} onClick={() => pick(country, row)} />
                    <Flag code={country.code} />
                    <span className="text-[15px] font-semibold">{row.name_fa}</span>
                  </label>
                ))}
              </RadioGroup>
            ) : (
              <StateBox icon={MapPinOff} title={`${query} هنوز در چمدون نیست`}>
                <Button className="w-full" onClick={() => toWaitlist(query)}>
                  به لیست انتظار اضافه کنید
                </Button>
              </StateBox>
            )}
            {long ? null : (
              <ItemGroup className="gap-0">
                <RowButton title={WAITLIST_ROW} value="لیست انتظار" icon={ListPlus} onClick={() => toWaitlist()} />
              </ItemGroup>
            )}
          </>
        ) : null}
      </AppSheet>
      {waitlist !== null ? (
        <WaitlistSheet
          key={waitlist}
          open
          country={countryCode}
          initial={waitlist}
          onClose={() => setWaitlist(null)}
          onPickListed={(typed) => {
            const row = country?.cities.find((city) => city.name_fa === typed)
            setWaitlist(null)
            if (row && country) pick(country, row)
          }}
        />
      ) : null}
    </>
  )
}
