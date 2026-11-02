import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getSession } from '@/lib/api'
import { cityFromPrefs, findCity, getSavedCity, saveCity, toSaved, useSavedCity } from '@/lib/city'
import { useCities } from '@/lib/use-cities'
import { CityGoneScreen, CountryScreen } from './first-run'
import { ListScreen } from './list'

// F01 for a first open, F02 for a return, and `?city=` from a link. A link's city shows here without replacing the saved one.
export function HomeScreen() {
  const [params] = useSearchParams()
  const saved = useSavedCity()
  const cities = useCities()
  const linked = params.get('city')
  const known = linked && cities.data ? findCity(cities.data, linked) : null

  useEffect(() => {
    if (!saved && known) void saveCity(toSaved(known.country, known.row))
    if (!saved && !linked && cities.data) {
      const fromServer = cityFromPrefs(getSession()?.prefs, cities.data)
      if (fromServer) void saveCity(fromServer)
    }
  }, [saved, known, linked, cities.data])

  if (known) return <ListScreen city={toSaved(known.country, known.row)} />
  if (!saved) {
    if (linked && cities.isPending) return <div className="h-full" data-testid="checking" />
    return <CountryScreen />
  }
  // The list needs no answer from /cities/ to open, so a failed call there never takes it down.
  const inactive = (cities.data && !findCity(cities.data, saved.slug)) || getSession()?.prefs.city_active === false
  if (inactive && getSavedCity()?.slug === saved.slug) return <CityGoneScreen saved={saved} onPick={(next) => void saveCity(next)} />
  return <ListScreen city={saved} />
}

