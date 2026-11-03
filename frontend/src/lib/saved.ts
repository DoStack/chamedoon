import { useSyncExternalStore } from 'react'
import type { SavedIds, SavedList, SavedRow } from '@/api/types'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { getSession, meFetch, subscribeSession, type ApiFailure, type ApiResult } from './api'
import { queryClient } from './query'
import { track } from './report'

export const NOTE_MAX = 200
export const SAVED_KEY = ['saved']

const STORE = 'chamedoon.saved_ids'

function readStore() {
  try {
    const ids: unknown = JSON.parse(localStorage.getItem(STORE) ?? '[]')
    return new Set(Array.isArray(ids) ? ids.filter(Number.isInteger) : [])
  } catch {
    return new Set<number>()
  }
}

// Browsing alone never exchanges (ADR-3), so the device keeps the ids between launches, and they refresh once a session
// exists. `removed` hides a row at once, before the list reloads, and an undo brings it back.
let ids = readStore() as Set<number>
const removed = new Set<number>()
const listeners = new Set<() => void>()
let version = 0
let writes = 0
let refreshAgain = false

function changed() {
  version += 1
  try {
    localStorage.setItem(STORE, JSON.stringify([...ids]))
  } catch {
    // No storage: the marks last this session.
  }
  listeners.forEach((listener) => listener())
}

function setMark(id: number, on: boolean) {
  if (on) {
    ids.add(id)
    removed.delete(id)
  } else {
    ids.delete(id)
    removed.add(id)
  }
  changed()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => void listeners.delete(listener)
}

export function useIsSaved(id: number) {
  return useSyncExternalStore(subscribe, () => ids.has(id))
}

export function useRemovedVersion() {
  return useSyncExternalStore(subscribe, () => version)
}

// A row on the saved list is saved, whatever the device remembered.
export function rememberRows(rows: SavedRow[]) {
  for (const row of rows) {
    if (isRemoved(row)) continue
    ids.add(row.listing_id)
    if (row.card) ids.add(row.card.id)
  }
  changed()
}

export function isRemoved(row: SavedRow) {
  return removed.has(row.listing_id) || (row.card ? removed.has(row.card.id) : false)
}

async function refreshIds() {
  if (!getSession()) return
  const started = writes
  const result = await meFetch<SavedIds>('/api/v1/me/saved/ids/')
  // A mark made meanwhile is newer than this answer, so ask again once the writes are done.
  if (writes !== started || queues.size) {
    refreshAgain = true
    return
  }
  if (!result.ok || !Array.isArray(result.data?.listing_ids)) return
  ids = new Set(result.data.listing_ids)
  changed()
}

subscribeSession(() => void refreshIds())

const put = (id: number, note?: string) =>
  meFetch<SavedRow>(`/api/v1/me/saved/${id}/`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(note === undefined ? {} : { note }) })
const del = (id: number) => meFetch<null>(`/api/v1/me/saved/${id}/`, { method: 'DELETE' })

// One write at a time per listing, so an undo cannot overtake the write it undoes.
const queues = new Map<number, Promise<unknown>>()

function queued<T>(id: number, write: () => Promise<ApiResult<T>>) {
  writes += 1
  const next = (queues.get(id) ?? Promise.resolve()).then(write)
  queues.set(id, next)
  return next
}

export function isWriting(id: number) {
  return queues.has(id)
}

function settle(id: number, promise: Promise<unknown>) {
  if (queues.get(id) === promise) queues.delete(id)
  if (refreshAgain && !queues.size) {
    refreshAgain = false
    void refreshIds()
  }
}

// These replace the whole app through onApiFailure, so no toast is needed.
const APP_WIDE = new Set(['maintenance', 'user_blocked', 'session_expired', 'auth_invalid', 'auth_outside', 'miniapp_off'])

function failed(failure: ApiFailure, retry: () => void) {
  track('bookmark_failed', { code: failure.code })
  if (APP_WIDE.has(failure.code)) return
  if (failure.code === 'listing_gone') return void showToast({ id: 'saved', message: 'این آگهی دیگر نیست', icon: 'alert' })
  if (failure.code === 'rate_limited') return rateLimitedToast(retry, 'saved')
  saveFailedToast(retry, 'saved') // F14#7
}

function cachedNote(id: number) {
  const pages = queryClient.getQueryData<{ pages: SavedList[] }>(SAVED_KEY)?.pages ?? []
  return pages.flatMap((page) => page.results).find((row) => row.listing_id === id || row.card?.id === id)?.note
}

// F14#1: optimistic, with undo. F14#7: back to how it was, with retry.
export function mark(id: number, note?: string, quiet = false) {
  setMark(id, true)
  track('bookmark_set', { on: true })
  if (!quiet) showToast({ id: 'saved', message: 'نشان شد', icon: 'bookmark', action: { label: 'برگردان', onClick: () => unmark(id, true) } })
  const write = queued(id, () => put(id, note))
  void write.then((result) => {
    settle(id, write)
    if (result.ok) return void queryClient.invalidateQueries({ queryKey: SAVED_KEY })
    setMark(id, false)
    failed(result, () => mark(id, note))
  })
}

// F14#5: the note goes with the mark, so it is read first for the undo. `quiet`: this is itself an undo.
export function unmark(id: number, quiet = false, undoable = true) {
  let note = cachedNote(id)
  setMark(id, false)
  track('bookmark_set', { on: false })
  if (!quiet) {
    showToast({
      id: 'saved',
      message: 'از نشان‌شده‌ها برداشته شد',
      icon: 'bookmarkOff',
      action: undoable ? { label: 'برگردان', onClick: () => mark(id, note, true) } : undefined,
    })
  }
  const write = queued(id, async () => {
    if (note === undefined && undoable) {
      const row = await put(id)
      if (row.ok) note = row.data.note
    }
    return del(id)
  })
  void write.then((result) => {
    settle(id, write)
    if (result.ok) return void queryClient.invalidateQueries({ queryKey: SAVED_KEY })
    setMark(id, true)
    failed(result, () => unmark(id, quiet, undoable))
  })
}

export function toggle(id: number) {
  if (isWriting(id)) return // A double tap is one tap.
  if (ids.has(id)) unmark(id)
  else mark(id)
}

// F14#3, #4, #9. The sheet stays open on a failure, with what was typed.
export async function saveNote(id: number, before: string, text: string) {
  const note = text.trim()
  const result = await put(id, note)
  if (!result.ok) {
    track('bookmark_failed', { code: result.code })
    if (!APP_WIDE.has(result.code)) showToast({ id: 'saved', message: 'ذخیره نشد', icon: 'alert', overSheet: true })
    return false
  }
  track('note_saved', { blank: !note })
  void queryClient.invalidateQueries({ queryKey: SAVED_KEY })
  if (note) showToast({ id: 'saved', message: 'یادداشت ذخیره شد', icon: 'check' })
  else if (before) {
    showToast({ id: 'saved', message: 'یادداشت حذف شد', icon: 'note', action: { label: 'برگردان', onClick: () => void saveNote(id, '', before) } })
  }
  return true
}
