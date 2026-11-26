// @vitest-environment jsdom
// Kamera-Widget mit Stream-Typ HLS (#1480): Start, Fehler-Overlay und Aufräumen.
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

interface StartCall {
  video: HTMLVideoElement
  url: string
  onFatal: () => void
  destroy: ReturnType<typeof vi.fn>
}

const hls = vi.hoisted(() => ({ calls: [] as StartCall[] }))

vi.mock('./hlsPlayer', () => ({
  startHls: (video: HTMLVideoElement, url: string, onFatal: () => void) => {
    const call = { video, url, onFatal, destroy: vi.fn() }
    hls.calls.push(call)
    return { ready: Promise.resolve('hlsjs'), destroy: call.destroy }
  },
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useRoute: () => reactive({ params: { id: 'p' }, fullPath: '/p' }),
}))
vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({ getNode: () => undefined, getChildren: () => [], hasSessionToken: () => false, isLoggedIn: false }),
}))

import KameraWidget from './Widget.vue'

const HLS_CONFIG = { url: 'http://cam.local/live.m3u8', streamType: 'hls' }

const wrappers: VueWrapper[] = []

function mountWidget(config: Record<string, unknown> = HLS_CONFIG) {
  const w = mount(KameraWidget, {
    props: { config, datapointId: null, value: null, statusValue: null, editorMode: false },
    global: { mocks: { $t: (key: string) => key } },
    attachTo: document.body,
  })
  wrappers.push(w)
  return w
}

beforeEach(() => {
  hls.calls = []
  vi.useFakeTimers()
})

afterEach(() => {
  for (const w of wrappers.splice(0)) w.unmount()
  vi.useRealTimers()
  localStorage.clear()
})

describe('Kamera Widget.vue — HLS', () => {
  it('starts the HLS player on the rendered video element', async () => {
    const wrapper = mountWidget()
    await nextTick()

    expect(hls.calls).toHaveLength(1)
    expect(hls.calls[0].url).toBe('http://cam.local/live.m3u8')
    expect(hls.calls[0].video).toBe(wrapper.get('video').element)
    // Die Quelle setzt der Player, nicht das Template.
    expect(wrapper.get('video').attributes('src')).toBeUndefined()
  })

  it('shows the error overlay on a fatal error and restarts on a new element after 10 s', async () => {
    const wrapper = mountWidget()
    await nextTick()
    const first = hls.calls[0]

    first.onFatal()
    await nextTick()
    expect(wrapper.find('[data-testid="kamera-reload"]').exists()).toBe(true)

    vi.advanceTimersByTime(10_000)
    await nextTick()
    await nextTick()

    expect(wrapper.find('[data-testid="kamera-reload"]').exists()).toBe(false)
    expect(first.destroy).toHaveBeenCalledTimes(1)
    expect(hls.calls).toHaveLength(2)
    expect(hls.calls[1].video).not.toBe(first.video)
    expect(hls.calls[1].video).toBe(wrapper.get('video').element)
  })

  it('restarts when "reload now" is pressed', async () => {
    const wrapper = mountWidget()
    await nextTick()
    hls.calls[0].onFatal()
    await nextTick()

    await wrapper.get('[data-testid="kamera-reload"]').trigger('click')
    await nextTick()

    expect(hls.calls[0].destroy).toHaveBeenCalledTimes(1)
    expect(hls.calls).toHaveLength(2)
  })

  it('destroys the old session and starts the new URL when the URL changes', async () => {
    const wrapper = mountWidget()
    await nextTick()

    await wrapper.setProps({ config: { ...HLS_CONFIG, url: 'http://cam.local/other.m3u8' } })
    await nextTick()

    expect(hls.calls[0].destroy).toHaveBeenCalledTimes(1)
    expect(hls.calls).toHaveLength(2)
    expect(hls.calls[1].url).toBe('http://cam.local/other.m3u8')
  })

  it('destroys the session when the stream type is switched away from HLS', async () => {
    const wrapper = mountWidget()
    await nextTick()

    await wrapper.setProps({ config: { ...HLS_CONFIG, streamType: 'mjpeg' } })
    await nextTick()

    expect(hls.calls[0].destroy).toHaveBeenCalledTimes(1)
    expect(hls.calls).toHaveLength(1)
    expect(wrapper.find('video').exists()).toBe(false)
  })

  it('destroys the session on unmount', async () => {
    const wrapper = mountWidget()
    await nextTick()

    wrapper.unmount()
    wrappers.splice(wrappers.indexOf(wrapper), 1)

    expect(hls.calls[0].destroy).toHaveBeenCalled()
  })

  it('does not start the HLS player for other stream types', async () => {
    mountWidget({ url: 'http://cam.local/stream', streamType: 'mjpeg' })
    await nextTick()
    expect(hls.calls).toHaveLength(0)
  })
})
