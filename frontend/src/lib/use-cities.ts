import { useQuery } from '@tanstack/react-query'
import type { Cities } from '@/api/types'
import { publicFetch, unwrap } from './api'

export function useCities() {
  return useQuery({ queryKey: ['cities'], queryFn: () => unwrap(publicFetch<Cities>('/api/v1/public/cities/')), staleTime: 5 * 60_000 })
}
