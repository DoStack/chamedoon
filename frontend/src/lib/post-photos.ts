import { useCallback, useEffect, useRef, useState } from 'react'
import { showToast } from '@/components/app-toast'
import type { PhotoUploadTicket, UploadedPhoto } from '@/api/post-types'
import { meFetch, type ApiCode } from './api'
import type { DraftPhoto } from './post-draft'
import { closePosting } from './posting'
import { track } from './report'

export const MAX_PHOTOS = 6
export const MAX_PHOTO_BYTES = 3 * 1024 * 1024
const AS_IS_BYTES = 1_500_000
const SENT_AS_IS = new Set(['image/jpeg', 'image/png', 'image/webp'])
const LONG_EDGE = 1600
const sizeBucket = (bytes: number) => (bytes < 500_000 ? 's' : bytes < AS_IS_BYTES ? 'm' : bytes < MAX_PHOTO_BYTES ? 'l' : 'xl')

export type Prepared = { blob: Blob; type: string } | { error: 'not_image' | 'too_big' }

async function shrink(file: File): Promise<Blob | null> {
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, LONG_EDGE / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    canvas.getContext('2d')?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    return await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82))
  } catch {
    return null
  }
}

// Small files of a type the server takes go as they are. The server checks the bytes either way.
export async function prepareFile(file: File, compress: (file: File) => Promise<Blob | null> = shrink): Promise<Prepared> {
  if (file.type && !file.type.startsWith('image/')) return { error: 'not_image' }
  if (SENT_AS_IS.has(file.type) && file.size <= AS_IS_BYTES) return { blob: file, type: file.type }
  const blob = await compress(file)
  if (!blob) return { error: 'not_image' }
  return blob.size > MAX_PHOTO_BYTES ? { error: 'too_big' } : { blob, type: 'image/jpeg' }
}

// XMLHttpRequest for the progress on the tile, which fetch does not give for an upload. `headers` are signed, so they
// go exactly as they came. No Authorization: this goes to storage, not to the API.
export function putFile(ticket: Pick<PhotoUploadTicket, 'upload_url' | 'headers'>, blob: Blob, onProgress: (percent: number) => void, signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', ticket.upload_url)
    for (const [name, value] of Object.entries(ticket.headers)) xhr.setRequestHeader(name, value)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => resolve(xhr.status >= 200 && xhr.status < 300)
    xhr.onerror = () => resolve(false)
    xhr.onabort = () => resolve(false)
    signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(blob)
  })
}

export type TileStatus = 'uploading' | 'ready' | 'failed' | 'bad'
export type Tile = { key: string; status: TileStatus; progress: number; photo?: DraftPhoto; file?: File; photoId?: string; message?: string }

const FAILED = 'عکس ارسال نشد'
const NOT_IMAGE = 'این فایل عکس نیست'
const TOO_BIG = 'حجم یک عکس بیشتر از ۳ مگابایت است'
const TOO_MANY = 'حداکثر ۶ عکس'

function outcomeOf(code: ApiCode): 'bad' | 'big' | 'many' | 'off' | 'failed' {
  switch (code) {
    case 'not_image':
      return 'bad'
    case 'too_big':
      return 'big'
    case 'too_many':
      return 'many'
    case 'feature_off':
      return 'off'
    default:
      return 'failed'
  }
}

const json = { 'Content-Type': 'application/json' }

function photoPath(id: string) {
  return `/api/v1/me/photos/${id}/`
}

