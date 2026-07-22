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
 * sobald der Job terminal ist (done/failed). Transiente Serverfehler werden
 * weiter im 1-s-Intervall versucht; nicht retryfähige Fehler stoppen den Poller.
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
// Monotoner Abschlusszähler für Consumer der getrennten /stats-Quelle. Der
// Migrationsstatus und die Segmentstatistik werden über verschiedene Endpoints
// geladen; ohne explizite Invalidierung kann nach ``phase=done`` kurz die alte
// Legacy-Manifestzeile sichtbar bleiben.
const completionRevision = ref(0)
let pollTimer = null

const decision = computed(() => status.value?.decision ?? null)
const legacy = computed(() => status.value?.legacy ?? null)
const job = computed(() => status.value?.job ?? null)
const jobRunning = computed(() => RUNNING_JOB_PHASES.has(job.value?.phase))

// Post-Commit-Retry-Fenster (#968, Codex :72): der Commit ist durch (keine Legacy mehr), aber die
// terminale Entscheidung wurde noch nicht persistiert (transienter app-DB-Fehler). Der Status-
// Endpoint zieht sie beim nächsten Poll nach – deshalb muss weitergepollt werden, bis die
// Entscheidung terminal ist. Sonst bliebe die bereits committete Migration als pending/skipped
// hängen (Banner + Segment-Eintrag verschwinden bei ``legacy === null``, ein Reload/Neustart wäre
// nötig).
// Nur die RETRY-fähigen non-terminalen Entscheidungen zählen als „Finalisierung ausstehend"
// (#968, Codex :48): pending/skipped (bzw. keine Entscheidung) warten nach einem Commit auf das
// Nachziehen von ``migrated``. ``keep`` ist dagegen eine BEWUSSTE non-terminale Entscheidung – der
// Backend-Finalizer wandelt sie absichtlich NICHT in ``migrated`` um. Würde ``keep`` hier als
// pending gewertet, liefe der 1-s-Poller nach einem retention-bedingten Entfernen der ge-keepten
// Quelle (``legacy === null``) endlos weiter.
// Nach einem Neustart mit Startup-Reconciler ist ``job.phase`` nicht mehr ``done`` (sondern
// ``idle``/``null``) – der Poller muss dennoch laufen, wenn eine non-null RETRY-fähige Entscheidung
// aussteht (#968, Codex :55). ``null``-decision (Fresh-Install) bleibt explizit ausgeschlossen.
const RETRYABLE_PENDING_DECISIONS = new Set(['pending', 'skipped', null])
const pendingFinalization = computed(
  () =>
    legacy.value === null &&
    RETRYABLE_PENDING_DECISIONS.has(decision.value) &&
    (job.value?.phase === 'done' || decision.value != null),
)

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
  if (jobRunning.value || pendingFinalization.value) {
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
  const previousPhase = status.value?.job?.phase ?? null
  const nextPhase = data?.job?.phase ?? null
  status.value = data
  loadError.value = false
  if (RUNNING_JOB_PHASES.has(previousPhase) && nextPhase === 'done') {
    completionRevision.value += 1
  }
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
    const responseStatus = error?.response?.status
    if (responseStatus >= 500 && responseStatus < 600) {
      // Der Status-Reconciler kann bei einem transienten app-DB-Lock mit 5xx
      // antworten. Auch beim initialen Dashboard-Refresh automatisch erneut
      // versuchen, damit die geschützte Legacy-Quelle nach erfolgreichem Repair
      // ohne manuellen Reload wieder bedienbar wird.
      if (pollTimer == null) {
        pollTimer = setInterval(() => {
          refresh().catch(() => {})
        }, POLL_INTERVAL_MS)
      }
    } else {
      // Auth-/Clientfehler sind nicht durch Wiederholen heilbar.
      stopPolling()
    }
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
    completionRevision,
    decision,
    legacy,
    job,
    jobRunning,
    pendingFinalization,
    showBanner,
    escalated,
    refresh,
    decide,
    startMigration,
  }
}
