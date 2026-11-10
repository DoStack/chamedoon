// OWNED PIECE: see ./OWNERS.md. The Chamedoon services the header switches between.
import type { Locale } from '@/lib/locale'

export type Service = 'home' | 'courier'

export const NAMES: Record<Service, Record<Locale, string>> = {
  home: { fa: 'خانه', en: 'Housing', de: 'Wohnen' },
  courier: { fa: 'باربری', en: 'Courier', de: 'Kurier' },
}