// F18#5, #12, #30: one tile per photo, each answering for itself, so one bad file fails only its tile.
export function usePhotoTiles(initial: DraftPhoto[], onReady: (photos: DraftPhoto[]) => void) {
  const [tiles, setTiles] = useState<Tile[]>(() => initial.map((photo) => ({ key: photo.photo_id, status: 'ready', progress: 100, photo })))
  const queue = useRef<Promise<void>>(Promise.resolve())
  const aborts = useRef(new Map<string, AbortController>())
  const counter = useRef(0)
  const latest = useRef(tiles)
  latest.current = tiles
  const notify = useRef(onReady)
  notify.current = onReady

  const patch = useCallback((key: string, change: Partial<Tile>) => {
    setTiles((rows) => rows.map((row) => (row.key === key ? { ...row, ...change } : row)))
  }, [])

  const remember = (rows: Tile[]) => rows.flatMap((row) => (row.status === 'ready' && row.photo ? [row.photo] : []))
  const ready = remember(tiles)
  const readyIds = ready.map((photo) => photo.photo_id).join()
  useEffect(() => {
    notify.current(remember(latest.current))
  }, [readyIds])

  const confirm = useCallback(
    async (key: string, photoId: string) => {
      patch(key, { progress: 100 })
      const done = await meFetch<UploadedPhoto>(photoPath(photoId), { method: 'POST' })
      if (done.ok) {
        track('post_photo_ready')
        patch(key, { status: 'ready', progress: 100, photo: { photo_id: done.data.photo_id, url: done.data.url, thumb: done.data.thumb }, message: undefined })
        return
      }
      if (done.code === 'photo_gone' || done.code === 'upload_failed') patch(key, { photoId: undefined })
      return refuse(key, done.code)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [patch],
  )

  function refuse(key: string, code: ApiCode) {
    track('post_photo_failed', { code })
    switch (outcomeOf(code)) {
      case 'bad':
        patch(key, { status: 'bad', message: NOT_IMAGE, photoId: undefined })
        break
      case 'big':
        patch(key, { status: 'failed', message: '', photoId: undefined })
        showToast({ message: TOO_BIG, icon: 'alert', id: 'post-photo' })
        break
      case 'many':
        setTiles((rows) => rows.filter((row) => row.key !== key))
        showToast({ message: TOO_MANY, icon: 'alert', id: 'post-photo' })
        break
      case 'off':
        closePosting('posting')
        patch(key, { status: 'failed', message: FAILED })
        break
      default:
        patch(key, { status: 'failed', message: FAILED })
    }
  }

  const upload = useCallback(
    async (key: string, file: File) => {
      const prepared = await prepareFile(file)
      if ('error' in prepared) return refuse(key, prepared.error)
      const ticket = await meFetch<PhotoUploadTicket>('/api/v1/me/photos/', { method: 'POST', headers: json, body: JSON.stringify({ content_type: prepared.type, size: prepared.blob.size }) })
      if (!ticket.ok) return refuse(key, ticket.code)
      patch(key, { photoId: ticket.data.photo_id })
      const abort = new AbortController()
      aborts.current.set(key, abort)
      const sent = await putFile(ticket.data, prepared.blob, (progress) => patch(key, { progress }), abort.signal)
      aborts.current.delete(key)
      if (abort.signal.aborted) return
      if (!sent) {
        track('post_photo_failed', { code: 'put_failed' })
        return patch(key, { status: 'failed', message: FAILED })
      }
      await confirm(key, ticket.data.photo_id)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [confirm, patch],
  )

  const enqueue = useCallback((run: () => Promise<void>) => {
    queue.current = queue.current.then(run, run)
  }, [])

  const addFiles = useCallback(
    (files: Iterable<File>) => {
      const list = [...files]
      const room = MAX_PHOTOS - latest.current.length
      if (list.length > room) track('post_photo_failed', { code: 'too_many' })
      if (list.length > room) showToast({ message: TOO_MANY, icon: 'alert', id: 'post-photo' })
      const fresh: Tile[] = list.slice(0, Math.max(0, room)).map((file) => {
        counter.current += 1
        return { key: `new-${counter.current}`, status: 'uploading', progress: 0, file }
      })
      setTiles((rows) => [...rows, ...fresh])
      for (const tile of fresh) {
        track('post_photo_add', { count: latest.current.length + fresh.length, size: sizeBucket(tile.file!.size) })
        enqueue(() => upload(tile.key, tile.file!))
      }
    },
    [enqueue, upload],
  )

  const retry = useCallback(
    (key: string) => {
      const tile = latest.current.find((row) => row.key === key)
      if (!tile) return
      if (!tile.file && !tile.photoId) return
      track('post_photo_retry')
      patch(key, { status: 'uploading', progress: 0, message: undefined })
      enqueue(() => (tile.photoId ? confirm(key, tile.photoId) : upload(key, tile.file!)))
    },
    [confirm, enqueue, patch, upload],
  )

  const remove = useCallback((key: string) => {
    const tile = latest.current.find((row) => row.key === key)
    track('post_photo_remove', { count: latest.current.length - 1 })
    aborts.current.get(key)?.abort()
    setTiles((rows) => rows.filter((row) => row.key !== key))
    const id = tile?.photo?.photo_id ?? tile?.photoId
    // Removing what is not there is a success, and an upload left behind is cleaned up by the server.
    if (id) void meFetch(photoPath(id), { method: 'DELETE' })
  }, [])

  // `photo_gone` at publish names no photo, so each one answers again. A retry of confirm is safe.
  const recheck = useCallback(async () => {
    for (const tile of latest.current) {
      if (tile.status !== 'ready' || !tile.photo) continue
      const check = await meFetch<UploadedPhoto>(photoPath(tile.photo.photo_id), { method: 'POST' })
      if (!check.ok && check.code === 'photo_gone') patch(tile.key, { status: tile.file ? 'failed' : 'bad', message: FAILED, photo: undefined, photoId: undefined })
    }
  }, [patch])

  const busy = tiles.some((tile) => tile.status === 'uploading')
  const blocked = busy || tiles.some((tile) => tile.status === 'failed' || tile.status === 'bad')
  return { tiles, addFiles, retry, remove, recheck, blocked, ready }
}
