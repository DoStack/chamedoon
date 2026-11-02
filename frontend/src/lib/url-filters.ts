import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

export type Filters<K extends string> = Record<K, string[]>

export function readFilters<K extends string>(params: URLSearchParams, keys: readonly K[]): Filters<K> {
  return Object.fromEntries(keys.map((key) => [key, params.getAll(key)])) as Filters<K>
}

export function writeFilter(params: URLSearchParams, key: string, values: readonly string[]): URLSearchParams {
  const next = new URLSearchParams(params)
  next.delete(key)
  for (const value of values) next.append(key, value)
  return next
}

// The URL is the only store for filters, so back, share and reload keep them. Other query keys are left alone.
export function useUrlFilters<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams()
  const filters = useMemo(() => readFilters(params, keys), [params, keys])
  const setFilter = useCallback(
    (key: K, values: readonly string[]) => setParams((prev) => writeFilter(prev, key, values), { replace: true }),
    [setParams],
  )
  return { filters, setFilter }
}
