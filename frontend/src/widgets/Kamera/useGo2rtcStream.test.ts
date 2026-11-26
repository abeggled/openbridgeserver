// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PlayerEvent, PlayerMode, PlayerOptions } from './go2rtcPlayer'

const players = vi.hoisted(() => [] as Array<{ opts: PlayerOptions; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }>)

vi.mock('./go2rtcPlayer', () => ({
  Go2rtcPlayer: class {
    opts: PlayerOptions
    start = vi.fn(async () => {})
    stop = vi.fn()
    constructor(opts: PlayerOptions) {
      this.opts = opts
      players.push(this)
    }
  },
}))

import { RECONNECT_MAX_MS, RECONNECT_MIN_MS, useGo2rtcStream } from './useGo2rtcStream'

const mounted: Array<{ unmount(): void }> = []
let observerCallback: ((entries: Array<{ isIntersecting: boolean }>) => void) | null = null
const observe = vi.fn()
const disconnect = vi.fn()

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

function mountStream(options: { withContainer?: boolean } = {}) {
  const active = ref(true)
  const src = ref('cam_sub')
  const modes = ref<PlayerMode[]>(['webrtc', 'mse'])
  const authKey = ref('jwt-1')
  const resolve = vi.fn(async () => ({ url: 'ws://x' }))
  let api!: ReturnType<typeof useGo2rtcStream>
  const Comp = defineComponent({
    setup() {
      const container = ref<HTMLElement | null>(null)
      const video = ref<HTMLVideoElement | null>(null)
      api = useGo2rtcStream({ container, video, active, src, modes, authKey, resolve })
      return () => h('div', options.withContainer === false ? {} : { ref: container }, [h('video', { ref: video })])
    },
  })
  const wrapper = mount(Comp)
  mounted.push(wrapper)
  return { wrapper, active, src, modes, authKey, resolve, api: () => api }
}

const last = () => players[players.length - 1]
const emit = (event: PlayerEvent) => last().opts.onEvent(event)

beforeEach(() => {
  vi.useFakeTimers()
  players.length = 0
  observerCallback = null
  observe.mockReset()
  disconnect.mockReset()
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: typeof observerCallback) { observerCallback = cb }
    observe = observe
    disconnect = disconnect
  })
  setVisibility('visible')
})

afterEach(() => {
  for (const w of mounted.splice(0)) {
    try { w.unmount() } catch { /* schon abgebaut */ }
  }
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useGo2rtcStream', () => {
  it('starts a player on mount with the current src and modes', async () => {
    const { resolve } = mountStream()
    await nextTick()
    expect(players).toHaveLength(1)
    expect(last().start).toHaveBeenCalled()
    expect(last().opts.modes).toEqual(['webrtc', 'mse'])
    await last().opts.target('mse')
    expect(resolve).toHaveBeenCalledWith('mse', 'cam_sub')
    expect(observe).toHaveBeenCalled()
  })

  it('tracks mode, audio and playing', async () => {
    const { api } = mountStream()
    await nextTick()
    emit({ type: 'mode', mode: 'webrtc' })
    emit({ type: 'audio', hasAudio: true })
    emit({ type: 'playing', mode: 'webrtc' })
    expect(api().mode.value).toBe('webrtc')
    expect(api().hasAudio.value).toBe(true)
    expect(api().playing.value).toBe(true)
    expect(api().failed.value).toBe(false)
  })

  it('reconnects with growing delay after failures and resets after playing', async () => {
    const { api } = mountStream()
    await nextTick()
    emit({ type: 'failed' })
    expect(api().failed.value).toBe(true)
    expect(api().playing.value).toBe(false)
    await vi.advanceTimersByTimeAsync(RECONNECT_MIN_MS - 1)
    expect(players).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(players).toHaveLength(2)

    emit({ type: 'failed' })
    await vi.advanceTimersByTimeAsync(RECONNECT_MIN_MS * 2)
    expect(players).toHaveLength(3)

    emit({ type: 'playing', mode: 'mse' })
    emit({ type: 'failed' })
    await vi.advanceTimersByTimeAsync(RECONNECT_MIN_MS)
    expect(players).toHaveLength(4)
  })

  it('caps the reconnect delay', async () => {
    mountStream()
    await nextTick()
    for (let i = 0; i < 10; i++) {
      emit({ type: 'failed' })
      await vi.advanceTimersByTimeAsync(RECONNECT_MAX_MS)
    }
    const before = players.length
    emit({ type: 'failed' })
    await vi.advanceTimersByTimeAsync(RECONNECT_MAX_MS - 1)
    expect(players).toHaveLength(before)
    await vi.advanceTimersByTimeAsync(1)
    expect(players).toHaveLength(before + 1)
  })

  it('stops while hidden or out of view and resumes afterwards', async () => {
    const { api } = mountStream()
    await nextTick()
    const first = last()
    setVisibility('hidden')
    await nextTick()
    expect(first.stop).toHaveBeenCalled()
    expect(players).toHaveLength(1)
    setVisibility('visible')
    await nextTick()
    expect(players).toHaveLength(2)

    observerCallback?.([{ isIntersecting: false }])
    await nextTick()
    expect(last().stop).toHaveBeenCalled()
    expect(players).toHaveLength(2)
    observerCallback?.([{ isIntersecting: true }])
    await nextTick()
    expect(players).toHaveLength(3)
    expect(api().playing.value).toBe(false)
  })

  it('restarts on src, mode or auth change and stays off when inactive', async () => {
    const { active, src, modes, authKey } = mountStream()
    await nextTick()
    src.value = 'cam_main'
    await nextTick()
    expect(players).toHaveLength(2)
    modes.value = ['mse']
    await nextTick()
    expect(last().opts.modes).toEqual(['mse'])
    authKey.value = 'jwt-2'
    await nextTick()
    expect(players).toHaveLength(4)
    active.value = false
    await nextTick()
    expect(players).toHaveLength(4)
    expect(last().stop).toHaveBeenCalled()
    src.value = ''
    active.value = true
    await nextTick()
    expect(players).toHaveLength(4)  // kein Stream gewählt
  })

  it('a pending retry is cancelled by a restart', async () => {
    const { src } = mountStream()
    await nextTick()
    emit({ type: 'failed' })
    src.value = 'other'
    await nextTick()
    expect(players).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(RECONNECT_MAX_MS)
    expect(players).toHaveLength(2)
  })

  it('cleans up on unmount', async () => {
    const { wrapper } = mountStream()
    await nextTick()
    const p = last()
    emit({ type: 'failed' })
    wrapper.unmount()
    expect(p.stop).toHaveBeenCalled()
    expect(disconnect).toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(RECONNECT_MAX_MS)
    expect(players).toHaveLength(1)
  })

  it('works without IntersectionObserver or container', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    mountStream()
    await nextTick()
    expect(players).toHaveLength(1)
    vi.unstubAllGlobals()
    vi.stubGlobal('IntersectionObserver', class { observe = observe; disconnect = disconnect })
    mountStream({ withContainer: false })
    await nextTick()
    expect(observe).not.toHaveBeenCalled()
    expect(players).toHaveLength(2)
  })
})
