// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { isEmbedded } from './embed'

afterEach(() => {
  window.name = ''
  sessionStorage.clear()
  window.history.replaceState(null, '', '/')
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

  it('ignores the old shared sessionStorage flag, so a reloaded main Visu stays normal', () => {
    // Before, the alarm popup frame wrote this into the storage it shares with the main window.
    sessionStorage.setItem('visu_embed', '1')
    window.history.replaceState(null, '', '/visu/page-1')
    expect(isEmbedded()).toBe(false)
  })
})
