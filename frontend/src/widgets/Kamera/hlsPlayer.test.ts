// @vitest-environment jsdom
// HLS-Wiedergabe des Kamera-Widgets (#1480) mit nachgebautem hls.js.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HLS_MIME, splitCredentials, startHls, type HlsLoader } from './hlsPlayer'

type ErrorHandler = (event: string, data: { fatal: boolean }) => void

class FakeHls {
  static supported = true
  static instances: FakeHls[] = []
  static Events = { ERROR: 'hlsError' }
  static isSupported() { return FakeHls.supported }

  config: { xhrSetup?: (xhr: XMLHttpRequest, url: string) => void }
  handlers: ErrorHandler[] = []
  loadSource = vi.fn()
  attachMedia = vi.fn()
  destroy = vi.fn()

  constructor(config: FakeHls['config']) {
    this.config = config
    FakeHls.instances.push(this)
  }

  on(event: string, handler: ErrorHandler) {
    if (event === FakeHls.Events.ERROR) this.handlers.push(handler)
  }

  emitError(fatal: boolean) {
    for (const h of this.handlers) h(FakeHls.Events.ERROR, { fatal })
  }
}

const fakeLoader = (() => Promise.resolve({ default: FakeHls })) as unknown as HlsLoader
const failingLoader: HlsLoader = () => Promise.reject(new Error('chunk missing'))

function videoWith(canPlay: string) {
  const video = document.createElement('video')
  video.canPlayType = vi.fn(() => canPlay as CanPlayTypeResult)
  return video
}

afterEach(() => {
  FakeHls.supported = true
  FakeHls.instances = []
})

describe('splitCredentials', () => {
  it('leaves a URL without credentials unchanged', () => {
    expect(splitCredentials('http://cam.local/live.m3u8')).toEqual({ url: 'http://cam.local/live.m3u8', authorization: null })
  })

  it('leaves an unparsable URL unchanged', () => {
    expect(splitCredentials('/relative/live.m3u8')).toEqual({ url: '/relative/live.m3u8', authorization: null })
  })

  it('moves user and password into a Basic header', () => {
    const r = splitCredentials('http://admin:geheim@cam.local/live.m3u8')
    expect(r.url).toBe('http://cam.local/live.m3u8')
    expect(r.authorization).toBe('Basic ' + btoa('admin:geheim'))
  })

  it('decodes percent-encoded and non-ASCII credentials as UTF-8', () => {
    const r = splitCredentials('http://J%C3%BCrgen:p%40ss@cam.local/live.m3u8')
    expect(r.url).toBe('http://cam.local/live.m3u8')
    const bytes = new TextEncoder().encode('Jürgen:p@ss')
    expect(r.authorization).toBe('Basic ' + btoa(String.fromCharCode(...bytes)))
  })

  it('handles a password without user name', () => {
    const r = splitCredentials('http://:pw@cam.local/live.m3u8')
    expect(r.url).toBe('http://cam.local/live.m3u8')
    expect(r.authorization).toBe('Basic ' + btoa(':pw'))
  })
})

