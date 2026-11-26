/**
 * Player für go2rtc-Streams (#1479) — WebRTC mit Rückfall auf MSE.
 *
 * Spricht das WebSocket-Protokoll von go2rtc (`/api/ws`, bei Frigate durchgereicht unter
 * `/live/{webrtc,mse}/api/ws`), nach dem Vorbild von go2rtcs `video-rtc.js`:
 *
 *   WebRTC: Client → {type:'webrtc/offer', value:sdp}, {type:'webrtc/candidate', value}
 *           Server → {type:'webrtc/answer', value:sdp}, {type:'webrtc/candidate', value}
 *   MSE:    Client → {type:'mse', value:'<unterstützte Codecs>'}
 *           Server → {type:'mse', value:'<MIME-Typ>'}, danach fMP4-Segmente als Binär-Frames
 *   beide:  Server → {type:'error', value:'…'}
 *
 * Die Modi werden der Reihe nach versucht; ein Modus gilt als gestartet, sobald das Video
 * spielt. Bricht ein laufender Stream ab, meldet der Player `failed` — Reconnect, Sichtbarkeit
 * und Snapshot-Platzhalter regelt der Aufrufer.
 */

export type PlayerMode = 'webrtc' | 'mse'

export interface SocketTarget {
  url: string
  protocols?: string[]
}

export type PlayerEvent =
  | { type: 'mode'; mode: PlayerMode }
  | { type: 'playing'; mode: PlayerMode }
  | { type: 'audio'; hasAudio: boolean }
  | { type: 'failed' }

export interface PlayerOptions {
  video: HTMLVideoElement
  modes: PlayerMode[]
  /** WebSocket-Ziel für einen Modus; `null` = Modus hier nicht verfügbar. */
  target: (mode: PlayerMode) => Promise<SocketTarget | null>
  onEvent: (event: PlayerEvent) => void
  webrtcTimeoutMs?: number
  mseTimeoutMs?: number
}

/** Codecs, die go2rtc per MSE liefern kann; gefiltert auf das, was der Browser abspielt. */
const MSE_CODECS = [
  'avc1.640029', 'avc1.64002A', 'avc1.640033',
  'hvc1.1.6.L153.B0',
  'mp4a.40.2', 'mp4a.40.5', 'flac', 'opus',
]

const LIVE_EDGE_LAG = 1.5     // s Rückstand, ab dem auf die Live-Kante gesprungen wird
const LIVE_EDGE_TARGET = 0.5  // s Abstand zur Live-Kante nach dem Sprung
const BUFFER_KEEP = 10        // s, die hinter der Abspielposition im Puffer bleiben
const BUFFER_TRIM_AT = 30     // s Puffer, ab denen abgeschnitten wird

type MediaSourceCtor = {
  new (): MediaSource
  isTypeSupported(type: string): boolean
}

export function mediaSourceCtor(): { ctor: MediaSourceCtor; managed: boolean } | null {
  const g = globalThis as unknown as { ManagedMediaSource?: MediaSourceCtor; MediaSource?: MediaSourceCtor }
  if (g.ManagedMediaSource) return { ctor: g.ManagedMediaSource, managed: true }
  if (g.MediaSource) return { ctor: g.MediaSource, managed: false }
  return null
}

export function supportedMseCodecs(ctor: MediaSourceCtor): string {
  return MSE_CODECS
    .filter(c => ctor.isTypeSupported(`video/mp4; codecs="${c}"`) || ctor.isTypeSupported(`audio/mp4; codecs="${c}"`))
    .join(',')
}

export function mimeHasAudio(mime: string): boolean {
  return /mp4a|opus|flac/i.test(mime)
}

/** Ein Versuch (ein Modus); `cleanup` räumt alles ab, was er angelegt hat. */
interface Attempt {
  started: Promise<boolean>
  cleanup: () => void
}

export class Go2rtcPlayer {
  private readonly opts: PlayerOptions
  private current: Attempt | null = null
  private stopped = true
  private generation = 0

  constructor(opts: PlayerOptions) {
    this.opts = opts
  }

