import { describe, expect, it } from 'vitest'
import { toE164, whatsappUrl } from './phone'

// Same cases as backend/tests/test_phone_contact.py: both sides must agree on the stored number.
describe('toE164', () => {
  it.each([
    ['00989356706868', '', '+989356706868'],
    ['00989356706868', 'CA', '+989356706868'],
    ['+98 935 670 6868', 'DE', '+989356706868'],
    ['۰۰۹۸۹۳۵۶۷۰۶۸۶۸', 'CA', '+989356706868'],
    ['09356706868', 'CA', '+989356706868'],
    ['416-555-0142', 'CA', '+14165550142'],
    ['(416) 555 0142', 'US', '+14165550142'],
    ['1 416 555 0142', 'CA', '+14165550142'],
    ['0151 2345 6789', 'DE', '+4915123456789'],
    ['+49 151 23456789', 'CA', '+4915123456789'],
  ])('%s in %s → %s', (raw, country, want) => expect(toE164(raw, country)).toBe(want))

  it.each(['', 'abc', '@sara_home', '12345', '+1234567890123456', '0151 23', '+1 416 555 0142 ext 3'])('%s is not a number', (raw) => expect(toE164(raw, 'CA')).toBe(''))

  it('wa.me takes digits only', () => expect(whatsappUrl('+1 416 555 0100')).toBe('https://wa.me/14165550100'))
})
