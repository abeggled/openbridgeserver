// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  Go2rtcPlayer,
  mediaSourceCtor,
  mimeHasAudio,
  supportedMseCodecs,
  type PlayerEvent,
  type PlayerMode,
  type SocketTarget,
} from './go2rtcPlayer'

// ── Fakes ────────────────────────────────────────────────────────────────────

class FakeWS {
  static OPEN = 1
  static instances: FakeWS[] = []
  readyState = 0
  binaryType = ''
  sent: string[] = []
  closed = false
  onopen: (() => void | Promise<void>) | null = null
  onmessage: ((ev: { data: unknown }) => void | Promise<void>) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string, public protocols?: string[]) {
    FakeWS.instances.push(this)
  }
  send(data: string) { this.sent.push(data) }
  close() {
    this.closed = true
    if (this.throwOnClose) throw new Error('closed')
  }
  throwOnClose = false
  async open() { this.readyState = 1; await this.onopen?.() }
  async receive(data: unknown) { await this.onmessage?.({ data }) }
  json(i = 0) { return JSON.parse(this.sent[i]) }
}

class FakePC {
  static instances: FakePC[] = []
  static failCreateOffer = false
  static noLocalDescription = false
  transceivers: Array<[string, unknown]> = []
  localDescription: { sdp: string } | null = null
  remote: unknown[] = []
  candidates: unknown[] = []
  connectionState = 'new'
  closed = false
  failRemote = false
  ontrack: ((e: { track: FakeTrack }) => void) | null = null
  onicecandidate: ((e: { candidate: { candidate: string } | null }) => void) | null = null
  onconnectionstatechange: (() => void) | null = null
  constructor(public config: unknown) { FakePC.instances.push(this) }
  addTransceiver(kind: string, init: unknown) { this.transceivers.push([kind, init]) }
  async createOffer() {
    if (FakePC.failCreateOffer) throw new Error('no-offer')
    return { type: 'offer', sdp: 'offer-sdp' }
  }
  async setLocalDescription(d: { sdp: string }) { if (!FakePC.noLocalDescription) this.localDescription = d }
  async setRemoteDescription(d: unknown) {
    if (this.failRemote) throw new Error('bad-answer')
    this.remote.push(d)
  }
  async addIceCandidate(c: unknown) { this.candidates.push(c) }
  close() { this.closed = true }
  setState(state: string) { this.connectionState = state; this.onconnectionstatechange?.() }
}

class FakeTrack {
  stopped = false
  constructor(public kind: string) {}
  stop() { this.stopped = true }
}

class FakeStream {
  tracks: FakeTrack[] = []
  addTrack(t: FakeTrack) { this.tracks.push(t) }
  getTracks() { return this.tracks }
}

class FakeSourceBuffer extends EventTarget {
  mode = ''
  updating = false
  appended: unknown[] = []
  removed: Array<[number, number]> = []
  failAppend = false
  appendBuffer(data: unknown) {
    if (this.failAppend) throw new Error('quota')
    this.appended.push(data)
  }
  remove(start: number, end: number) { this.removed.push([start, end]) }
  done() { this.dispatchEvent(new Event('updateend')) }
}

class FakeMediaSource extends EventTarget {
  static instances: FakeMediaSource[] = []
  static supported = new Set(['avc1.640029', 'mp4a.40.2'])
  static isTypeSupported(type: string) {
    return [...FakeMediaSource.supported].some(c => type.includes(`"${c}"`))
  }
  buffers: FakeSourceBuffer[] = []
  failAdd = false
  constructor() { super(); FakeMediaSource.instances.push(this) }
  addSourceBuffer(mime: string) {
    if (this.failAdd) throw new Error(mime)
    const sb = new FakeSourceBuffer()
    this.buffers.push(sb)
    return sb
  }
  open() { this.dispatchEvent(new Event('sourceopen')) }
}

class FakeVideo extends EventTarget {
  srcObject: unknown = null
  src = ''
  currentTime = 0
  ranges: Array<[number, number]> = []
  disableRemotePlayback = false
  playResult: unknown = Promise.resolve()
  play = vi.fn(() => this.playResult)
  get buffered() {
    const r = this.ranges
    return { length: r.length, start: (i: number) => r[i][0], end: (i: number) => r[i][1] }
  }
  removeAttribute(name: string) { if (name === 'src') this.src = '' }
  playing() { this.dispatchEvent(new Event('playing')) }
}

