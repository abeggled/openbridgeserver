// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { isEmbedded } from './embed'

afterEach(() => {
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
  vi.restoreAllMocks()
})

describe('isEmbedded', () => {
  it('is false for a normal Visu', () => {
    expect(isEmbedded()).toBe(false)
  })

  it('is true with ?embed=1 and stays true while navigating inside the frame', () => {
    window.history.replaceState(null, '', '/visu/page-1?embed=1')
    expect(isEmbedded()).toBe(true)

    window.history.replaceState(null, '', '/visu/page-2')
    expect(isEmbedded()).toBe(true)
  })

  it('falls back to the URL when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    window.history.replaceState(null, '', '/visu/page-1?embed=1')
    expect(isEmbedded()).toBe(true)
    window.history.replaceState(null, '', '/visu/page-1')
    expect(isEmbedded()).toBe(false)
  })
})
