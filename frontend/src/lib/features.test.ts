import { describe, expect, it } from 'vitest'
import { featureOn } from './features'

const features = { alerts: true, account: false, posting: false, smart_posting: false }

describe('featureOn', () => {
  it('is off when meta, or its features, are missing', () => {
    expect(featureOn(null, 'alerts')).toBe(false)
    expect(featureOn(undefined, 'account')).toBe(false)
    expect(featureOn({}, 'alerts')).toBe(false)
  })

  it('follows the flag', () => {
    expect(featureOn({ features }, 'alerts')).toBe(true)
    expect(featureOn({ features }, 'account')).toBe(false)
  })
})
