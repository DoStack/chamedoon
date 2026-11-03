import { showToast } from '@/components/app-toast'
import { track } from '@/lib/report'
import { getStartParam, getWebApp } from '@/lib/telegram'
import { postJson } from './api'
import { query } from './filters'
import type { components } from '@/api/courier-schema.gen'

type DeepLink = components['schemas']['DeepLink']

const SCREENS: Record<string, string> = {
  courier: '/',
  courier_alerts: '/alerts',
  demand: '/post/package',
  supply: '/post/trip',
  requests: '/me/requests',
  matches: '/me/matches',
  explore: '/',
  support: '/me/support',
}

// A webview can keep sessionStorage across launches, and each launch signs a new auth_date, so reopening the link applies it.
function alreadyUsed(value: string) {
  const launch = new URLSearchParams(getWebApp()?.initData ?? '').get('auth_date') ?? ''
  const mark = `${value}|${launch}`
  try {
    if (sessionStorage.getItem('chamedoon.courier.start') === mark) return true
    sessionStorage.setItem('chamedoon.courier.start', mark)
  } catch {
    // No storage: the link applies again, which only repeats a navigation.
  }
  return false
}

export type Landing = { path: string; state?: Record<string, unknown>; external?: boolean }

export function landingFor(link: DeepLink): Landing {
  switch (link.kind) {
    case 'housing':
      return { path: '/app/v2/', external: true }
    case 'request':
      return { path: `/request/${link.id}`, state: { fromLink: true, readOnly: link.read_only } }
    case 'own_request':
      return { path: `/me/requests/${link.id}` }
    case 'match':
      return { path: `/me/matches/${link.id}` }
    case 'ticket':
      return { path: `/me/support/${link.id}` }
    case 'intro':
      return { path: `/me/intros/${link.id}` }
    case 'travelers':
      return { path: `/${query({ ids: link.ids.join(',') })}` }
    default:
      return { path: SCREENS[link.screen ?? ''] ?? '/' }
  }
}

// C01: where the launch link goes, before the first screen, so a link never flashes the question. Null is a normal open.
// A bad link opens the list with a message (C01.6); a good link to something gone is the not-found screen (C18.6).
export async function resolveStart(): Promise<Landing | null> {
  const value = getStartParam()
  if (!value || alreadyUsed(value)) return null
  const result = await postJson<DeepLink>('deeplink/', { start: value })
  if (result.ok) {
    track('courier.c01.link', { kind: result.data.kind })
    return landingFor(result.data)
  }
  switch (result.code) {
    case 'not_found':
      track('courier.c01.link_not_found')
      return { path: '/missing' }
    case 'start_invalid':
      track('courier.c01.link_invalid')
      showToast({ message: 'این لینک معتبر نیست', icon: 'alert' })
      return { path: '/' }
    default:
      return null
  }
}
