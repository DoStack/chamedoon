import type { components } from './schema.gen'

type Schemas = components['schemas']
export type Card = Schemas['PublicCard']
export type Listing = Schemas['PublicListing']
export type ViewerListing = Schemas['ViewerListing']
export type Contact = Schemas['Contact']
export type GateChannel = Schemas['GateChannel']
export type Meta = Schemas['Meta']
export type Exchange = Schemas['Exchange']
export type Prefs = Schemas['Prefs']
export type Cities = Schemas['Cities']
export type CountryRow = Schemas['CountryRow']
export type CityRow = Schemas['CityRow']
export type DeepLink = Schemas['DeepLink']
export type Manifest = Schemas['Manifest']
export type SnapshotFile = Schemas['SnapshotFile']
export type Feed = Schemas['Feed']
export type Waitlist = Schemas['Waitlist']
export type Availability = Schemas['Availability']

// What this bundle was built against. A newer one in meta or the manifest shows the update sheet.
export const API_VERSION = 1
export const SNAPSHOT_SCHEMA = 1
