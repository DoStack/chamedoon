import type { DeepLink } from '@/api/types'
import { publicFetch, setLinkSource } from './api'
import { track } from './report'
import { getStartParam } from './telegram'

let resolving: Promise<DeepLink | null> | null = null

// Where the launch link goes. A link that cannot be read or resolved is a normal open, never an error.
export function resolveStart(): Promise<DeepLink | null> {
  resolving ??= (async () => {
    const value = getStartParam()
    if (!value || value === 'qa') return null // See `asksForQa`: not a place to go.
    const result = await publicFetch<DeepLink>(`/api/v1/public/deeplink/?startapp=${encodeURIComponent(value)}`)
    if (!result.ok) return null
    if (result.data.src) setLinkSource(result.data.src)
    if (result.data.malformed) track('deeplink_bad')
    return result.data
  })()
  return resolving
}
