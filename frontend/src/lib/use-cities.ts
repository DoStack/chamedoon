import { useQuery } from '@tanstack/react-query'
import type { Cities } from '@/api/types'
import { ApiFailureError, publicFetch, unwrap } from './api'

function checked(cities: Cities) {
  if (!Array.isArray(cities?.countries)) throw new ApiFailureError({ ok: false, code: 'bad_response' })
  return cities
}

export function useCities() {
  return useQuery({ queryKey: ['cities'], queryFn: () => unwrap(publicFetch<Cities>('/api/v1/public/cities/')).then(checked), staleTime: 5 * 60_000 })
}
