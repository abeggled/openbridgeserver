// HLS-Wiedergabe des Kamera-Widgets (Quelle „Kamera-URL“, Typ hls, #1480).
//
// hls.js hat Vorrang, sobald der Browser es kann (Media Source Extensions); das
// native <video src="….m3u8"> ist nur der Rückfall, etwa für ein iPhone ohne
// ManagedMediaSource. Umgekehrt ginge es nicht: Chrome meldet für
// application/vnd.apple.mpegurl inzwischen „maybe“, bricht an go2rtc-Streams
// aber nach dem ersten Bild mit DEMUXER_ERROR_COULD_NOT_PARSE ab.
//
// hls.js wird erst geladen, wenn ein HLS-Widget startet; das Visu-Bundle für alle
// anderen Widgets wächst dadurch nicht.
import type Hls from 'hls.js'

export type HlsMode = 'hlsjs' | 'native' | 'unsupported'

export const HLS_MIME = 'application/vnd.apple.mpegurl'

type HlsClass = typeof Hls
export type HlsLoader = () => Promise<{ default: HlsClass }>

const loadHlsModule: HlsLoader = () => import('hls.js')

export interface HlsSession {
  /** Löst auf, sobald feststeht, wie gespielt wird; `null`, wenn vorher zerstört. */
  readonly ready: Promise<HlsMode | null>
  /** Beendet die Wiedergabe; ein noch laufender Start wird verworfen. */
  destroy(): void
}

/**
 * Trennt Zugangsdaten aus der URL ab. hls.js lädt per XHR, und eine URL mit
 * `user:pass@` darin taugt dafür nicht zuverlässig; die Daten wandern stattdessen
 * in einen Authorization-Header.
 */
export function splitCredentials(url: string): { url: string; authorization: string | null } {
  let u: URL
  try {
    u = new URL(url)
  } catch {
    return { url, authorization: null }
  }
  if (!u.username && !u.password) return { url, authorization: null }
  const user = decodeURIComponent(u.username)
  const pass = decodeURIComponent(u.password)
  u.username = ''
  u.password = ''
  const bytes = new TextEncoder().encode(`${user}:${pass}`)
  const authorization = 'Basic ' + btoa(String.fromCharCode(...bytes))
  return { url: u.toString(), authorization }
}

/**
 * Startet `url` im `video`. `onFatal` meldet einen Abbruch, von dem sich die
 * Wiedergabe nicht selbst erholt; das Widget zeigt dann sein Fehler-Overlay.
 */
export function startHls(
  video: HTMLVideoElement,
  url: string,
  onFatal: () => void,
  load: HlsLoader = loadHlsModule,
): HlsSession {
  let destroyed = false
  let hls: Hls | null = null

  const ready = (async (): Promise<HlsMode | null> => {
    let HlsCtor: HlsClass | null = null
    try {
      HlsCtor = (await load()).default
    } catch {
      // Chunk nicht ladbar (z. B. offline nach einem Update) → nativer Versuch.
    }
    if (destroyed) return null

    if (HlsCtor?.isSupported()) {
      const { url: plainUrl, authorization } = splitCredentials(url)
      const instance = new HlsCtor({
        xhrSetup: authorization
          ? (xhr: XMLHttpRequest) => xhr.setRequestHeader('Authorization', authorization)
          : undefined,
      })
      hls = instance
      instance.on(HlsCtor.Events.ERROR, (_event, data) => {
        if (!data.fatal || destroyed) return
        instance.destroy()
        hls = null
        onFatal()
      })
      instance.loadSource(plainUrl)
      instance.attachMedia(video)
      return 'hlsjs'
    }

    if (video.canPlayType(HLS_MIME)) {
      video.src = url
      return 'native'
    }

    onFatal()
    return 'unsupported'
  })()

  return {
    ready,
    destroy() {
      destroyed = true
      hls?.destroy()
      hls = null
    },
  }
}
