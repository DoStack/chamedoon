import { useSyncExternalStore } from 'react'
import type { Card } from '@/api/types'

export type CardOverride = Card & { closed?: boolean }

// The detail is newer than the snapshot. For this session its card follows what the detail said (F04#11).
let overrides: Record<number, CardOverride> = {}
const listeners = new Set<() => void>()

export function isClosedOverride(id: number) {
  return Boolean(overrides[id]?.closed)
}

export function overrideCard(card: CardOverride) {
  overrides = { ...overrides, [card.id]: card }
  listeners.forEach((listener) => listener())
}

export function useCardOverrides() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => overrides,
  )
}
