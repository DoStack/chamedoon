import type { components } from './schema.gen'

type Schemas = components['schemas']
export type MineRow = Schemas['MineRow']
export type MineList = Schemas['MineList']
export type MineForm = Schemas['MineForm']
export type PostParse = Schemas['PostParse']
export type PostParsed = Schemas['PostFields']
export type PostResult = Schemas['PostResult']
export type PostRequest = Schemas['PostCreateRequestRequest']
export type PostEditRequest = Schemas['PatchedListingEditRequestRequest']
export type PhotoUploadTicket = Schemas['PhotoUploadTicket']
export type UploadedPhoto = Schemas['UploadedPhoto']
export type CityAreas = Schemas['CityAreas']
export type Availability = Schemas['Availability']
export type RentalMode = NonNullable<PostParsed['rental_mode']>
export type PricePeriod = NonNullable<PostParsed['price_period']>
export type PriceBasis = NonNullable<PostParsed['price_basis']>
export type Tri = NonNullable<PostParsed['bills_included']>
export type Furnished = NonNullable<PostParsed['furnished']>
export type Household = NonNullable<PostRequest['household']>
