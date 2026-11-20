/**
 * Lebenszyklus eines go2rtc-Streams im Kamera-Widget (#1479).
 *
 * - läuft nur, solange das Widget sichtbar ist (IntersectionObserver) und der Tab im
 *   Vordergrund (`visibilitychange`) — ein verdecktes Widget belastet weder go2rtc noch das Netz
 * - startet neu, wenn sich Stream, Modi oder Anmeldung ändern
 * - verbindet nach einem Abbruch mit wachsendem Abstand neu (2 s … 30 s)
 */
import { onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { Go2rtcPlayer, type PlayerMode, type SocketTarget } from './go2rtcPlayer'

export const RECONNECT_MIN_MS = 2000
export const RECONNECT_MAX_MS = 30000

export interface Go2rtcStreamOptions {
  container: Ref<HTMLElement | null>
  video: Ref<HTMLVideoElement | null>
  /** Konfiguriert und nicht im Editor. */
  active: Ref<boolean>
  src: Ref<string>
  modes: Ref<PlayerMode[]>
  /** Ändert sich, wenn sich die Anmeldung ändert (Token-Refresh, Abmeldung). */
  authKey: Ref<string>
  resolve: (mode: PlayerMode, src: string) => Promise<SocketTarget | null>
}

export function useGo2rtcStream(opts: Go2rtcStreamOptions) {
  const mode = ref<PlayerMode | null>(null)
  const playing = ref(false)
  const failed = ref(false)
  const hasAudio = ref(false)
  const inView = ref(true)
  const pageVisible = ref(typeof document === 'undefined' || document.visibilityState !== 'hidden')

  let player: Go2rtcPlayer | null = null
  let observer: IntersectionObserver | null = null
  let retryTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelay = RECONNECT_MIN_MS

  function clearRetry() {
    if (retryTimer !== null) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
  }

  function shouldRun(): boolean {
    return opts.active.value && inView.value && pageVisible.value && !!opts.src.value && !!opts.video.value
  }

  function stop() {
    clearRetry()
    player?.stop()
    player = null
    playing.value = false
    mode.value = null
    hasAudio.value = false
  }

  function restart() {
    stop()
    failed.value = false
    if (!shouldRun()) return
    const src = opts.src.value
    player = new Go2rtcPlayer({
      video: opts.video.value as HTMLVideoElement,
      modes: opts.modes.value,
      target: (m) => opts.resolve(m, src),
      onEvent: (event) => {
        if (event.type === 'mode') mode.value = event.mode
        else if (event.type === 'audio') hasAudio.value = event.hasAudio
        else if (event.type === 'playing') {
          playing.value = true
          failed.value = false
          retryDelay = RECONNECT_MIN_MS
        } else {
          playing.value = false
          failed.value = true
          scheduleRetry()
        }
      },
    })
    void player.start()
  }

  function scheduleRetry() {
    clearRetry()
    const delay = retryDelay
    retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS)
    retryTimer = setTimeout(() => {
      retryTimer = null
      restart()
    }, delay)
  }

  function onVisibility() {
    pageVisible.value = document.visibilityState !== 'hidden'
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    if (typeof IntersectionObserver !== 'undefined' && opts.container.value) {
      observer = new IntersectionObserver((entries) => {
        inView.value = entries.some(e => e.isIntersecting)
      })
      observer.observe(opts.container.value)
    }
    watch(
      [opts.active, inView, pageVisible, opts.src, () => opts.modes.value.join(','), opts.authKey, opts.video],
      () => {
        retryDelay = RECONNECT_MIN_MS
        restart()
      },
      { immediate: true },
    )
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisibility)
    observer?.disconnect()
    observer = null
    stop()
  })

  return { mode, playing, failed, hasAudio, restart }
}
