// @vitest-environment jsdom
// Kamera-Widget mit Quelle „Kamera-Server“ (go2rtc / Frigate, #1479).
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive, ref, type Ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Go2rtcStreamOptions } from './useGo2rtcStream'

const stream = vi.hoisted(() => ({
  opts: null as Go2rtcStreamOptions | null,
  state: null as null | {
    mode: Ref<string | null>
    playing: Ref<boolean>
    failed: Ref<boolean>
    hasAudio: Ref<boolean>
    restart: () => void
  },
}))
const resolveTarget = vi.hoisted(() => vi.fn(async () => ({ url: 'ws://x' })))

vi.mock('./useGo2rtcStream', () => ({
  useGo2rtcStream: (opts: Go2rtcStreamOptions) => {
    stream.opts = opts
    return stream.state
  },
}))
vi.mock('./streamSource', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./streamSource')>()),
  resolveTarget,
}))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useRoute: () => reactive({ params: { id: 'p' }, fullPath: '/p' }),
}))
vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({ getNode: () => undefined, getChildren: () => [], hasSessionToken: () => false, isLoggedIn: false }),
}))

import KameraWidget from './Widget.vue'
import './index'
import { WidgetRegistry } from '@/widgets/registry'

const CONFIG = {
  source: 'go2rtc',
  streamInstanceId: 'inst-1',
  streamCamera: 'hof',
  tileSrc: 'cam_sub',
  fullscreenSrc: 'cam_main',
  preferMode: 'auto',
}

const wrappers: VueWrapper[] = []

function mountWidget(config: Record<string, unknown> = CONFIG, editorMode = false) {
  const w = mount(KameraWidget, {
    props: { config, datapointId: null, value: null, statusValue: null, editorMode, pageId: 'page-1', sessionToken: 'sess' },
    global: { mocks: { $t: (key: string) => key } },
    attachTo: document.body,
  })
  wrappers.push(w)
  return w
}

const snapshot = (w: VueWrapper) => w.find('[data-testid="kamera-stream-snapshot"]')

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.setItem('visu_jwt', 'jwt-1')
  stream.opts = null
  stream.state = { mode: ref(null), playing: ref(false), failed: ref(false), hasAudio: ref(false), restart: vi.fn() }
  resolveTarget.mockClear()
})

afterEach(() => {
  for (const w of wrappers.splice(0)) w.unmount()
  localStorage.clear()
  vi.useRealTimers()
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null })
})

