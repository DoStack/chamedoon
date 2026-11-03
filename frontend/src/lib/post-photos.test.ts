import { afterEach, describe, expect, it, vi } from 'vitest'
import { MAX_PHOTO_BYTES, prepareFile, putFile } from './post-photos'

const file = (size: number, type: string) => new File([new Uint8Array(size)], 'a', { type })

describe('what leaves the phone', () => {
  it('a small photo of a type the server takes goes as it is', async () => {
    const shrink = vi.fn()
    const small = file(1000, 'image/jpeg')
    expect(await prepareFile(small, shrink)).toEqual({ blob: small, type: 'image/jpeg' })
    expect(shrink).not.toHaveBeenCalled()
  })

  it('a large photo is compressed, then sized', async () => {
    const out = new Blob([new Uint8Array(1000)])
    expect(await prepareFile(file(2_000_000, 'image/jpeg'), async () => out)).toEqual({ blob: out, type: 'image/jpeg' })
    expect(await prepareFile(file(2_000_000, 'image/jpeg'), async () => new Blob([new Uint8Array(MAX_PHOTO_BYTES + 1)]))).toEqual({ error: 'too_big' })
  })

  it('a type the server does not take goes through the canvas', async () => {
    const out = new Blob([new Uint8Array(1000)])
    expect(await prepareFile(file(1000, 'image/heic'), async () => out)).toEqual({ blob: out, type: 'image/jpeg' })
  })

  it('what is not a picture stops here', async () => {
    const shrink = vi.fn()
    expect(await prepareFile(file(1000, 'application/pdf'), shrink)).toEqual({ error: 'not_image' })
    expect(shrink).not.toHaveBeenCalled()
    expect(await prepareFile(file(2_000_000, 'image/jpeg'), async () => null)).toEqual({ error: 'not_image' })
  })
})

class FakeXhr {
  static last: FakeXhr
  headers: Record<string, string> = {}
  method = ''
  url = ''
  status = 200
  upload: { onprogress?: (event: Partial<ProgressEvent>) => void } = {}
  onload?: () => void
  onerror?: () => void
  onabort?: () => void
  body: unknown
  constructor() {
    FakeXhr.last = this
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  send(body: unknown) {
    this.body = body
  }
  abort() {
    this.onabort?.()
  }
}

describe('the signed PUT', () => {
  afterEach(() => vi.unstubAllGlobals())
  const ticket = { upload_url: 'https://storage.test/x?signed', headers: { 'Content-Type': 'image/jpeg', 'x-amz-acl': 'private' } }

  it('sends the signed headers exactly, no token, and reports progress', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const seen: number[] = []
    const blob = new Blob(['x'])
    const done = putFile(ticket, blob, (percent) => seen.push(percent))
    const xhr = FakeXhr.last
    expect(xhr.method).toBe('PUT')
    expect(xhr.url).toBe(ticket.upload_url)
    expect(xhr.headers).toEqual(ticket.headers)
    expect(xhr.body).toBe(blob)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 3, total: 5 })
    expect(seen).toEqual([60])
    xhr.onload?.()
    expect(await done).toBe(true)
  })

  it('a refused PUT, a lost connection and an abort are all a failed tile', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const refused = putFile(ticket, new Blob(['x']), () => undefined)
    FakeXhr.last.status = 403
    FakeXhr.last.onload?.()
    expect(await refused).toBe(false)
    const lost = putFile(ticket, new Blob(['x']), () => undefined)
    FakeXhr.last.onerror?.()
    expect(await lost).toBe(false)
    const abort = new AbortController()
    const stopped = putFile(ticket, new Blob(['x']), () => undefined, abort.signal)
    abort.abort()
    expect(await stopped).toBe(false)
  })
})
