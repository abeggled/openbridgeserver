/**
 * useLegacyMigration (#966) – geteilter Zustand des Migrations-Assistenten
 * für den Legacy-Altbestand des RingBuffers.
 *
 * EINE Quelle der Wahrheit für Banner (RingBufferView + Dashboard-Karte),
 * Wizard-Modal und den Einstieg im Segment-Status-Panel: der Status aus
 * ``GET /ringbuffer/migration`` liegt als Modul-Singleton vor, damit eine
 * Entscheidung (skip/keep/discard/migrate) sofort überall sichtbar wird und
 * der Banner nach Abschluss ohne Extra-Wiring verschwindet.
 *
 * Der Endpoint ist admin-only – Aufrufer müssen ``refresh()`` hinter
 * ``auth.isAdmin`` gaten (403 für Nicht-Admins).
 *
 * Während ein Migrationsjob läuft (phase starting/precheck/copying/committing)
 * pollt der Composable den Status im 1-s-Intervall und stoppt selbsttätig,
 * sobald der Job terminal ist (done/failed) oder ein Refresh fehlschlägt.
 */
import { computed, ref } from 'vue'
import { ringbufferApi } from '@/api/client'

/** Job-Phasen, in denen der Hintergrundjob aktiv ist → Status-Polling. */
export const RUNNING_JOB_PHASES = new Set(['starting', 'precheck', 'copying', 'committing'])

/** Eskalation, wenn das Budget die Alt-Historie in < 7 Tagen erzwingt. */
export const ESCALATION_WINDOW_SECONDS = 7 * 24 * 3600

const POLL_INTERVAL_MS = 1000

// ── Modul-Singleton-Zustand ────────────────────────────────────────────────
const status = ref(null)
const loading = ref(false)
const loadError = ref(false)
let pollTimer = null

const decision = computed(() => status.value?.decision ?? null)
const legacy = computed(() => status.value?.legacy ?? null)
const job = computed(() => status.value?.job ?? null)
const jobRunning = computed(() => RUNNING_JOB_PHASES.has(job.value?.phase))

// Banner nur solange KEINE informierte Entscheidung vorliegt und die
// Legacy-Quelle noch existiert (#966).
const showBanner = computed(() => decision.value === 'pending' && legacy.value != null)

// Eskalation (#966): Budget bereits überschritten (eta === 0 inklusive) oder
// Prognose sagt Budget-Druck innerhalb von 7 Tagen voraus.
const escalated = computed(() => {
  const s = status.value
  if (!s) return false
  if (s.over_budget) return true
  const eta = s.estimated_seconds_until_budget
  return eta !== null && eta !== undefined && Number(eta) < ESCALATION_WINDOW_SECONDS
})

function stopPolling() {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** Startet/stoppt das 1-s-Polling abhängig von der aktuellen Job-Phase. */
function syncPolling() {
  if (jobRunning.value) {
    if (pollTimer == null) {
      pollTimer = setInterval(() => {
        // refresh() wirft weiter (für Aufrufer mit eigener Fehlerbehandlung);
        // der Poll-Tick schluckt den Fehler – refresh() stoppt das Polling selbst.
        refresh().catch(() => {})
      }, POLL_INTERVAL_MS)
    }
  } else {
    stopPolling()
  }
}

function applyStatus(data) {
  status.value = data
  loadError.value = false
  syncPolling()
}

async function refresh() {
  loading.value = true
  try {
    const { data } = await ringbufferApi.migrationStatus()
    applyStatus(data)
    return data
  } catch (error) {
    loadError.value = true
    // Kein Endlos-Polling gegen einen fehlschlagenden Endpoint.
    stopPolling()
    throw error
  } finally {
    loading.value = false
  }
}

/**
 * Setzt die Assistenten-Entscheidung (``skip`` | ``keep`` | ``discard``) und
 * übernimmt den zurückgegebenen Status direkt in den geteilten Zustand.
 */
async function decide(decisionValue) {
  const { data } = await ringbufferApi.migrationDecision(decisionValue)
  applyStatus(data)
  return data
}

/** Startet den Offline-Migrationsjob; Fortschritt kommt über das Polling. */
async function startMigration() {
  const { data } = await ringbufferApi.migrationStart()
  applyStatus(data)
  return data
}

export function useLegacyMigration() {
  return {
    status,
    loading,
    loadError,
    decision,
    legacy,
    job,
    jobRunning,
    showBanner,
    escalated,
    refresh,
    decide,
    startMigration,
  }
}
