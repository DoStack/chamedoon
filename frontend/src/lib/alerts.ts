import type { AlertList } from '@/api/types'
import { meFetch, unwrap, type ApiInit, type ApiResult } from './api'
import { closeFeature } from './features'

export const ALERTS_KEY = ['alerts']

// Every call of F15 and F16. `feature_off` means meta was out of date, so the entry points leave.
export async function alertsFetch<T>(path: string, init?: ApiInit): Promise<ApiResult<T>> {
  const result = await meFetch<T>(path, init)
  if (!result.ok && result.code === 'feature_off') closeFeature('alerts')
  return result
}

export const fetchAlerts = () => unwrap(alertsFetch<AlertList>('/api/v1/me/alerts/'))
