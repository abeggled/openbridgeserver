// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

const streamInfo = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/api/client')>()
  return { ...mod, cameraStreams: { ...mod.cameraStreams, streamInfo } }
})

import { streamScopeParams } from '@/api/client'
import {
  allStreams,
  defaultFullscreenSrc,
  defaultTileSrc,
  relayTarget,
  resolveTarget,
  snapshotUrl,
} from './streamSource'

const http = { protocol: 'http:', host: 'obs.lan:8080' } as Location
const https = { protocol: 'https:', host: 'obs.example' } as Location
const page = { pageId: 'p1', sessionToken: 's1' }

afterEach(() => streamInfo.mockReset())

describe('streamScopeParams', () => {
  it('sets only what is given', () => {
    expect(streamScopeParams({}).toString()).toBe('')
    expect(streamScopeParams({ editorPreview: true, pageId: 'p', sessionToken: 't' }).toString())
      .toBe('editor_preview=1&page_id=p&session_token=t')
  })
})

describe('snapshotUrl', () => {
  it('carries scope, size, token and cache buster', () => {
    expect(snapshotUrl('inst/1', 'cam a', page, 'jwt', 480, 7))
      .toBe('/api/v1/camera/streams/inst%2F1/snapshot?page_id=p1&session_token=s1&src=cam+a&h=480&_token=jwt&_t=7')
  })

  it('omits the token when logged out', () => {
    expect(snapshotUrl('i', 'c', {}, '', 360, 1)).toBe('/api/v1/camera/streams/i/snapshot?src=c&h=360&_t=1')
  })
})

describe('relayTarget', () => {
  it('uses ws on http and the JWT subprotocol', () => {
    expect(relayTarget('i', 'c', 'webrtc', page, 'jwt', http)).toEqual({
      url: 'ws://obs.lan:8080/api/v1/camera/streams/i/ws?page_id=p1&session_token=s1&src=c&kind=webrtc',
      protocols: ['obs.jwt.jwt'],
    })
  })

  it('uses wss on https and no subprotocol without JWT', () => {
    expect(relayTarget('i', 'c', 'mse', { editorPreview: true }, '', https)).toEqual({
      url: 'wss://obs.example/api/v1/camera/streams/i/ws?editor_preview=1&src=c&kind=mse',
      protocols: undefined,
    })
  })

  it('defaults to the current location', () => {
    expect(relayTarget('i', 'c', 'webrtc', {}, '').url).toContain(`//${window.location.host}/api/v1/camera/streams/i/ws`)
  })
})

describe('resolveTarget', () => {
  it('webrtc always goes through the relay without asking for stream info', async () => {
    const target = await resolveTarget('webrtc', 'i', 'c', page, 'jwt', http)
    expect(target?.url).toContain('kind=webrtc')
    expect(streamInfo).not.toHaveBeenCalled()
  })

  it('mse in proxy mode uses the relay', async () => {
    streamInfo.mockResolvedValue({ mse_mode: 'proxy', mse_direct_url: null })
    const target = await resolveTarget('mse', 'i', 'c', page, 'jwt', http)
    expect(target?.url).toContain('kind=mse')
    expect(streamInfo).toHaveBeenCalledWith('i', 'c', page)
  })

  it('mse in direct mode connects to go2rtc directly', async () => {
    streamInfo.mockResolvedValue({ mse_mode: 'direct', mse_direct_url: 'ws://cams:1984/api/ws?src=c' })
    expect(await resolveTarget('mse', 'i', 'c', page, 'jwt', http)).toEqual({ url: 'ws://cams:1984/api/ws?src=c' })
  })

  it('direct mode without URL (credentials) is unavailable', async () => {
    streamInfo.mockResolvedValue({ mse_mode: 'direct', mse_direct_url: null })
    expect(await resolveTarget('mse', 'i', 'c', page, 'jwt', http)).toBeNull()
  })

  it('an https page cannot open ws:// directly', async () => {
    streamInfo.mockResolvedValue({ mse_mode: 'direct', mse_direct_url: 'ws://cams:1984/api/ws?src=c' })
    expect(await resolveTarget('mse', 'i', 'c', page, 'jwt', https)).toBeNull()
    streamInfo.mockResolvedValue({ mse_mode: 'direct', mse_direct_url: 'wss://cams/api/ws?src=c' })
    expect(await resolveTarget('mse', 'i', 'c', page, 'jwt', https)).toEqual({ url: 'wss://cams/api/ws?src=c' })
  })

  it('defaults to the current location', async () => {
    expect((await resolveTarget('webrtc', 'i', 'c', {}, ''))?.url).toContain(window.location.host)
  })
})

describe('stream defaults', () => {
  const frigate = [
    { label: 'cam_hof_rtsp', src: 'cam_hof_rtsp' },
    { label: 'cam_hof_rtsp_sub', src: 'cam_hof_rtsp_sub' },
  ]

  it('picks sub for the tile and main for full screen', () => {
    expect(defaultTileSrc(frigate)).toBe('cam_hof_rtsp_sub')
    expect(defaultFullscreenSrc(frigate)).toBe('cam_hof_rtsp')
  })

  it('recognises the sub stream by its label', () => {
    const streams = [{ label: 'Main', src: 'a' }, { label: 'Detect', src: 'b' }]
    expect(defaultTileSrc(streams)).toBe('b')
    expect(defaultFullscreenSrc(streams)).toBe('a')
  })

  it('falls back to last/first when nothing looks like a sub stream', () => {
    const streams = [{ label: 'A', src: 'a' }, { label: 'B', src: 'b' }]
    expect(defaultTileSrc(streams)).toBe('b')
    expect(defaultFullscreenSrc(streams)).toBe('a')
  })

  it('falls back to the first when everything looks like a sub stream', () => {
    expect(defaultFullscreenSrc([{ label: 'sub1', src: 'x_sub' }])).toBe('x_sub')
  })

  it('is empty without streams', () => {
    expect(defaultTileSrc([])).toBe('')
    expect(defaultFullscreenSrc([])).toBe('')
  })

  it('flattens all streams', () => {
    expect(allStreams([{ name: 'a', streams: [frigate[0]] }, { name: 'b', streams: [frigate[1]] }])).toEqual(frigate)
  })
})