describe('Kamera widget — camera server source', () => {
  it('shows the editor placeholder until instance and stream are chosen', () => {
    const w = mountWidget({ source: 'go2rtc', streamInstanceId: 'inst-1' }, true)
    expect(w.text()).toContain('widgets.kamera.configureUrl')
    expect(w.find('[data-testid="kamera-stream"]').exists()).toBe(false)
  })

  it('shows "not configured" in the viewer', () => {
    const w = mountWidget({ source: 'go2rtc', tileSrc: 'x' })
    expect(w.text()).toContain('widgets.kamera.noUrlConfigured')
  })

  it('passes stream options and resolves targets through the API helper', async () => {
    mountWidget()
    const o = stream.opts!
    expect(o.active.value).toBe(true)
    expect(o.src.value).toBe('cam_sub')
    expect(o.modes.value).toEqual(['webrtc', 'mse'])
    expect(o.authKey.value).toBe('jwt-1')
    await o.resolve('mse', 'cam_sub')
    expect(resolveTarget).toHaveBeenCalledWith('mse', 'inst-1', 'cam_sub', { pageId: 'page-1', sessionToken: 'sess', editorPreview: false }, 'jwt-1')
  })

  it('MSE only when preferred; full screen falls back to the tile stream', () => {
    const w = mountWidget({ ...CONFIG, preferMode: 'mse', fullscreenSrc: '' })
    expect(stream.opts!.modes.value).toEqual(['mse'])
    expect((w.vm as unknown as { fullscreenSrc: string }).fullscreenSrc).toBe('cam_sub')
  })

  it('editor shows only the snapshot (editor preview), no live stream and no controls', () => {
    const w = mountWidget(CONFIG, true)
    expect(stream.opts!.active.value).toBe(false)
    expect(snapshot(w).attributes('src')).toContain('editor_preview=1')
    expect(w.find('[data-testid="kamera-stream-fullscreen"]').exists()).toBe(false)
    stream.state!.failed.value = true
    expect(w.find('[data-testid="kamera-stream-offline"]').exists()).toBe(false)
  })

  it('shows the snapshot until the stream plays, then the video', async () => {
    const w = mountWidget()
    const src = snapshot(w).attributes('src')!
    expect(src).toContain('/api/v1/camera/streams/inst-1/snapshot?')
    expect(src).toContain('page_id=page-1')
    expect(src).toContain('src=cam_sub')
    expect(src).toContain('_token=jwt-1')
    expect(w.get('[data-testid="kamera-stream-video"]').attributes('style')).toContain('display: none')

    stream.state!.mode.value = 'webrtc'
    stream.state!.playing.value = true
    await nextTick()
    expect(snapshot(w).exists()).toBe(false)
    expect(w.get('[data-testid="kamera-stream-video"]').attributes('style')).not.toContain('display: none')
    expect(w.get('[data-testid="kamera-stream"]').attributes('data-mode')).toBe('webrtc')
  })

  it('refreshes the snapshot only while not playing', async () => {
    const w = mountWidget()
    const first = snapshot(w).attributes('src')
    await vi.advanceTimersByTimeAsync(10_000)
    const second = snapshot(w).attributes('src')
    expect(second).not.toBe(first)

    stream.state!.playing.value = true
    await nextTick()
    await vi.advanceTimersByTimeAsync(10_000)
    stream.state!.playing.value = false
    await nextTick()
    expect(snapshot(w).attributes('src')).toBe(second)
  })

  it('hides a broken snapshot until the next one', async () => {
    const w = mountWidget()
    await snapshot(w).trigger('error')
    expect(snapshot(w).exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(snapshot(w).exists()).toBe(true)
  })

  it('marks a failed live stream', async () => {
    const w = mountWidget()
    stream.state!.failed.value = true
    await nextTick()
    expect(w.find('[data-testid="kamera-stream-offline"]').text()).toBe('widgets.kamera.liveUnavailable')
  })

  it('offers mute only with audio and toggles it', async () => {
    const w = mountWidget()
    expect(w.find('[data-testid="kamera-stream-mute"]').exists()).toBe(false)
    stream.state!.hasAudio.value = true
    await nextTick()
    const btn = w.get('[data-testid="kamera-stream-mute"]')
    expect(btn.attributes('title')).toBe('widgets.kamera.unmute')
    expect((w.get('video').element as HTMLVideoElement).muted).toBe(true)
    await btn.trigger('click')
    expect(btn.attributes('title')).toBe('widgets.kamera.mute')
    expect((w.get('video').element as HTMLVideoElement).muted).toBe(false)
  })

  it('enters full screen on the widget and switches to the full-screen stream', async () => {
    const w = mountWidget()
    const root = w.get('[data-testid="kamera-widget"]').element as HTMLElement
    const request = vi.fn(async () => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: root })
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    root.requestFullscreen = request
    const btn = w.get('[data-testid="kamera-stream-fullscreen"]')
    expect(btn.attributes('title')).toBe('widgets.kamera.fullscreen')

    await btn.trigger('click')
    await nextTick()
    expect(request).toHaveBeenCalled()
    expect(stream.opts!.src.value).toBe('cam_main')
    expect(btn.attributes('title')).toBe('widgets.kamera.exitFullscreen')
    expect(snapshot(w).attributes('src')).toContain('src=cam_main')

    const exit = vi.fn(async () => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null })
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    document.exitFullscreen = exit
    await btn.trigger('click')
    await nextTick()
    expect(exit).toHaveBeenCalled()
    expect(stream.opts!.src.value).toBe('cam_sub')
  })

  it('another element in full screen does not count', async () => {
    mountWidget()
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: document.createElement('div') })
    document.dispatchEvent(new Event('fullscreenchange'))
    await nextTick()
    expect(stream.opts!.src.value).toBe('cam_sub')
  })

  it('ignores rejected full-screen requests', async () => {
    const w = mountWidget()
    const root = w.get('[data-testid="kamera-widget"]').element as HTMLElement
    root.requestFullscreen = vi.fn(async () => { throw new Error('denied') })
    await w.get('[data-testid="kamera-stream-fullscreen"]').trigger('click')
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: root })
    document.exitFullscreen = vi.fn(async () => { throw new Error('denied') })
    await w.get('[data-testid="kamera-stream-fullscreen"]').trigger('click')
    expect(document.exitFullscreen).toHaveBeenCalled()
  })

  it('falls back to the native video full screen (iPhone)', async () => {
    const w = mountWidget()
    const root = w.get('[data-testid="kamera-widget"]').element as unknown as { requestFullscreen?: unknown }
    root.requestFullscreen = undefined
    const video = w.get('video').element as HTMLVideoElement & { webkitEnterFullscreen?: () => void }
    video.webkitEnterFullscreen = vi.fn()
    await w.get('[data-testid="kamera-stream-fullscreen"]').trigger('click')
    expect(video.webkitEnterFullscreen).toHaveBeenCalled()
    // Ohne beides passiert schlicht nichts.
    video.webkitEnterFullscreen = undefined
    await w.get('[data-testid="kamera-stream-fullscreen"]').trigger('click')
  })

  it('buttons do not trigger the tap action', async () => {
    const w = mountWidget({ ...CONFIG, clickAction: 'navigate', targetNodeId: 'x' })
    const root = w.get('[data-testid="kamera-widget"]').element as HTMLElement
    root.requestFullscreen = vi.fn(async () => {})
    const click = vi.fn()
    root.addEventListener('click', click)
    await w.get('[data-testid="kamera-stream-fullscreen"]').trigger('click')
    expect(click).not.toHaveBeenCalled()
  })

  it('stops the snapshot timer and full-screen listener on unmount', () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    const w = mountWidget()
    wrappers.splice(wrappers.indexOf(w), 1)
    w.unmount()
    expect(remove).toHaveBeenCalledWith('fullscreenchange', expect.any(Function))
    remove.mockRestore()
  })

  it('toggleFullscreen without a root element is a no-op', async () => {
    const w = mountWidget()
    // Über den Button ist das Root-Element immer da; der Guard schützt nur den Aufruf ohne DOM.
    const vm = w.vm as unknown as { rootEl: HTMLElement | null; toggleFullscreen: () => Promise<void> }
    const exit = vi.fn(async () => {})
    document.exitFullscreen = exit
    vm.rootEl = null
    await vm.toggleFullscreen()
    expect(exit).not.toHaveBeenCalled()
  })
})

describe('Kamera registration', () => {
  it('declares that no config value is a datapoint', () => {
    const def = WidgetRegistry.get('Kamera')!
    expect(def.getExtraDatapointIds?.({ streamInstanceId: '11111111-1111-1111-1111-111111111111' })).toEqual([])
    expect(def.defaultConfig.source).toBe('url')
  })
})
