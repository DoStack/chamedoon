import type { Card } from '@/api/types'

const text = (value: unknown) => (typeof value === 'string' ? value : '')
const date = (value: unknown) => (typeof value === 'string' && !Number.isNaN(Date.parse(value)) ? value : null)

// Screens read these fields with no guard. A bad row costs that field, never the whole city.
export function cleanCard<T extends Card>(card: T): T {
  return { ...card, area: text(card.area), city: text(card.city), available_from: date(card.available_from), available_to: date(card.available_to) }
}

export function cleanCards<T extends Card>(cards: T[]): T[] {
  return cards.filter((card) => Number.isInteger(card?.id)).map(cleanCard)
}
