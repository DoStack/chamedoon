import type { components } from '@/api/courier-schema.gen'

type Schemas = components['schemas']
export type Meta = Schemas['Meta']
export type Exchange = Schemas['Exchange']
export type Me = Schemas['Me']
export type Guide = Schemas['Guide']
export type Place = Schemas['Place']
export type RequestCard = Schemas['RequestCard']

// What this bundle was built against. A newer `min_api_version` in meta shows the update sheet (C18.5).
export const API_VERSION = 1
