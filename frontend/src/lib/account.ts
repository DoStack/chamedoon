import type { Account, Prefs } from '@/api/types'
import { meFetch, unwrap, type ApiInit, type ApiResult } from './api'
import { closeFeature } from './features'
import { queryClient } from './query'

export const ACCOUNT_KEY = ['account']

// Every call behind `features.account`. `feature_off` means meta was out of date, so the entry points leave.
export async function accountFetch<T>(path: string, init?: ApiInit): Promise<ApiResult<T>> {
  const result = await meFetch<T>(path, init)
  if (!result.ok && result.code === 'feature_off') closeFeature('account')
  return result
}

export const fetchAccount = () => unwrap(accountFetch<Account>('/api/v1/me/account/'))

// F22 and F23 save here. This path stays open whatever the flags say.
export const patchPrefs = (body: Partial<Prefs>) => meFetch<Prefs>('/api/v1/me/prefs/', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export function setCachedPrefs(change: Partial<Prefs>) {
  const account = queryClient.getQueryData<Account>(ACCOUNT_KEY)
  if (account) queryClient.setQueryData(ACCOUNT_KEY, { ...account, prefs: { ...account.prefs, ...change } })
}