// ── Setup ────────────────────────────────────────────────────────────────────

let video: FakeVideo
let events: PlayerEvent[]
let targets: Record<PlayerMode, SocketTarget | null | Error>

function makePlayer(modes: PlayerMode[] = ['webrtc', 'mse'], extra: Partial<ConstructorParameters<typeof Go2rtcPlayer>[0]> = {}) {
  return new Go2rtcPlayer({
    video: video as unknown as HTMLVideoElement,
    modes,
    target: async (mode) => {
      const t = targets[mode]
      if (t instanceof Error) throw t
      return t
    },
    onEvent: (e) => events.push(e),
    ...extra,
  })
}

const flush = () => vi.advanceTimersByTimeAsync(0)
const lastWS = () => FakeWS.instances[FakeWS.instances.length - 1]
const lastPC = () => FakePC.instances[FakePC.instances.length - 1]
const lastMS = () => FakeMediaSource.instances[FakeMediaSource.instances.length - 1]

beforeEach(() => {
  vi.useFakeTimers()
  FakeWS.instances = []
  FakePC.instances = []
  FakePC.failCreateOffer = false
  FakePC.noLocalDescription = false
  FakeMediaSource.instances = []
  FakeMediaSource.supported = new Set(['avc1.640029', 'mp4a.40.2'])
  video = new FakeVideo()
  events = []
  targets = { webrtc: { url: 'ws://obs/webrtc', protocols: ['obs.jwt.x'] }, mse: { url: 'ws://obs/mse' } }
  vi.stubGlobal('WebSocket', FakeWS)
  vi.stubGlobal('RTCPeerConnection', FakePC)
  vi.stubGlobal('MediaStream', FakeStream)
  vi.stubGlobal('MediaSource', FakeMediaSource)
  vi.stubGlobal('ManagedMediaSource', undefined)
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:ms'), revokeObjectURL: vi.fn() })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Startet WebRTC bis zur laufenden Wiedergabe. */
async function startWebrtc(player = makePlayer()) {
  const done = player.start()
  await flush()
  const ws = lastWS()
  const pc = lastPC()
  await ws.open()
  await ws.receive(JSON.stringify({ type: 'webrtc/answer', value: 'answer-sdp' }))
  pc.ontrack?.({ track: new FakeTrack('video') })
  video.playing()
  await done
  return { player, ws, pc }
}

/** Startet MSE (als einzigen Modus) bis zur laufenden Wiedergabe. */
async function startMse(player = makePlayer(['mse']), mime = 'video/mp4; codecs="avc1.640029"') {
  const done = player.start()
  await flush()
  lastMS().open()
  const ws = lastWS()
  await ws.open()
  await ws.receive(JSON.stringify({ type: 'mse', value: mime }))
  video.playing()
  await done
  return { player, ws, ms: lastMS(), sb: lastMS().buffers[0] }
}

// ── Hilfsfunktionen ──────────────────────────────────────────────────────────

describe('helpers', () => {
  it('mimeHasAudio', () => {
    expect(mimeHasAudio('video/mp4; codecs="avc1.640029,mp4a.40.2"')).toBe(true)
    expect(mimeHasAudio('video/mp4; codecs="avc1.640029,opus"')).toBe(true)
    expect(mimeHasAudio('video/mp4; codecs="avc1.4D0029"')).toBe(false)
  })

  it('supportedMseCodecs filters by video or audio support', () => {
    expect(supportedMseCodecs(FakeMediaSource as unknown as Parameters<typeof supportedMseCodecs>[0])).toBe('avc1.640029,mp4a.40.2')
  })

  it('mediaSourceCtor prefers ManagedMediaSource, falls back, or reports none', () => {
    expect(mediaSourceCtor()).toEqual({ ctor: FakeMediaSource, managed: false })
    vi.stubGlobal('ManagedMediaSource', FakeMediaSource)
    expect(mediaSourceCtor()).toEqual({ ctor: FakeMediaSource, managed: true })
    vi.stubGlobal('ManagedMediaSource', undefined)
    vi.stubGlobal('MediaSource', undefined)
    expect(mediaSourceCtor()).toBeNull()
  })
})

// ── WebRTC ───────────────────────────────────────────────────────────────────

describe('WebRTC', () => {
  it('negotiates over the websocket and reports playing', async () => {
    const { ws, pc } = await startWebrtc()
    expect(ws.url).toBe('ws://obs/webrtc')
    expect(ws.protocols).toEqual(['obs.jwt.x'])
    expect(pc.config).toEqual({ iceServers: [] })
    expect(pc.transceivers.map(t => t[0])).toEqual(['video', 'audio'])
    expect(ws.json(0)).toEqual({ type: 'webrtc/offer', value: 'offer-sdp' })
    expect(pc.remote).toEqual([{ type: 'answer', sdp: 'answer-sdp' }])
    expect(video.srcObject).toBeInstanceOf(FakeStream)
    expect(video.play).toHaveBeenCalledTimes(1)
    expect(events).toEqual([{ type: 'mode', mode: 'webrtc' }, { type: 'playing', mode: 'webrtc' }])
  })

  it('trickles ICE both ways and ignores noise', async () => {
    const player = makePlayer()
    void player.start()
    await flush()
    const ws = lastWS()
    const pc = lastPC()
    pc.onicecandidate?.({ candidate: { candidate: 'early' } })  // WS noch zu → verworfen
    await ws.open()
    pc.onicecandidate?.({ candidate: { candidate: 'local-1' } })
    pc.onicecandidate?.({ candidate: null })
    await ws.receive(JSON.stringify({ type: 'webrtc/candidate', value: 'remote-1' }))
    await ws.receive(JSON.stringify({ type: 'webrtc/candidate' }))  // ohne Wert
    await ws.receive(JSON.stringify({ type: 'unknown' }))
    await ws.receive('{kaputt')
    await ws.receive(new ArrayBuffer(2))
    expect(ws.sent.slice(1).map(s => JSON.parse(s))).toEqual([{ type: 'webrtc/candidate', value: 'local-1' }])
    expect(pc.candidates).toEqual([{ candidate: 'remote-1', sdpMid: '0' }])
    player.stop()
  })

  it('an answer without sdp is passed on as empty', async () => {
    void makePlayer().start()
    await flush()
    await lastWS().receive(JSON.stringify({ type: 'webrtc/answer' }))
    expect(lastPC().remote).toEqual([{ type: 'answer', sdp: '' }])
  })

  it('uses the offer sdp when there is no local description', async () => {
    FakePC.noLocalDescription = true
    void makePlayer().start()
    await flush()
    await lastWS().open()
    expect(lastWS().json(0)).toEqual({ type: 'webrtc/offer', value: 'offer-sdp' })
  })

  it('reports audio and keeps one stream for several tracks', async () => {
    const player = makePlayer()
    void player.start()
    await flush()
    const pc = lastPC()
    pc.ontrack?.({ track: new FakeTrack('video') })
    const stream = video.srcObject
    pc.ontrack?.({ track: new FakeTrack('audio') })
    expect(video.srcObject).toBe(stream)
    expect(video.play).toHaveBeenCalledTimes(1)
    expect(events).toContainEqual({ type: 'audio', hasAudio: true })
    player.stop()
  })

  it('a closed signalling socket after start is not a failure', async () => {
    const { ws } = await startWebrtc()
    ws.onclose?.()
    expect(events[events.length - 1]).toEqual({ type: 'playing', mode: 'webrtc' })
  })

  it.each(['failed', 'disconnected', 'closed'])('connection state %s after start reports failed', async (state) => {
    const { pc, ws } = await startWebrtc()
    pc.setState(state)
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
    expect(pc.closed).toBe(true)
    expect(ws.closed).toBe(true)
    // Weitere Zustände nach dem Aufräumen ändern nichts mehr.
    pc.setState('failed')
    expect(events.filter(e => e.type === 'failed')).toHaveLength(1)
  })

  it('ignores harmless connection states', async () => {
    const { pc } = await startWebrtc()
    pc.setState('connecting')
    expect(events[events.length - 1]).toEqual({ type: 'playing', mode: 'webrtc' })
  })

  it('an error during the attempt falls back to MSE', async () => {
    void makePlayer().start()
    await flush()
    await lastWS().receive(JSON.stringify({ type: 'error', value: 'no stream' }))
    await flush()
    expect(lastPC().closed).toBe(true)
    expect(events).toEqual([{ type: 'mode', mode: 'webrtc' }, { type: 'mode', mode: 'mse' }])
  })

  it.each([
    ['createOffer throws', async () => { FakePC.failCreateOffer = true; await lastWS().open() }],
    ['bad answer', async () => { lastPC().failRemote = true; await lastWS().receive(JSON.stringify({ type: 'webrtc/answer', value: 'x' })) }],
    ['socket closes before start', async () => { lastWS().onclose?.() }],
    ['connection fails before start', async () => { lastPC().setState('failed') }],
  ])('%s → next mode', async (_name, act) => {
    void makePlayer(['webrtc', 'mse']).start()
    await flush()
    await act()
    await flush()
    expect(events.map(e => e.type === 'mode' && e.mode)).toEqual(['webrtc', 'mse'])
  })

  it('times out and falls back', async () => {
    void makePlayer(['webrtc', 'mse'], { webrtcTimeoutMs: 1000 }).start()
    await flush()
    await vi.advanceTimersByTimeAsync(999)
    expect(events).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(events[events.length - 1]).toEqual({ type: 'mode', mode: 'mse' })
  })

  it('uses a 5 s default timeout', async () => {
    void makePlayer(['webrtc']).start()
    await flush()
    await vi.advanceTimersByTimeAsync(4999)
    expect(events).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
  })

  it('tolerates a socket that throws on close', async () => {
    const { player, ws } = await startWebrtc()
    ws.throwOnClose = true
    expect(() => player.stop()).not.toThrow()
  })
})

// ── MSE ──────────────────────────────────────────────────────────────────────

describe('MSE', () => {
  it('negotiates codecs, appends segments and reports playing', async () => {
    const { ws, sb } = await startMse()
    expect(video.src).toBe('blob:ms')
    expect(ws.binaryType).toBe('arraybuffer')
    expect(ws.json(0)).toEqual({ type: 'mse', value: 'avc1.640029,mp4a.40.2' })
    expect(sb.mode).toBe('segments')
    expect(events).toEqual([
      { type: 'mode', mode: 'mse' },
      { type: 'audio', hasAudio: false },
      { type: 'playing', mode: 'mse' },
    ])
    const a = new ArrayBuffer(1)
    const b = new ArrayBuffer(2)
    sb.updating = true
    await ws.receive(a)          // Puffer beschäftigt → wartet
    expect(sb.appended).toEqual([])
    sb.updating = false
    sb.done()                    // updateend → a
    await ws.receive(b)          // sofort
    expect(sb.appended).toEqual([a, b])
    sb.done()                    // Schlange leer → nichts
    expect(sb.appended).toHaveLength(2)
  })

  it('reports audio from the MIME type', async () => {
    await startMse(makePlayer(['mse']), 'video/mp4; codecs="avc1.640029,mp4a.40.2"')
    expect(events).toContainEqual({ type: 'audio', hasAudio: true })
  })

  it('jumps to the live edge and trims old buffer', async () => {
    const { sb } = await startMse()
    video.currentTime = 1
    video.ranges = [[0, 5]]
    sb.done()
    expect(video.currentTime).toBe(4.5)
    expect(sb.removed).toEqual([])

    video.currentTime = 40
    video.ranges = [[0, 40.2]]
    sb.done()
    expect(sb.removed).toEqual([[0, 30]])
  })

  it('ignores segments before the source buffer exists and stray messages', async () => {
    void makePlayer(['mse']).start()
    await flush()
    lastMS().open()
    const ws = lastWS()
    await ws.open()
    await ws.receive(new ArrayBuffer(1))  // noch kein SourceBuffer → nur gepuffert
    await ws.receive('{kaputt')
    await ws.receive(JSON.stringify({ type: 'mse' }))  // ohne Wert
    await ws.receive(JSON.stringify({ type: 'other' }))
    await ws.receive(JSON.stringify({ type: 'mse', value: 'video/mp4; codecs="avc1.640029"' }))
    await ws.receive(JSON.stringify({ type: 'mse', value: 'video/mp4; codecs="avc1.640029"' }))  // zweites Mal: ignoriert
    expect(lastMS().buffers).toHaveLength(1)
  })

  it('sets disableRemotePlayback for ManagedMediaSource', async () => {
    vi.stubGlobal('ManagedMediaSource', FakeMediaSource)
    void makePlayer(['mse']).start()
    await flush()
    expect(video.disableRemotePlayback).toBe(true)
  })

  it('without MediaSource the mode fails immediately', async () => {
    vi.stubGlobal('MediaSource', undefined)
    await makePlayer(['mse']).start()
    expect(events).toEqual([{ type: 'mode', mode: 'mse' }, { type: 'failed' }])
  })

  it.each([
    ['unsupported MIME', async () => { lastMS().failAdd = true; await lastWS().receive(JSON.stringify({ type: 'mse', value: 'x' })) }],
    ['server error', async () => { await lastWS().receive(JSON.stringify({ type: 'error' })) }],
    ['socket closes', async () => { lastWS().onclose?.() }],
  ])('%s during start → failed', async (_name, act) => {
    const done = makePlayer(['mse']).start()
    await flush()
    lastMS().open()
    await lastWS().open()
    await act()
    await done
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:ms')
  })

  it('an append error after start reports failed', async () => {
    const { ws, sb } = await startMse()
    sb.failAppend = true
    await ws.receive(new ArrayBuffer(1))
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
  })

  it('a closed socket after start reports failed', async () => {
    const { ws } = await startMse()
    ws.onclose?.()
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
  })

  it('times out after 10 s by default', async () => {
    void makePlayer(['mse']).start()
    await flush()
    await vi.advanceTimersByTimeAsync(9999)
    expect(events).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(events[events.length - 1]).toEqual({ type: 'failed' })
  })

  it('cleanup before sourceopen leaves no socket', async () => {
    const player = makePlayer(['mse'], { mseTimeoutMs: 10 })
    void player.start()
    await flush()
    await vi.advanceTimersByTimeAsync(10)
    lastMS().open()  // Listener ist weg
    expect(FakeWS.instances).toHaveLength(0)
  })

  it('tolerates a socket that throws on close', async () => {
    const { player, ws } = await startMse()
    ws.throwOnClose = true
    expect(() => player.stop()).not.toThrow()
  })
})

// ── Ablauf ───────────────────────────────────────────────────────────────────

describe('Go2rtcPlayer flow', () => {
  it('skips unavailable modes and reports failed when none is left', async () => {
    targets = { webrtc: null, mse: new Error('stream-info') }
    await makePlayer().start()
    expect(events).toEqual([{ type: 'failed' }])
    expect(FakeWS.instances).toHaveLength(0)
  })

  it('stop during target resolution ends silently', async () => {
    let release!: (t: SocketTarget) => void
    const player = makePlayer(['webrtc'], {
      target: () => new Promise<SocketTarget>((r) => { release = r }),
    })
    const done = player.start()
    player.stop()
    release({ url: 'ws://late' })
    await done
    expect(events).toEqual([])
    expect(FakeWS.instances).toHaveLength(0)
  })

  it('stop during an attempt ends silently', async () => {
    const player = makePlayer(['webrtc', 'mse'])
    const done = player.start()
    await flush()
    player.stop()
    await done
    expect(events).toEqual([{ type: 'mode', mode: 'webrtc' }])
    expect(video.srcObject).toBeNull()
  })

  it('restart supersedes a pending failure', async () => {
    const player = makePlayer(['webrtc'], { webrtcTimeoutMs: 100 })
    void player.start()
    await flush()
    void player.start()
    await flush()
    await vi.advanceTimersByTimeAsync(100)
    expect(events.filter(e => e.type === 'failed')).toHaveLength(1)
  })

  it('ignores a loss reported by a superseded or stopped attempt', async () => {
    const { player } = await startWebrtc()
    const internals = player as unknown as { lost(gen: number): void; generation: number }
    internals.lost(internals.generation - 1)
    expect(events[events.length - 1]).toEqual({ type: 'playing', mode: 'webrtc' })
    player.stop()
    internals.lost(internals.generation)
    expect(events.filter(e => e.type === 'failed')).toHaveLength(0)
  })

  it('a refused autoplay keeps going; play() without promise is fine', async () => {
    video.play.mockImplementationOnce(() => Promise.reject(new Error('NotAllowedError')))
    await startWebrtc()
    video.playResult = undefined
    await startMse()
    expect(events.filter(e => e.type === 'playing')).toHaveLength(2)
  })
})