  /** Probiert die Modi der Reihe nach; meldet `failed`, wenn keiner startet. */
  async start(): Promise<void> {
    this.stop()
    this.stopped = false
    const gen = ++this.generation
    for (const mode of this.opts.modes) {
      let target: SocketTarget | null
      try {
        target = await this.opts.target(mode)
      } catch {
        target = null
      }
      if (gen !== this.generation) return
      if (!target) continue

      this.opts.onEvent({ type: 'mode', mode })
      const attempt = mode === 'webrtc' ? this.webrtc(target, gen) : this.mse(target, gen)
      this.current = attempt
      const ok = await attempt.started
      if (gen !== this.generation) return
      if (ok) {
        this.opts.onEvent({ type: 'playing', mode })
        return
      }
      this.dropCurrent()
    }
    // Zwischen dem letzten await und hier kann kein stop() mehr dazwischenkommen.
    this.opts.onEvent({ type: 'failed' })
  }

  stop(): void {
    this.generation++
    this.stopped = true
    this.dropCurrent()
    const v = this.opts.video
    v.srcObject = null
    v.removeAttribute('src')
  }

  private dropCurrent(): void {
    this.current?.cleanup()
    this.current = null
  }

  /** Abbruch eines bereits laufenden Streams (nicht während des Starts). */
  private lost(gen: number): void {
    if (gen !== this.generation || this.stopped) return
    this.dropCurrent()
    this.generation++
    this.opts.onEvent({ type: 'failed' })
  }

