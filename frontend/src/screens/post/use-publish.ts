import { createElement, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { MineForm, PostResult } from '@/api/post-types'
import type { JoinCheck, JoinChat, ViewerListing } from '@/api/types'
import { JoinSheet } from '@/components/join-sheet'
import { needsInternetToast, rateLimitedToast, showToast } from '@/components/app-toast'
import { meFetch, type ApiFailure, type ApiResult } from '@/lib/api'
import { clearDraft, clearFresh, toRequest, type Draft } from '@/lib/post-draft'
import { closePosting } from '@/lib/posting'
import { track } from '@/lib/report'

export type Duplicate = { id: number; owner: 'self' | 'other'; viewer: ViewerListing | null }
export type PublishPhase = 'idle' | 'sending' | 'failed' | 'capped'

const JSON_HEADERS = { 'Content-Type': 'application/json' }
export const MINE_KEY = ['mine']
// Publishing reaches storage and the channel. A retry is safe either way.
const SEND_TIMEOUT_MS = 30_000
// The server gives Telegram two seconds per chat. Past this the post goes out anyway: our slowness never blocks a poster.
const JOIN_CHECK_TIMEOUT_MS = 5_000

type JoinState = { chats: JoinChat[]; stillOut: boolean; checking: boolean }

type Options = {
  draft: Draft
  setDraft: (next: Draft) => void
  hasUsername: boolean
  editing?: { id: number; updatedAt: string } | null
  // A new post started beside a saved draft: only its own copy goes when it is published.
  fresh?: boolean
  onDone: (result: PostResult) => void
  // `fields` of `validation_failed`, for the screen to send the person to the step that holds the field.
  onFields: (fields: Record<string, string[]>) => void
  onPhotoGone?: () => void
  onEditRefused?: (code: 'edit_conflict' | 'listing_closed' | 'listing_gone') => void
}

// F18#16, #21 to #23, F19#8 and #9, F20#2. The one way a post or an edit leaves the phone.
export function usePublish({ draft, setDraft, hasUsername, editing, fresh = false, onDone, onFields, onPhotoGone, onEditRefused }: Options) {
  const [phase, setPhase] = useState<PublishPhase>('idle')
  const [duplicate, setDuplicate] = useState<Duplicate | null>(null)
  const [join, setJoin] = useState<JoinState | null>(null)
  const sending = useRef(false)
  // A retry can run from a toast's old closure, so it reads the draft as it is now and the one key this draft has.
  // A new draft is a new mount of the form, and only that starts a new key.
  const latest = useRef(draft)
  latest.current = draft
  const keyRef = useRef(draft.idempotencyKey)
  const client = useQueryClient()

  const finish = (result: PostResult, kept: Draft) => {
    track('post_done', { kind: kept.kind, outcome: result.post_outcome, editing: Boolean(editing) })
    void client.invalidateQueries({ queryKey: MINE_KEY })
    if (fresh) clearFresh()
    else if (!editing) clearDraft(kept.kind)
    if (result.photos_stored < kept.photos.length) showToast({ message: 'بعضی عکس‌ها ذخیره نشد', icon: 'alert' })
    setPhase('idle')
    onDone(result)
  }

  const refused = async (failure: ApiFailure, retry: () => void) => {
    track('post_refused', { kind: draft.kind, code: failure.code })
    switch (failure.code) {
      case 'validation_failed':
        for (const [field, codes] of Object.entries(failure.fields ?? {})) track('post_validation_failed', { kind: draft.kind, step: 'publish', field, code: codes[0] })
        setPhase('idle')
        onFields(failure.fields ?? {})
        break
      case 'duplicate_listing': {
        setPhase('idle')
        const id = failure.duplicateOf ?? 0
        const viewer = id ? await meFetch<ViewerListing>(`/api/v1/me/listings/${id}/`) : null
        setDuplicate({ id, owner: failure.duplicateOwner === 'other' ? 'other' : 'self', viewer: viewer?.ok ? viewer.data : null })
        break
      }
      case 'photo_gone':
        setPhase('idle')
        onPhotoGone?.()
        break
      case 'post_cap':
        setPhase('capped')
        break
      case 'rate_limited':
        setPhase('idle')
        rateLimitedToast(retry, 'post-rate')
        break
      case 'edit_conflict':
      case 'listing_closed':
      case 'listing_gone':
        setPhase('idle')
        onEditRefused?.(failure.code)
        break
      case 'feature_off':
        closePosting('posting')
        setPhase('failed')
        break
      case 'offline':
        setPhase('failed')
        needsInternetToast()
        break
      default:
        setPhase('failed')
    }
  }

  const send = async (forceNew: boolean, target = editing) => {
    if (sending.current) return
    sending.current = true
    track('post_submit', { kind: latest.current.kind, editing: Boolean(target), force_new: forceNew })
    setPhase('sending')
    keyRef.current ??= crypto.randomUUID()
    const kept = { ...latest.current, idempotencyKey: keyRef.current }
    if (latest.current.idempotencyKey !== keyRef.current) setDraft(kept)
    const body = { ...toRequest(kept, hasUsername), force_new: forceNew }
    const result: ApiResult<PostResult> = target
      ? await meFetch<PostResult>(`/api/v1/me/listings/${target.id}/`, { method: 'PATCH', timeoutMs: SEND_TIMEOUT_MS, headers: JSON_HEADERS, body: JSON.stringify({ ...body, base_updated_at: target.updatedAt }) })
      : await meFetch<PostResult>('/api/v1/me/listings/', { method: 'POST', timeoutMs: SEND_TIMEOUT_MS, headers: { ...JSON_HEADERS, 'Idempotency-Key': keyRef.current }, body: JSON.stringify(body) })
    sending.current = false
    if (result.ok) finish(result.data, kept)
    else await refused(result, () => void send(forceNew, target))
  }

  // F18#16 «به‌روز کردن آگهی قبلی»: this draft replaces that listing, through the same checks as any edit.
  const updateOld = async (id: number) => {
    setDuplicate(null)
    setPhase('sending')
    const form = await meFetch<MineForm>(`/api/v1/me/listings/${id}/form/`)
    if (!form.ok) return setPhase('failed')
    await send(true, { id, updatedAt: form.data.updated_at })
  }

  // Before a new post: the city's channel and group. Anything but a clear «not a member» lets the post through.
  const joinCheck = async (): Promise<JoinChat[]> => {
    const city = latest.current.city
    if (!city) return []
    const result = await meFetch<JoinCheck>('/api/v1/me/join-check/', {
      method: 'POST',
      timeoutMs: JOIN_CHECK_TIMEOUT_MS,
      headers: JSON_HEADERS,
      body: JSON.stringify({ country: city.country, city: city.city }),
    })
    if (!result.ok) {
      track('join_check_failed', { moment: 'post', code: result.code })
      return []
    }
    if (result.data.gate === 'failed_open') track('join_check_failed', { moment: 'post', code: 'telegram' })
    return result.data.chats
  }

  const publishNew = async () => {
    if (editing || sending.current) return void send(false)
    setPhase('sending')
    const chats = await joinCheck()
    if (!chats.length) return void send(false)
    setPhase('idle')
    track('join_gate_shown', { moment: 'post', kinds: chats.map((chat) => chat.kind).join(',') })
    setJoin({ chats, stillOut: false, checking: false })
  }

  const confirmJoin = async (via: 'tap' | 'focus') => {
    if (!join || join.checking) return
    setJoin({ ...join, checking: via === 'tap' })
    const chats = await joinCheck()
    if (chats.length) {
      if (via === 'tap') track('gate_still_out', { moment: 'post' })
      return setJoin({ chats, stillOut: via === 'tap', checking: false })
    }
    track('join_confirmed', { moment: 'post', via })
    setJoin(null)
    void send(false)
  }

  const city = latest.current.city
  const joinSheet = createElement(JoinSheet, {
    open: Boolean(join),
    moment: 'post',
    chats: join?.chats ?? [],
    place: city?.name_fa || city?.city || '',
    checking: Boolean(join?.checking),
    stillOut: Boolean(join?.stillOut),
    onConfirm: (via) => void confirmJoin(via),
    onClose: () => {
      track('join_gate_skipped', { moment: 'post' })
      setJoin(null)
    },
  })

  return {
    phase,
    duplicate,
    joinSheet,
    publish: () => void publishNew(),
    publishAnyway: () => {
      setDuplicate(null)
      void send(true)
    },
    updateOld: (id: number) => void updateOld(id),
    closeDuplicate: () => setDuplicate(null),
    reset: () => setPhase('idle'),
  }
}