describe('startHls', () => {
  it('prefers hls.js when supported, even if the browser claims native HLS', async () => {
    const video = videoWith('maybe')
    const session = startHls(video, 'http://cam.local/live.m3u8', vi.fn(), fakeLoader)

    expect(await session.ready).toBe('hlsjs')
    const hls = FakeHls.instances[0]
    expect(hls.loadSource).toHaveBeenCalledWith('http://cam.local/live.m3u8')
    expect(hls.attachMedia).toHaveBeenCalledWith(video)
    expect(hls.config.xhrSetup).toBeUndefined()
    expect(video.getAttribute('src')).toBeNull()
  })

  it('loads without credentials in the URL and sends them as header instead', async () => {
    const session = startHls(videoWith(''), 'http://admin:geheim@cam.local/live.m3u8', vi.fn(), fakeLoader)
    await session.ready

    const hls = FakeHls.instances[0]
    expect(hls.loadSource).toHaveBeenCalledWith('http://cam.local/live.m3u8')
    const xhr = { setRequestHeader: vi.fn() } as unknown as XMLHttpRequest
    hls.config.xhrSetup!(xhr, 'http://cam.local/seg1.ts')
    expect(xhr.setRequestHeader).toHaveBeenCalledWith('Authorization', 'Basic ' + btoa('admin:geheim'))
  })

  it('reports a fatal hls.js error once and destroys the instance', async () => {
    const onFatal = vi.fn()
    const session = startHls(videoWith(''), 'http://cam.local/live.m3u8', onFatal, fakeLoader)
    await session.ready
    const hls = FakeHls.instances[0]

    hls.emitError(false)
    expect(onFatal).not.toHaveBeenCalled()
    expect(hls.destroy).not.toHaveBeenCalled()

    hls.emitError(true)
    expect(onFatal).toHaveBeenCalledTimes(1)
    expect(hls.destroy).toHaveBeenCalledTimes(1)

    // Die Instanz ist schon weg: ein späteres destroy() der Sitzung räumt nichts doppelt.
    session.destroy()
    expect(hls.destroy).toHaveBeenCalledTimes(1)
  })

  it('ignores a fatal error that arrives after the session was destroyed', async () => {
    const onFatal = vi.fn()
    const session = startHls(videoWith(''), 'http://cam.local/live.m3u8', onFatal, fakeLoader)
    await session.ready
    const hls = FakeHls.instances[0]

    session.destroy()
    expect(hls.destroy).toHaveBeenCalledTimes(1)
    hls.emitError(true)
    expect(onFatal).not.toHaveBeenCalled()
  })

  it('discards a start that is destroyed while hls.js is still loading', async () => {
    const video = videoWith('maybe')
    const onFatal = vi.fn()
    const session = startHls(video, 'http://cam.local/live.m3u8', onFatal, fakeLoader)
    session.destroy()

    expect(await session.ready).toBeNull()
    expect(FakeHls.instances).toHaveLength(0)
    expect(video.getAttribute('src')).toBeNull()
    expect(onFatal).not.toHaveBeenCalled()
  })

  it('falls back to native HLS when hls.js is not supported', async () => {
    FakeHls.supported = false
    const video = videoWith('maybe')
    const onFatal = vi.fn()
    const session = startHls(video, 'http://cam.local/live.m3u8', onFatal, fakeLoader)

    expect(await session.ready).toBe('native')
    expect(video.canPlayType).toHaveBeenCalledWith(HLS_MIME)
    expect(video.getAttribute('src')).toBe('http://cam.local/live.m3u8')
    expect(FakeHls.instances).toHaveLength(0)
    expect(onFatal).not.toHaveBeenCalled()
    session.destroy()
  })

  it('falls back to native HLS when the hls.js chunk cannot be loaded', async () => {
    const video = videoWith('maybe')
    const session = startHls(video, 'http://cam.local/live.m3u8', vi.fn(), failingLoader)

    expect(await session.ready).toBe('native')
    expect(video.getAttribute('src')).toBe('http://cam.local/live.m3u8')
  })

  it('reports an error when the browser can play HLS neither way', async () => {
    FakeHls.supported = false
    const video = videoWith('')
    const onFatal = vi.fn()
    const session = startHls(video, 'http://cam.local/live.m3u8', onFatal, fakeLoader)

    expect(await session.ready).toBe('unsupported')
    expect(onFatal).toHaveBeenCalledTimes(1)
    expect(video.getAttribute('src')).toBeNull()
  })

  it('loads the real hls.js on demand by default', async () => {
    // jsdom hat weder Media Source Extensions noch natives HLS.
    const onFatal = vi.fn()
    const session = startHls(videoWith(''), 'http://cam.local/live.m3u8', onFatal)

    expect(await session.ready).toBe('unsupported')
    expect(onFatal).toHaveBeenCalledTimes(1)
  })
})
