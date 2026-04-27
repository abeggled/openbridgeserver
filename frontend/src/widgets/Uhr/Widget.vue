<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

// ── Konfiguration ─────────────────────────────────────────────────────────────
const mode        = computed(() => (props.config.mode        as string  | undefined) ?? 'digital')
const showSeconds = computed(() => (props.config.showSeconds as boolean | undefined) ?? false)
const showDate    = computed(() => (props.config.showDate    as boolean | undefined) ?? false)
const color       = computed(() => (props.config.color       as string  | undefined) ?? '#3b82f6')
const label       = computed(() => (props.config.label       as string  | undefined) ?? '')
const timezone    = computed(() => (props.config.timezone    as string  | undefined) ?? '')

// ── Live-Zeit ─────────────────────────────────────────────────────────────────
const now = ref(new Date())
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  timer = setInterval(() => { now.value = new Date() }, 1000)
})
onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
})

// ── Digital ───────────────────────────────────────────────────────────────────
const timeStr = computed(() => {
  const d  = now.value
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return showSeconds.value ? `${hh}:${mm}:${ss}` : `${hh}:${mm}`
})

const dateStr = computed(() =>
  now.value.toLocaleDateString('de-CH', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  }),
)

// ── Analog ────────────────────────────────────────────────────────────────────

/**
 * Gibt Stunden/Minuten/Sekunden für eine beliebige IANA-Zeitzone zurück.
 * Leerer String oder ungültige Zeitzone → lokale Zeit.
 */
function getZonedTime(date: Date, tz: string): { h: number; m: number; s: number } {
  if (!tz) return { h: date.getHours(), m: date.getMinutes(), s: date.getSeconds() }
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      hour: 'numeric', minute: 'numeric', second: 'numeric',
      hour12: false,
    }).formatToParts(date)
    const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value ?? '0', 10)
    // Stunde 24 (Mitternacht in manchen Implementierungen) → 0
    return { h: get('hour') % 24, m: get('minute'), s: get('second') }
  } catch {
    return { h: date.getHours(), m: date.getMinutes(), s: date.getSeconds() }
  }
}

const zonedTime  = computed(() => getZonedTime(now.value, timezone.value))
const hourDeg    = computed(() => (zonedTime.value.h % 12) * 30 + zonedTime.value.m * 0.5)
const minuteDeg  = computed(() => zonedTime.value.m * 6)
const secondDeg  = computed(() => zonedTime.value.s * 6)

/** Kurzes Kürzel der Zeitzone für die Anzeige unter dem Zifferblatt, z.B. "UTC+9" oder "EST" */
const timezoneLabel = computed(() => {
  if (!timezone.value) return ''
  try {
    return new Intl.DateTimeFormat('de-CH', {
      timeZone: timezone.value,
      timeZoneName: 'short',
    }).formatToParts(now.value).find(p => p.type === 'timeZoneName')?.value ?? timezone.value
  } catch {
    return timezone.value
  }
})

// ── Wortuhr ───────────────────────────────────────────────────────────────────
/**
 * 11×10 Buchstabenraster für die deutsche Wortuhr.
 *
 * Zeile 0 : E S K I S T L F Ü N F
 * Zeile 1 : Z E H N Z W A N Z I G
 * Zeile 2 : D R E I V I E R T E L
 * Zeile 3 : T G N A C H V O R J M
 * Zeile 4 : H A L B Q Z W Ö L F P
 * Zeile 5 : Z W E I N S D R E I X
 * Zeile 6 : V I E R S I E B E N A
 * Zeile 7 : A C H T Z E H N E L F
 * Zeile 8 : N E U N F Ü N F S E C
 * Zeile 9 : H S S E C H S U H R A
 */
const GRID: string[][] = [
  ['E','S','K','I','S','T','L','F','Ü','N','F'],
  ['Z','E','H','N','Z','W','A','N','Z','I','G'],
  ['D','R','E','I','V','I','E','R','T','E','L'],
  ['T','G','N','A','C','H','V','O','R','J','M'],
  ['H','A','L','B','Q','Z','W','Ö','L','F','P'],
  ['Z','W','E','I','N','S','D','R','E','I','X'],
  ['V','I','E','R','S','I','E','B','E','N','A'],
  ['A','C','H','T','Z','E','H','N','E','L','F'],
  ['N','E','U','N','F','Ü','N','F','S','E','C'],
  ['H','S','S','E','C','H','S','U','H','R','A'],
]

