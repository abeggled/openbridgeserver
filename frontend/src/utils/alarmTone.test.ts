// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
})

function fakeAudio() {
  const nodes = { oscillators: 0, started: [] as number[] }
  class FakeContext {
    currentTime = 5
    destination = {}
    createOscillator() {
      nodes.oscillators++
      return { type: '', frequency: { value: 0 }, connect: (n: unknown) => n, start: (t: number) => nodes.started.push(t), stop: vi.fn() }
    }
    createGain() {
      return { gain: { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() }, connect: (n: unknown) => n }
    }
  }
  return { nodes, FakeContext }
}

describe('playAlarmTone', () => {
  it('plays three short beeps and reuses the audio context', async () => {
    const { nodes, FakeContext } = fakeAudio()
    const ctor = vi.fn(function () { return new FakeContext() })
    vi.stubGlobal('AudioContext', ctor)
    const { playAlarmTone } = await import('./alarmTone')

    playAlarmTone()
    playAlarmTone()

    expect(nodes.oscillators).toBe(6)
    expect(nodes.started.slice(0, 3)).toEqual([5, 5.35, 5.7])
    expect(ctor).toHaveBeenCalledTimes(1)
  })

  it('uses the webkit prefix where needed', async () => {
    const { nodes, FakeContext } = fakeAudio()
    vi.stubGlobal('AudioContext', undefined)
    vi.stubGlobal('webkitAudioContext', FakeContext)
    const { playAlarmTone } = await import('./alarmTone')

    playAlarmTone()
    expect(nodes.oscillators).toBe(3)
  })

  it('stays silent without audio support or when the browser refuses', async () => {
    vi.stubGlobal('AudioContext', undefined)
    vi.stubGlobal('webkitAudioContext', undefined)
    const { playAlarmTone } = await import('./alarmTone')
    expect(() => playAlarmTone()).not.toThrow()

    vi.resetModules()
    vi.stubGlobal('AudioContext', vi.fn(() => { throw new Error('denied') }))
    const again = await import('./alarmTone')
    expect(() => again.playAlarmTone()).not.toThrow()
  })
})
