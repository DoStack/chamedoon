import { afterEach, describe, expect, it, vi } from 'vitest'
import { FORM, REQUEST } from '@/mocks/posting'
import { amount, applyParsed, clearDraft, draftSummary, ensureKey, fromForm, hasContact, hasContent, loadDraft, missingFields, newDraft, offerArea, pickType, removeContact, saveDraft, textTooShort, toRequest, typeFrom, writeDraft, type Draft } from './post-draft'

const CITY = { country: 'CA', city: 'Toronto', slug: 'toronto', name_fa: 'تورنتو' }
const OFFER: Draft = {
  ...newDraft('offer', CITY),
  text: 'آپارتمان ۱ خوابه مبله نزدیک مترو، قبوض و اینترنت داخل.',
  homeType: 'b1',
  rooms: 1,
  rentalMode: 'long_term',
  price: '۱٬۸۵۰',
  bills: 'yes',
  furnished: 'yes',
  area: 'نورث یورک',
  address: '5000 Yonge St',
  size: '55',
  availableFrom: '2026-12-01',
  minTerm: '12',
  phone: '+1 416 555 0100',
}
const WANTED: Draft = { ...newDraft('wanted', CITY), text: 'دانشجوی ارشد، غیرسیگاری، بدون حیوان، آرام.', wantedTypes: ['room', 'studio'], price: '1600', areas: ['نورث یورک', 'ریچموند هیل'], household: 'one', availableFrom: '2026-12-01', phone: '', showPhone: true }

describe('the request a post sends', () => {
  it('matches the bodies the backend test posts to the server', () => {
    expect(toRequest(OFFER, true)).toEqual(REQUEST('request_offer'))
    expect(toRequest(WANTED, true)).toEqual(REQUEST('request_wanted'))
  })

  it('sends the phone in its field only, and nowhere when switched off', () => {
    const body = toRequest(OFFER, true)
    const { phone, ...rest } = body
    expect(phone).toBe('+1 416 555 0100')
    expect(JSON.stringify(rest)).not.toContain('416')
    expect(toRequest({ ...OFFER, showPhone: false }, true).phone).toBe('')
  })

  it('keeps the phone when it is the only way to reach the person', () => {
    expect(toRequest({ ...OFFER, showPhone: false }, false).phone).toBe('+1 416 555 0100')
  })

  it('never sends an address that is switched off', () => {
    expect(offerArea({ area: 'نورث یورک', address: '5000 Yonge St', showAddress: false })).toBe('نورث یورک')
    expect(offerArea({ area: 'نورث یورک', address: '5000 Yonge St', showAddress: true })).toBe('نورث یورک · 5000 Yonge St')
    expect(offerArea({ area: 'ن'.repeat(90), address: 'x'.repeat(20), showAddress: true })).toHaveLength(96)
  })

  it('a wanted post sends every type, one property_type, and no photos', () => {
    const body = toRequest({ ...WANTED, wantedTypes: ['b1', 'b2'], photos: [{ photo_id: 'a', url: '', thumb: '' }] }, true)
    expect(body.wanted_types).toEqual(['apartment'])
    expect(body.property_type).toBe('apartment')
    expect(body.rooms).toBe(1)
    expect(body.photo_ids).toEqual([])
  })
})

describe('the form back to a draft', () => {
  it('splits the address off the place, and turns its switch on', () => {
    const draft = fromForm(FORM('form_offer'), CITY)
    expect(draft.area).toBe('نورث یورک')
    expect(draft.address).toBe('5000 Yonge St')
    expect(draft.showAddress).toBe(true)
    expect(draft.showPhone).toBe(true)
    expect(draft.homeType).toBe('b1')
    expect(draft.photos).toHaveLength(1)
    expect(draft.stage).toBe('manual')
  })

  it('a wanted form brings back its types and areas', () => {
    const draft = fromForm(FORM('form_wanted'), CITY)
    expect(draft.kind).toBe('wanted')
    expect(draft.wantedTypes).toEqual(['room', 'studio'])
    expect(draft.areas).toEqual(['نورث یورک', 'ریچموند هیل'])
    expect(draft.household).toBe('one')
    expect(draft.showPhone).toBe(false)
  })
})

describe('what was read', () => {
  it('keeps what the text left out', () => {
    const next = applyParsed({ ...newDraft('offer', CITY), price: '900', phone: '+1' }, { area: 'وان' })
    expect(next.price).toBe('900')
    expect(next.area).toBe('وان')
    expect(next.phone).toBe('+1')
  })

  it('does not invent a room count for an apartment', () => {
    expect(typeFrom('apartment', null)).toBeNull()
    expect(typeFrom('apartment', 1)).toBe('b1')
    expect(typeFrom('apartment', 2.5)).toBe('b2')
    expect(typeFrom('apartment', 4)).toBe('b3')
    expect(typeFrom('other', null)).toBeNull()
  })

  it('names what a post still needs', () => {
    expect(missingFields(newDraft('offer', CITY))).toEqual(['type', 'price', 'area'])
    expect(missingFields(newDraft('offer', null))).toEqual(['type', 'price', 'area', 'city'])
    expect(missingFields({ ...OFFER, price: '99999999' })).toEqual(['price'])
    expect(missingFields(OFFER)).toEqual([])
  })

  it('choosing a type sets its rooms', () => {
    expect(pickType(newDraft('offer', CITY), 'b2').rooms).toBe(2)
    expect(pickType({ ...newDraft('offer', CITY), rooms: 2 }, 'room').rooms).toBeNull()
  })
})