// [wortSchlüssel, zeile, startSpalte, länge]
const WORT_DEFINITIONEN: [string, number, number, number][] = [
  ['ES',          0,  0, 2],
  ['IST',         0,  3, 3],
  ['FÜNF_MIN',    0,  7, 4],   // FÜNF (Minuten)
  ['ZEHN_MIN',    1,  0, 4],   // ZEHN (Minuten)
  ['ZWANZIG',     1,  4, 7],
  ['DREIVIERTEL', 2,  0, 11],  // gesamte Zeile
  ['VIERTEL',     2,  4, 7],
  ['NACH',        3,  2, 4],
  ['VOR',         3,  6, 3],
  ['HALB',        4,  0, 4],
  ['H12',         4,  5, 5],   // ZWÖLF
  ['H1',          5,  2, 3],   // EIN (gemeinsame Buchstaben mit ZWEI)
  ['H2',          5,  0, 4],   // ZWEI
  ['H3',          5,  6, 4],   // DREI
  ['H4',          6,  0, 4],   // VIER
  ['H7',          6,  4, 6],   // SIEBEN
  ['H8',          7,  0, 4],   // ACHT
  ['H10',         7,  4, 4],   // ZEHN
  ['H11',         7,  8, 3],   // ELF
  ['H9',          8,  0, 4],   // NEUN
  ['H5',          8,  4, 4],   // FÜNF (Stunde)
  ['H6',          9,  2, 5],   // SECHS
  ['UHR',         9,  7, 3],
]

// Vorberechnung: welche Wörter decken jede Zelle ab?
const ZELLEN_WÖRTER: string[][][] = GRID.map(row => row.map(() => [] as string[]))
for (const [schlüssel, zeile, spalte, länge] of WORT_DEFINITIONEN) {
  for (let s = spalte; s < spalte + länge; s++) {
    ZELLEN_WÖRTER[zeile][s].push(schlüssel)
  }
}

function getAktiveWörter(stunden: number, minuten: number): Set<string> {
  const aktiv = new Set<string>(['ES', 'IST'])

  let gerundeteMinuten = Math.round(minuten / 5) * 5

  // 24h → 12h
  let h = stunden % 12
  if (h === 0) h = 12

  // Überlauf (z.B. 57 min → gerundet 60)
  const überlauf = gerundeteMinuten >= 60
  if (überlauf) gerundeteMinuten = 0

  // Ab 25 Minuten zeigen wir die nächste Stunde an
  const nächsteStunde = gerundeteMinuten >= 25 || überlauf
  let anzeigeStunde = nächsteStunde ? (h % 12) + 1 : h
  if (anzeigeStunde > 12) anzeigeStunde = 1

  switch (gerundeteMinuten) {
    case  0: aktiv.add('UHR'); break
    case  5: aktiv.add('FÜNF_MIN'); aktiv.add('NACH'); break
    case 10: aktiv.add('ZEHN_MIN'); aktiv.add('NACH'); break
    case 15: aktiv.add('VIERTEL');  aktiv.add('NACH'); break
    case 20: aktiv.add('ZWANZIG');  aktiv.add('NACH'); break
    case 25: aktiv.add('FÜNF_MIN'); aktiv.add('VOR');  aktiv.add('HALB'); break
    case 30: aktiv.add('HALB'); break
    case 35: aktiv.add('FÜNF_MIN'); aktiv.add('NACH'); aktiv.add('HALB'); break
    case 40: aktiv.add('ZWANZIG');  aktiv.add('VOR');  break
    case 45: aktiv.add('DREIVIERTEL'); break
    case 50: aktiv.add('ZEHN_MIN'); aktiv.add('VOR'); break
    case 55: aktiv.add('FÜNF_MIN'); aktiv.add('VOR'); break
  }

  aktiv.add(`H${anzeigeStunde}`)
  return aktiv
}

const aktiveWörter = computed(() =>
  getAktiveWörter(now.value.getHours(), now.value.getMinutes()),
)

function istZelleAktiv(zeile: number, spalte: number): boolean {
  return ZELLEN_WÖRTER[zeile][spalte].some(k => aktiveWörter.value.has(k))
}
</script>

