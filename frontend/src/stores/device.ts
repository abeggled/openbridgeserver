/**
 * Pinia-Store: Geräteprofil dieses Browsers (Startseite + Rücksprung).
 *
 * Die Profile selbst werden zentral im Admin-GUI gepflegt und über eine
 * öffentliche Route geladen. Welches Profil *dieses* Gerät nutzt, merkt sich
 * der Browser — gesetzt über den Link `/visu/?device=<kennung>` oder die
 * Auswahl auf der Übersicht. `?device=` ohne Wert (oder `none`) löst das
 * Gerät wieder vom Profil.
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { RouteLocationRaw } from 'vue-router'
import { visuDevices, type VisuDevice } from '@/api/client'

export const DEVICE_STORAGE_KEY = 'visu_device'

// Neuer Versuch nach einem Fehler: ab 5 s, verdoppelt bis 1 min. Danach alle 5 min,
// damit Änderungen im Admin-GUI auch ein Wandpanel erreichen, das nie neu geladen wird.
const RETRY_MIN_MS = 5_000
const RETRY_MAX_MS = 60_000
const REFRESH_INTERVAL_MS = 5 * 60_000
// Länger wartet die erste Navigation nicht auf die Profile (hängender Abruf hinter
// einem Proxy, wackliges WLAN): dann öffnet sich die Übersicht, der Rest kommt nach.
const START_LOAD_TIMEOUT_MS = 3_000

function readStoredSlug(): string | null {
  try {
    return localStorage.getItem(DEVICE_STORAGE_KEY)
  } catch {
    return null
  }
}

export const useDeviceStore = defineStore('device', () => {
  const slug = ref<string | null>(readStoredSlug())
  const devices = ref<VisuDevice[]>([])
  /** Mindestens eine Liste ist angekommen (auch eine leere). */
  const loaded = ref(false)
  let loading: Promise<void> | null = null
  let retryDelayMs = RETRY_MIN_MS
  // Jeder reload() eröffnet eine Generation; eine ältere Antwort, die später
  // eintrifft, darf weder die Liste überschreiben noch den Ladezustand zurücksetzen.
  let generation = 0

  const profile = computed(() => devices.value.find(device => device.slug === slug.value) ?? null)

  /** Wohin „Start“ führt: die Startseite des Profils, sonst (auch wenn sie gelöscht wurde) die Übersicht. */
  const startRoute = computed<RouteLocationRaw>(() =>
    profile.value?.start_node_id && !profile.value.start_node_missing
      ? { name: 'viewer', params: { id: profile.value.start_node_id } }
      : { name: 'tree' },
  )

  const returnAfterMs = computed(() => (profile.value?.return_after_s ?? 0) * 1000)

  /**
   * Lädt die Profile einmal; ein Fehler lässt die Visu ohne Profil weiterlaufen.
   * Ein Fehler wird nicht gemerkt — der nächste Aufruf versucht es erneut, und
   * eine schon geladene Liste bleibt bei einem späteren Fehler erhalten.
   */
  function fetchProfiles(own: number): Promise<void> {
    return visuDevices.list()
      .then((list) => {
        if (own !== generation) return
        devices.value = list
        loaded.value = true
        retryDelayMs = RETRY_MIN_MS
      })
      .catch(() => {
        if (own === generation) loading = null
      })
  }

  function load(): Promise<void> {
    loading ??= fetchProfiles(generation)
    return loading
  }

  /** Holt die Profile neu, auch wenn sie schon da sind. */
  function reload(): Promise<void> {
    generation += 1
    loading = fetchProfiles(generation)
    return loading
  }

  /** Wie load(), wartet aber höchstens `timeoutMs` — für die erste Navigation. */
  function loadWithTimeout(timeoutMs = START_LOAD_TIMEOUT_MS): Promise<void> {
    return Promise.race([load(), new Promise<void>((resolve) => setTimeout(resolve, timeoutMs))])
  }

  /**
   * Hält die Profile auf einem Dauerläufer aktuell: solange noch keine Liste da
   * ist, neuer Versuch mit wachsendem Abstand; danach regelmäßig sowie sobald die
   * Verbindung zurückkommt oder die Seite wieder sichtbar wird. Gibt die
   * Stop-Funktion zurück.
   */
  function keepFresh(intervalMs = REFRESH_INTERVAL_MS): () => void {
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      clearTimeout(timer)
      timer = setTimeout(async () => {
        await reload()
        // Nur ein fehlgeschlagener Versuch vergrößert den Abstand.
        if (!loaded.value) retryDelayMs = Math.min(retryDelayMs * 2, RETRY_MAX_MS)
        schedule()
      }, loaded.value ? intervalMs : retryDelayMs)
    }
    const refreshNow = () => { void reload().then(schedule) }
    const onVisibility = () => { if (document.visibilityState === 'visible') refreshNow() }
    window.addEventListener('online', refreshNow)
    document.addEventListener('visibilitychange', onVisibility)
    // Erst wenn ein noch laufender Abruf entschieden ist, steht fest, ob ein
    // Wiederholungs- oder ein Aktualisierungsabstand gilt (kein neuer Abruf hier).
    void (loading ?? Promise.resolve()).then(schedule)
    return () => {
      window.removeEventListener('online', refreshNow)
      document.removeEventListener('visibilitychange', onVisibility)
      clearTimeout(timer)
    }
  }

  function select(next: string | null) {
    slug.value = next || null
    try {
      if (slug.value) localStorage.setItem(DEVICE_STORAGE_KEY, slug.value)
      else localStorage.removeItem(DEVICE_STORAGE_KEY)
    } catch {
      // Ohne Speicher gilt die Wahl nur bis zum Neuladen.
    }
  }

  /** Übernimmt `?device=` aus der URL, falls vorhanden. */
  function applyQuery(query: Record<string, unknown>) {
    if (!('device' in query)) return
    const value = Array.isArray(query.device) ? query.device[0] : query.device
    select(typeof value === 'string' && value !== 'none' ? value : null)
  }

  return { slug, devices, loaded, profile, startRoute, returnAfterMs, load, loadWithTimeout, reload, keepFresh, select, applyQuery }
})