  /**
   * Wartet, bis das Video spielt — oder bis Timeout/Fehler. `cancel` beendet das Warten
   * mit `false`, damit ein abgebrochener Start nicht ewig hängt.
   */
  private waitPlaying(timeoutMs: number, failed: Promise<void>): { promise: Promise<boolean>; cancel: () => void } {
    const v = this.opts.video
    let settle!: (ok: boolean) => void
    const promise = new Promise<boolean>((resolve) => {
      let settled = false
      settle = (ok) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        v.removeEventListener('playing', onPlaying)
        resolve(ok)
      }
      const onPlaying = () => settle(true)
      v.addEventListener('playing', onPlaying)
      const timer = setTimeout(() => settle(false), timeoutMs)
      void failed.then(() => settle(false))
    })
    return { promise, cancel: () => settle(false) }
  }

  private play(): void {
    const p = this.opts.video.play()
    if (p && typeof p.catch === 'function') p.catch(() => { /* Autoplay verweigert: bleibt beim Snapshot */ })
  }

  // ── WebRTC ────────────────────────────────────────────────────────────────

  private webrtc(target: SocketTarget, gen: number): Attempt {
    const v = this.opts.video
    let running = false
    let signalFailed!: () => void
    const failed = new Promise<void>((resolve) => { signalFailed = resolve })
    const fail = () => { if (running) this.lost(gen); else signalFailed() }

    const pc = new RTCPeerConnection({ iceServers: [] })
    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.addTransceiver('audio', { direction: 'recvonly' })
    const stream = new MediaStream()
    pc.ontrack = (e) => {
      stream.addTrack(e.track)
      if (e.track.kind === 'audio') this.opts.onEvent({ type: 'audio', hasAudio: true })
      if (v.srcObject !== stream) {
        v.srcObject = stream
        this.play()
      }
    }
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected' || pc.connectionState === 'closed') fail()
    }

    const ws = new WebSocket(target.url, target.protocols)
    pc.onicecandidate = (e) => {
      if (e.candidate && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'webrtc/candidate', value: e.candidate.candidate }))
      }
    }
    ws.onopen = async () => {
      try {
        const offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        ws.send(JSON.stringify({ type: 'webrtc/offer', value: pc.localDescription?.sdp ?? offer.sdp }))
      } catch {
        fail()
      }
    }
    ws.onmessage = async (ev) => {
      if (typeof ev.data !== 'string') return
      let msg: { type?: string; value?: string }
      try { msg = JSON.parse(ev.data) } catch { return }
      try {
        if (msg.type === 'webrtc/answer') {
          await pc.setRemoteDescription({ type: 'answer', sdp: msg.value ?? '' })
        } else if (msg.type === 'webrtc/candidate' && msg.value) {
          await pc.addIceCandidate({ candidate: msg.value, sdpMid: '0' })
        } else if (msg.type === 'error') {
          fail()
        }
      } catch {
        fail()
      }
    }
    // Nach dem Start trägt die Peer-Verbindung die Medien; ein später geschlossener
    // Signalisierungs-WebSocket ist kein Abbruch.
    ws.onclose = () => { if (!running) signalFailed() }

    const waiting = this.waitPlaying(this.opts.webrtcTimeoutMs ?? 5000, failed)
    const started = waiting.promise.then((ok) => { running = ok; return ok })
    return {
      started,
      cleanup: () => {
        running = false
        waiting.cancel()
        ws.onopen = ws.onmessage = ws.onclose = null
        pc.ontrack = pc.onicecandidate = pc.onconnectionstatechange = null
        try { ws.close() } catch { /* bereits zu */ }
        pc.close()
        stream.getTracks().forEach(t => t.stop())
      },
    }
  }

  // ── MSE ───────────────────────────────────────────────────────────────────

  private mse(target: SocketTarget, gen: number): Attempt {
    const v = this.opts.video
    const found = mediaSourceCtor()
    if (!found) return { started: Promise.resolve(false), cleanup: () => {} }

    let running = false
    let signalFailed!: () => void
    const failed = new Promise<void>((resolve) => { signalFailed = resolve })
    const fail = () => { if (running) this.lost(gen); else signalFailed() }

    const ms = new found.ctor()
    if (found.managed) (v as HTMLVideoElement & { disableRemotePlayback: boolean }).disableRemotePlayback = true
    const objectUrl = URL.createObjectURL(ms)
    v.src = objectUrl

    let ws: WebSocket | null = null
    let sb: SourceBuffer | null = null
    const queue: ArrayBuffer[] = []

    const trimOrPump = () => {
      if (!sb || sb.updating) return
      const b = v.buffered
      if (b.length) {
        const start = b.start(0)
        const end = b.end(b.length - 1)
        if (end - v.currentTime > LIVE_EDGE_LAG) v.currentTime = end - LIVE_EDGE_TARGET
        if (v.currentTime - start > BUFFER_TRIM_AT) {
          sb.remove(start, v.currentTime - BUFFER_KEEP)
          return // updateend ruft erneut
        }
      }
      const next = queue.shift()
      if (!next) return
      try {
        sb.appendBuffer(next)
      } catch {
        fail()
      }
    }

    const onSourceOpen = () => {
      ws = new WebSocket(target.url, target.protocols)
      ws.binaryType = 'arraybuffer'
      ws.onopen = () => ws?.send(JSON.stringify({ type: 'mse', value: supportedMseCodecs(found.ctor) }))
      ws.onmessage = (ev) => {
        if (typeof ev.data === 'string') {
          let msg: { type?: string; value?: string }
          try { msg = JSON.parse(ev.data) } catch { return }
          if (msg.type === 'mse' && msg.value && !sb) {
            try {
              sb = ms.addSourceBuffer(msg.value)
            } catch {
              fail()
              return
            }
            sb.mode = 'segments'
            sb.addEventListener('updateend', trimOrPump)
            this.opts.onEvent({ type: 'audio', hasAudio: mimeHasAudio(msg.value) })
            this.play()
          } else if (msg.type === 'error') {
            fail()
          }
          return
        }
        queue.push(ev.data as ArrayBuffer)
        trimOrPump()
      }
      ws.onclose = () => fail()
    }
    ms.addEventListener('sourceopen', onSourceOpen, { once: true })

    const waiting = this.waitPlaying(this.opts.mseTimeoutMs ?? 10000, failed)
    const started = waiting.promise.then((ok) => { running = ok; return ok })
    return {
      started,
      cleanup: () => {
        running = false
        waiting.cancel()
        ms.removeEventListener('sourceopen', onSourceOpen)
        if (sb) sb.removeEventListener('updateend', trimOrPump)
        if (ws) {
          ws.onopen = ws.onmessage = ws.onclose = null
          try { ws.close() } catch { /* bereits zu */ }
        }
        queue.length = 0
        URL.revokeObjectURL(objectUrl)
      },
    }
  }
}