<template>
  <div
    class="h-full w-full flex flex-col items-center justify-center select-none overflow-hidden"
    style="container-type: inline-size;"
    data-testid="uhr-widget"
  >

    <!-- ═══════════════════════════════════════ DIGITAL ═══ -->
    <template v-if="mode === 'digital'">
      <div class="flex flex-col items-center justify-center gap-1 w-full px-3">
        <span
          data-testid="uhr-digital-time"
          class="font-mono font-bold tabular-nums leading-none w-full text-center"
          style="font-size: clamp(1.4rem, 15cqi, 5rem);"
          :style="{ color }"
        >{{ timeStr }}</span>

        <span
          v-if="showDate"
          data-testid="uhr-digital-date"
          class="text-gray-400 dark:text-gray-500 text-center w-full leading-tight"
          style="font-size: clamp(0.55rem, 3cqi, 0.9rem);"
        >{{ dateStr }}</span>

        <span
          v-if="label"
          class="text-gray-500 dark:text-gray-400 text-center"
          style="font-size: clamp(0.5rem, 2.5cqi, 0.8rem);"
        >{{ label }}</span>
      </div>
    </template>

    <!-- ═══════════════════════════════════════ ANALOG ════ -->
    <template v-else-if="mode === 'analog'">
      <div class="flex flex-col items-center justify-center w-full h-full px-2 py-2 gap-1">
        <svg
          data-testid="uhr-analog"
          viewBox="0 0 100 100"
          class="w-full h-full"
          :style="label ? 'max-height: calc(100% - 1.4rem)' : 'max-height: 100%'"
          xmlns="http://www.w3.org/2000/svg"
        >
          <!-- Zifferblatt-Ring -->
          <circle
            cx="50" cy="50" r="48"
            fill="none"
            class="stroke-gray-200 dark:stroke-gray-700"
            stroke-width="1.5"
          />

          <!-- Stundenmarkierungen -->
          <g v-for="i in 12" :key="`hmark-${i}`">
            <line
              :x1="50 + 40 * Math.sin((i * 30) * Math.PI / 180)"
              :y1="50 - 40 * Math.cos((i * 30) * Math.PI / 180)"
              :x2="50 + 45 * Math.sin((i * 30) * Math.PI / 180)"
              :y2="50 - 45 * Math.cos((i * 30) * Math.PI / 180)"
              class="stroke-gray-400 dark:stroke-gray-500"
              stroke-width="2"
              stroke-linecap="round"
            />
          </g>

          <!-- Minutenmarkierungen (kleine Punkte) -->
          <g v-for="i in 60" :key="`mmark-${i}`">
            <line
              v-if="i % 5 !== 0"
              :x1="50 + 43 * Math.sin((i * 6) * Math.PI / 180)"
              :y1="50 - 43 * Math.cos((i * 6) * Math.PI / 180)"
              :x2="50 + 46 * Math.sin((i * 6) * Math.PI / 180)"
              :y2="50 - 46 * Math.cos((i * 6) * Math.PI / 180)"
              class="stroke-gray-300 dark:stroke-gray-600"
              stroke-width="0.7"
              stroke-linecap="round"
            />
          </g>

          <!-- Stundenzeiger -->
          <line
            :transform="`rotate(${hourDeg}, 50, 50)`"
            x1="50" y1="50"
            x2="50" y2="23"
            stroke-width="3.5"
            stroke-linecap="round"
            :stroke="color"
          />

          <!-- Minutenzeiger -->
          <line
            :transform="`rotate(${minuteDeg}, 50, 50)`"
            x1="50" y1="52"
            x2="50" y2="12"
            stroke-width="2.5"
            stroke-linecap="round"
            :stroke="color"
          />

          <!-- Sekundenzeiger -->
          <line
            v-if="showSeconds"
            :transform="`rotate(${secondDeg}, 50, 50)`"
            x1="50" y1="58"
            x2="50" y2="9"
            stroke-width="1"
            stroke-linecap="round"
            stroke="#ef4444"
          />

          <!-- Mittelachse -->
          <circle cx="50" cy="50" r="3" :fill="color" />
          <circle v-if="showSeconds" cx="50" cy="50" r="1.5" fill="#ef4444" />
        </svg>

        <!-- Beschriftung + optionale Zeitzone -->
        <div
          v-if="label || timezoneLabel"
          class="flex items-center justify-center gap-1.5 shrink-0"
        >
          <span
            v-if="label"
            class="text-gray-500 dark:text-gray-400 text-xs text-center truncate leading-none"
          >{{ label }}</span>
          <span
            v-if="timezoneLabel"
            class="text-xs font-mono leading-none px-1 py-0.5 rounded"
            :style="{ color, backgroundColor: color + '22' }"
          >{{ timezoneLabel }}</span>
        </div>
      </div>
    </template>

    <!-- ═══════════════════════════════════════ WORTUHR ═══ -->
    <template v-else-if="mode === 'wortuhr'">
      <div
        data-testid="uhr-wortuhr"
        class="w-full h-full flex flex-col items-center justify-center p-1"
      >
        <!-- Buchstabenraster 11 × 10 -->
        <div
          class="grid w-full"
          style="
            grid-template-columns: repeat(11, 1fr);
            grid-template-rows: repeat(10, 1fr);
            flex: 1;
            min-height: 0;
          "
        >
          <template v-for="(zeile, zi) in GRID" :key="zi">
            <div
              v-for="(buchstabe, si) in zeile"
              :key="`${zi}-${si}`"
              class="flex items-center justify-center font-bold uppercase transition-colors duration-300 leading-none"
              style="font-size: clamp(0.35rem, 6cqi, 1rem);"
              :style="istZelleAktiv(zi, si) ? { color } : {}"
              :class="istZelleAktiv(zi, si)
                ? ''
                : 'text-gray-300 dark:text-gray-700'"
            >{{ buchstabe }}</div>
          </template>
        </div>

        <span
          v-if="label"
          class="text-gray-500 dark:text-gray-400 text-center mt-0.5 shrink-0"
          style="font-size: clamp(0.5rem, 2.5cqi, 0.75rem);"
        >{{ label }}</span>
      </div>
    </template>

  </div>
</template>
