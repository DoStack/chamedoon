import type { OutreachAlert, OutreachLanding } from '@/api/types'
import { getLinkSource, meFetch, type ApiResult } from './api'
import { track } from './report'
import { getWebApp, isVersionAtLeast } from './telegram'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

let landing: Promise<OutreachLanding | null> | null = null

// Telegram's own prompt, once, after the landing asked for it. Nothing waits on the answer.
function askWriteAccess() {
  const app = getWebApp()
  if (!app?.requestWriteAccess || !isVersionAtLeast('6.9')) return
  track('outreach_write_ask')
  app.requestWriteAccess((granted) => {
    track('outreach_write_answer', { granted })
    if (granted) void meFetch('/api/v1/me/outreach/write-access/', { method: 'POST' })
  })
}

// A link from an outreach message (`src-o-…`): once per launch, sign in and ask the server what to offer. Null for any
// other link, a failure, or a forwarded link (not the recipient), which shows the public page alone.
export function loadOutreachLanding(): Promise<OutreachLanding | null> {
  landing ??= (async () => {
    const src = getLinkSource()
    if (!src.startsWith('o-')) return null
    const result = await meFetch<OutreachLanding>('/api/v1/me/outreach/landing/', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ src }) })
    if (!result.ok) return null
    track('outreach_landing', { kind: result.data.kind, recipient: result.data.is_recipient })
    if (!result.data.is_recipient) return null
    if (result.data.ask_write_access) askWriteAccess()
    return result.data
  })()
  return landing
}

export function confirmOutreachAlert(id: number): Promise<ApiResult<OutreachAlert>> {
  return meFetch<OutreachAlert>(`/api/v1/me/outreach/alerts/${id}/confirm/`, { method: 'POST' })
}

// Tests only.
export function resetOutreachLanding() {
  landing = null
}
