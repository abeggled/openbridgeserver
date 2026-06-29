import { describe, expect, it } from 'vitest'

import { applyValueMap } from './transformation'

describe('applyValueMap', () => {
  it('matches string keys case-insensitively after exact lookup misses', () => {
    expect(applyValueMap({ on: 'true', off: 'false' }, 'OFF')).toBe('false')
    expect(applyValueMap({ on: 'true', off: 'false' }, 'On')).toBe('true')
  })

  it('prefers exact keys over case-insensitive fallback keys', () => {
    expect(applyValueMap({ off: 'lowercase', OFF: 'uppercase' }, 'OFF')).toBe('uppercase')
  })

  it('falls back from boolean keys to numeric keys', () => {
    expect(applyValueMap({ '1': 'on', '0': 'off' }, true)).toBe('on')
    expect(applyValueMap({ '1': 'on', '0': 'off' }, false)).toBe('off')
  })

  it('matches uppercase boolean text keys case-insensitively', () => {
    expect(applyValueMap({ TRUE: 'on', FALSE: 'off' }, true)).toBe('on')
    expect(applyValueMap({ TRUE: 'on', FALSE: 'off' }, false)).toBe('off')
  })

  it('prefers case-insensitive boolean text keys over numeric fallback keys', () => {
    expect(applyValueMap({ '1': 'numeric', TRUE: 'text' }, true)).toBe('text')
  })

  it('returns the original value when no key matches', () => {
    expect(applyValueMap({ off: 'false' }, 'standby')).toBe('standby')
  })
})