describe('numbers and text', () => {
  it('reads Persian and Arabic digits and separators', () => {
    expect(amount('۱٬۸۵۰')).toBe(1850)
    expect(amount('٢٠٠٠')).toBe(2000)
    expect(amount('1,850')).toBe(1850)
    expect(amount('')).toBeNull()
    expect(amount('چند')).toBeNull()
  })

  it('counts letters after trimming', () => {
    expect(textTooShort('   اتاق خالی   ')).toBe(true)
    expect(textTooShort('ا'.repeat(20))).toBe(false)
  })

  it('sums up a draft', () => {
    expect(draftSummary({ ...OFFER, size: '', photos: [{ photo_id: 'a', url: '', thumb: '' }] })).toBe('آپارتمان ۱ خوابه · نورث یورک · ۱ عکس')
    expect(hasContent(newDraft('offer', CITY))).toBe(false)
    expect(hasContent(OFFER)).toBe(true)
  })
})

// The rules of home/contact_text.py, which still decides at publish.
describe('a contact in the text', () => {
  it.each([
    '0416 555 0100',
    '۰۴۱۶ ۵۵۵ ۰۱۰۰',
    '416-555-0142',
    '+49 176 1234567',
    '0176 1234567',
    '030/1234567',
    '@sara_home',
    'sara_home',
    'sara123',
    'https://t.me/sara',
    't.me/sara',
    'wa.me/4915',
    'تلگرام: someone',
    'آیدی someone',
    'id: someone',
  ])('finds %s', (value) => {
    expect(hasContact(`آپارتمان نزدیک مترو ${value} تماس`)).toBe(true)
  })

  it.each(['Yonge و Finch', 'North York', 'Neukölln', 'Apt 302', 'Apt302', '2BR', 'M5V 2T6', 'از ۱ دسامبر', '۱۸۵۰ دلار', '5000 Yonge St'])('leaves %s', (value) => {
    expect(hasContact(`آپارتمان نزدیک مترو ${value}`)).toBe(false)
  })

  it('«حذف از متن» takes out only the contact, even in Persian digits', () => {
    expect(removeContact('آپارتمان مبله ۰۴۱۶ ۵۵۵ ۰۱۰۰ نزدیک مترو')).toBe('آپارتمان مبله نزدیک مترو')
    expect(removeContact('آپارتمان @sara_home مبله')).toBe('آپارتمان مبله')
    expect(removeContact('بدون راه تماس')).toBe('بدون راه تماس')
  })
})

describe('the draft on the device', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
    delete window.Telegram
  })

  it('comes back as it was, and goes when cleared', () => {
    writeDraft(OFFER)
    expect(loadDraft('offer')?.text).toBe(OFFER.text)
    expect(loadDraft('offer')?.savedAt).toBeGreaterThan(0)
    expect(loadDraft('wanted')).toBeNull()
    clearDraft('offer')
    expect(loadDraft('offer')).toBeNull()
  })

  it('a stored value that is not a draft is no draft', () => {
    localStorage.setItem('chamedoon.post.offer', '{bad')
    expect(loadDraft('offer')).toBeNull()
  })

  it('an older draft gets the fields it lacks', () => {
    localStorage.setItem('chamedoon.post.offer', JSON.stringify({ kind: 'offer', text: 'x' }))
    expect(loadDraft('offer')?.wantedTypes).toEqual([])
  })

  it('keeps one key across retries', () => {
    const first = ensureKey(OFFER)
    expect(first.idempotencyKey).toBeTruthy()
    expect(ensureKey(first)).toBe(first)
  })

  it('save fails only when neither copy took it', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full')
    })
    expect(await saveDraft(OFFER)).toBe(false)
    window.Telegram = { WebApp: { isVersionAtLeast: () => true, CloudStorage: { getItem: () => undefined, setItem: (_key, _value, done) => done?.(null, true) } } }
    expect(await saveDraft(OFFER)).toBe(true)
  })

  it('the cloud copy holds no phone', async () => {
    const setItem = vi.fn((_key: string, _value: string, done?: (error: unknown, stored?: boolean) => void) => done?.(null, true))
    window.Telegram = { WebApp: { isVersionAtLeast: () => true, CloudStorage: { getItem: () => undefined, setItem } } }
    await saveDraft(OFFER)
    expect(setItem.mock.calls[0][0]).toBe('chamedoon_post_offer')
    expect(setItem.mock.calls[0][1]).not.toContain('416')
    expect(loadDraft('offer')?.phone).toBe(OFFER.phone)
  })
})
