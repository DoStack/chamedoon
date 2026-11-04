import { describe, expect, it } from 'vitest'
import { postingOn, smartPostingOn } from './posting'

describe('the P4 flags', () => {
  it('missing means off', () => {
    expect(postingOn(null)).toBe(false)
    expect(postingOn({})).toBe(false)
    expect(smartPostingOn({})).toBe(false)
  })

  it('only a true opens it', () => {
    const features = (posting: boolean, smart_posting: boolean) => ({ features: { alerts: false, account: false, posting, smart_posting } })
    expect(postingOn(features(true, false))).toBe(true)
    expect(smartPostingOn(features(true, false))).toBe(false)
    expect(smartPostingOn(features(true, true))).toBe(true)
    expect(postingOn(features(false, true))).toBe(false)
    expect(smartPostingOn(features(false, true))).toBe(false)
  })
})
