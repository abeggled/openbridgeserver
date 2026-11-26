/**
 * URLs zur Kamera-Stream-API von OBS (#1479) und Hilfen für die Stream-Auswahl.
 */
import { cameraStreams, streamScopeParams, type StreamScope, type StreamSourceCamera, type StreamSourceStream } from '@/api/client'
import type { PlayerMode, SocketTarget } from './go2rtcPlayer'

const API = '/api/v1/camera/streams'

/** JPEG-Platzhalter über OBS; der JWT steckt wie beim Kamera-Proxy im Query (für `<img>`). */
export function snapshotUrl(instanceId: string, src: string, scope: StreamScope, jwt: string, height: number, cacheBuster: number): string {
  const p = streamScopeParams(scope)
  p.set('src', src)
  p.set('h', String(height))
  if (jwt) p.set('_token', jwt)
  p.set('_t', String(cacheBuster))
  return `${API}/${encodeURIComponent(instanceId)}/snapshot?${p.toString()}`
}

/** WebSocket-Relay über OBS; der JWT reist im Subprotokoll wie beim Live-WebSocket der Visu. */
export function relayTarget(instanceId: string, src: string, kind: PlayerMode, scope: StreamScope, jwt: string, loc: Location = window.location): SocketTarget {
  const p = streamScopeParams(scope)
  p.set('src', src)
  p.set('kind', kind)
  const scheme = loc.protocol === 'https:' ? 'wss' : 'ws'
  return {
    url: `${scheme}://${loc.host}${API}/${encodeURIComponent(instanceId)}/ws?${p.toString()}`,
    protocols: jwt ? [`obs.jwt.${jwt}`] : undefined,
  }
}

/**
 * Wohin der Player sich für einen Modus verbindet.
 * WebRTC: immer das Relay (nur Signalisierung). MSE: direkt zu go2rtc, wenn die Instanz das
 * erlaubt, sonst über OBS — außer eine HTTPS-Seite müsste ein `ws://` öffnen (Mixed Content).
 */
export async function resolveTarget(
  mode: PlayerMode,
  instanceId: string,
  src: string,
  scope: StreamScope,
  jwt: string,
  loc: Location = window.location,
): Promise<SocketTarget | null> {
  if (mode === 'webrtc') return relayTarget(instanceId, src, 'webrtc', scope, jwt, loc)
  const info = await cameraStreams.streamInfo(instanceId, src, scope)
  if (info.mse_mode === 'proxy') return relayTarget(instanceId, src, 'mse', scope, jwt, loc)
  if (!info.mse_direct_url) return null
  if (loc.protocol === 'https:' && info.mse_direct_url.startsWith('ws:')) return null
  return { url: info.mse_direct_url }
}

const SUB_RE = /sub|low|klein|small|detect/i

/** Vorschlag Kachel-Stream: der Substream, falls erkennbar, sonst der letzte. */
export function defaultTileSrc(streams: StreamSourceStream[]): string {
  const sub = streams.find(s => SUB_RE.test(s.label) || SUB_RE.test(s.src))
  return (sub ?? streams[streams.length - 1])?.src ?? ''
}

/** Vorschlag Vollbild-Stream: der erste, der nicht wie ein Substream aussieht. */
export function defaultFullscreenSrc(streams: StreamSourceStream[]): string {
  const main = streams.find(s => !SUB_RE.test(s.label) && !SUB_RE.test(s.src))
  return (main ?? streams[0])?.src ?? ''
}

/** Alle Streams aller Kameras (eigenständiges go2rtc: jeder Stream ist eine „Kamera“). */
export function allStreams(cameras: StreamSourceCamera[]): StreamSourceStream[] {
  return cameras.flatMap(c => c.streams)
}
