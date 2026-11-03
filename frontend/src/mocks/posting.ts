// P4 answers, as the server gives them once its flags are on. From the file backend/tests/test_p4_fixtures.py holds to the server.
import type { MineForm, MineList, MineRow, PostParse, PostRequest, PostResult, UploadedPhoto } from '../api/post-types'
import type { Meta } from '../api/types'
import P4 from '../../../backend/home_api/testdata/p4_fixtures.json' with { type: 'json' }

export type PostingMock = { status: number; body: unknown }

type Fixtures = typeof P4
export const p4 = <K extends keyof Fixtures>(name: K): Fixtures[K]['body'] => structuredClone(P4[name].body)

export const META_POSTING = () => p4('meta_on') as unknown as Meta
export const META_MANUAL = () => p4('meta_manual') as unknown as Meta
export const PARSE = (name: 'parse_full' | 'parse_incomplete' | 'parse_nothing' | 'parse_wanted' | 'parse_other_city' | 'parse_sublet' | 'parse_german' | 'parse_contact') => p4(name) as unknown as PostParse
export const ROW = (name: 'mine_live' | 'mine_review' | 'mine_rejected_price' | 'mine_rejected_other' | 'mine_closed' | 'mine_expired' | 'mine_check' | 'mine_wanted' | 'mine_wanted_check') => p4(name) as unknown as MineRow
export const REQUEST = (name: 'request_offer' | 'request_wanted') => p4(name) as unknown as PostRequest
export const FORM = (name: 'form_offer' | 'form_wanted') => p4(name) as unknown as MineForm

const ok = (body: unknown, status = 200): PostingMock => ({ status, body })

let uploads = 0

function ticket() {
  uploads += 1
  const body = p4('photo_ticket')
  return { ...body, photo_id: body.photo_id.replace(/.{4}$/, String(1000 + uploads)) }
}

// Null: not one of the P4 paths.
export function answerPosting(method: string, pathname: string): PostingMock | null {
  if (pathname === '/api/v1/me/posts/parse/') return ok(PARSE('parse_full'))
  if (pathname === '/api/v1/me/photos/') return ok(ticket(), 201)
  const photo = pathname.match(/^\/api\/v1\/me\/photos\/([0-9a-f-]+)\/$/)
  if (photo) return method === 'DELETE' ? ok(null, 204) : ok({ ...(p4('uploaded_photo') as unknown as UploadedPhoto), photo_id: photo[1] })
  if (pathname === '/api/v1/me/listings/') return method === 'POST' ? ok(p4('post_live') as unknown as PostResult, 201) : ok(p4('mine_list') as unknown as MineList)
  const form = pathname.match(/^\/api\/v1\/me\/listings\/(\d+)\/form\/$/)
  if (form) return ok(FORM('form_offer'))
  const status = pathname.match(/^\/api\/v1\/me\/listings\/(\d+)\/status\/$/)
  if (status) return ok(ROW('mine_live'))
  const listing = pathname.match(/^\/api\/v1\/me\/listings\/(\d+)\/$/)
  if (listing && method === 'PATCH') return ok(p4('post_live'))
  if (listing && method === 'DELETE') return ok(null, 204)
  return null
}
