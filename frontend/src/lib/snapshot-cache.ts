import type { SnapshotFile } from '@/api/types'

const DB = 'chamedoon'
const STORE = 'snapshots'

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

const READ_TIMEOUT_MS = 500

async function read(slug: string): Promise<SnapshotFile | null> {
  const db = await open()
  return new Promise((resolve) => {
    const request = db.transaction(STORE).objectStore(STORE).get(slug)
    request.onsuccess = () => resolve((request.result as SnapshotFile | undefined) ?? null)
    request.onerror = () => resolve(null)
  })
}

// A cache that fails or hangs is the same as an empty cache: the feed loads from the network instead.
export async function readSnapshot(slug: string): Promise<SnapshotFile | null> {
  try {
    return await Promise.race([read(slug), new Promise<null>((resolve) => setTimeout(() => resolve(null), READ_TIMEOUT_MS))])
  } catch {
    return null
  }
}

export async function writeSnapshot(file: SnapshotFile) {
  try {
    const db = await open()
    db.transaction(STORE, 'readwrite').objectStore(STORE).put(file, file.city_slug)
  } catch {
    // Same.
  }
}
