import { describe, it, expect } from 'vitest'
import { NODE_TINT_ALPHA, nodeTint } from '@/utils/logicNodeSurface'

describe('logicNodeSurface', () => {
  it('exposes the shared tint alpha suffix', () => {
    expect(NODE_TINT_ALPHA).toBe('12')
  })

  it('appends the tint alpha to a category colour', () => {
    expect(nodeTint('#1d4ed8')).toBe('#1d4ed812')
  })

  it('keeps the colour itself untouched so categories stay recognisable', () => {
    expect(nodeTint('#ca8a04').startsWith('#ca8a04')).toBe(true)
  })
})
