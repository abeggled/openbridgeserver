<template>
  <div class="mt-2">
    <label class="label mb-1">Benutzerdefinierte Feiertage</label>
    <p class="text-xs text-slate-400 mb-2">
      Ergänzen Sie den Feiertagskalender um eigene Feiertage (z.B. regionale oder betriebliche Feiertage).
    </p>

    <!-- Existing entries -->
    <div v-if="modelValue.length" class="space-y-1 mb-3">
      <div
        v-for="(entry, i) in modelValue"
        :key="i"
        class="flex items-center gap-2 px-3 py-1.5 bg-slate-50 dark:bg-slate-800 rounded border border-slate-200 dark:border-slate-700"
      >
        <span class="text-xs font-mono text-slate-600 dark:text-slate-300 flex-1 min-w-0 truncate" :title="entry">
          {{ formatEntry(entry) }}
        </span>
        <span class="text-xs text-slate-400 dark:text-slate-500 font-mono flex-shrink-0">{{ entry }}</span>
        <button
          type="button"
          title="Entfernen"
          class="text-slate-400 hover:text-red-400 text-base leading-none flex-shrink-0 transition-colors px-0.5"
          @click="removeEntry(i)"
        >×</button>
      </div>
    </div>
    <div v-else class="text-xs text-slate-400 italic mb-3">Keine benutzerdefinierten Feiertage konfiguriert.</div>

    <!-- Add new entry form -->
    <div class="border border-slate-200 dark:border-slate-700 rounded p-3 space-y-2 bg-slate-50/50 dark:bg-slate-800/30">
      <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Neuer Eintrag</div>

      <!-- Type selector -->
      <div class="form-group">
        <label class="label">Typ</label>
        <select v-model="form.type" class="input text-sm">
          <option value="fixed">Festes Datum (z.B. 26. März)</option>
          <option value="easter">Relativ zu Ostern (z.B. Karfreitag = Ostern−2)</option>
          <option value="advent">Relativ zum 1. Advent (z.B. advent+0 = 1. Advent)</option>
          <option value="last_weekday">Letzter Wochentag im Monat</option>
          <option value="nth_weekday">N-ter Wochentag im Monat</option>
        </select>
      </div>

      <!-- Fixed date inputs -->
      <template v-if="form.type === 'fixed'">
        <div class="grid grid-cols-2 gap-2">
          <div class="form-group">
            <label class="label">Monat</label>
            <select v-model="form.month" class="input text-sm">
              <option v-for="m in MONTHS" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Tag (1–31)</label>
            <input v-model.number="form.day" type="number" min="1" max="31" class="input text-sm" />
          </div>
        </div>
      </template>

      <!-- Ostern-relativ inputs -->
      <template v-if="form.type === 'easter'">
        <div class="form-group">
          <label class="label">Abstand von Ostersonntag</label>
          <div class="flex gap-2 items-center">
            <select v-model="form.easterSign" class="input text-sm" style="width: 4rem">
              <option value="+">+</option>
              <option value="-">−</option>
            </select>
            <input v-model.number="form.easterOffset" type="number" min="0" max="400" class="input text-sm flex-1" placeholder="Tage" />
            <span class="text-xs text-slate-400 whitespace-nowrap flex-shrink-0">Tage</span>
          </div>
          <p class="hint">Beispiele: 0 = Ostersonntag, +1 = Ostermontag, −2 = Karfreitag, −47 = Rosenmontag</p>
        </div>
      </template>

      <!-- 1. Advent-relativ inputs -->
      <template v-if="form.type === 'advent'">
        <div class="form-group">
          <label class="label">Abstand vom 1. Advent</label>
          <div class="flex gap-2 items-center">
            <select v-model="form.easterSign" class="input text-sm" style="width: 4rem">
              <option value="+">+</option>
              <option value="-">−</option>
            </select>
            <input v-model.number="form.easterOffset" type="number" min="0" max="400" class="input text-sm flex-1" placeholder="Tage" />
            <span class="text-xs text-slate-400 whitespace-nowrap flex-shrink-0">Tage</span>
          </div>
          <p class="hint">Beispiele: 0 = 1. Advent, +7 = 2. Advent, +14 = 3. Advent, +21 = 4. Advent, +24 = Heiligabend</p>
        </div>
      </template>

      <!-- Last weekday of month inputs -->
      <template v-if="form.type === 'last_weekday'">
        <div class="grid grid-cols-2 gap-2">
          <div class="form-group">
            <label class="label">Wochentag</label>
            <select v-model="form.weekday" class="input text-sm">
              <option v-for="wd in WEEKDAYS" :key="wd.value" :value="wd.value">{{ wd.label }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Monat</label>
            <select v-model="form.month" class="input text-sm">
              <option v-for="m in MONTHS" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>
        </div>
      </template>

      <!-- Nth weekday of month inputs -->
      <template v-if="form.type === 'nth_weekday'">
        <div class="grid grid-cols-3 gap-2">
          <div class="form-group">
            <label class="label">N-ter</label>
            <select v-model="form.n" class="input text-sm">
              <option value="1">1.</option>
              <option value="2">2.</option>
              <option value="3">3.</option>
              <option value="4">4.</option>
              <option value="5">5.</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Wochentag</label>
            <select v-model="form.weekday" class="input text-sm">
              <option v-for="wd in WEEKDAYS" :key="wd.value" :value="wd.value">{{ wd.label }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Monat</label>
            <select v-model="form.month" class="input text-sm">
              <option v-for="m in MONTHS" :key="m.value" :value="m.value">{{ m.label }}</option>
            </select>
          </div>
        </div>
      </template>

      <!-- Name (always shown) -->
      <div class="form-group">
        <label class="label">Bezeichnung</label>
        <input v-model="form.name" type="text" class="input text-sm" placeholder="z.B. open bridge server Geburtstag" @keyup.enter="addEntry" />
      </div>

      <!-- Preview of generated entry string -->
      <div v-if="previewEntry" class="text-xs font-mono text-blue-500 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1">
        Eintrag: {{ previewEntry }}
      </div>

      <div class="flex justify-end pt-1">
        <button
          type="button"
          :disabled="!canAdd"
          class="btn-primary btn-sm"
          :class="{ 'opacity-40 cursor-not-allowed': !canAdd }"
          @click="addEntry"
        >+ Hinzufügen</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, computed } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

// ── Constants ─────────────────────────────────────────────────────────────

const WEEKDAYS = [
  { value: 'MO', label: 'Montag' },
  { value: 'DI', label: 'Dienstag' },
  { value: 'MI', label: 'Mittwoch' },
  { value: 'DO', label: 'Donnerstag' },
  { value: 'FR', label: 'Freitag' },
  { value: 'SA', label: 'Samstag' },
  { value: 'SO', label: 'Sonntag' },
]

const MONTHS = [
  { value: 'JAN', label: 'Januar',    num: '01' },
  { value: 'FEB', label: 'Februar',   num: '02' },
  { value: 'MAR', label: 'März',      num: '03' },
  { value: 'APR', label: 'April',     num: '04' },
  { value: 'MAI', label: 'Mai',       num: '05' },
  { value: 'JUN', label: 'Juni',      num: '06' },
  { value: 'JUL', label: 'Juli',      num: '07' },
  { value: 'AUG', label: 'August',    num: '08' },
  { value: 'SEP', label: 'September', num: '09' },
  { value: 'OKT', label: 'Oktober',   num: '10' },
  { value: 'NOV', label: 'November',  num: '11' },
  { value: 'DEZ', label: 'Dezember',  num: '12' },
]

const MONTH_MAP = Object.fromEntries(MONTHS.map(m => [m.value, m]))
const WEEKDAY_MAP = Object.fromEntries(WEEKDAYS.map(w => [w.value, w]))

// ── Form state ────────────────────────────────────────────────────────────

const form = reactive({
  type:        'fixed',
  month:       'JAN',
  day:         1,
  easterSign:  '+',
  easterOffset: 1,
  weekday:     'MO',
  n:           '1',
  name:        '',
})

// ── Entry building ────────────────────────────────────────────────────────

function buildEntry() {
  const name = form.name.trim() || 'Feiertag'
  switch (form.type) {
    case 'fixed': {
      const mn = MONTH_MAP[form.month]
      if (!mn) return null
      const mm = mn.num
      const dd = String(form.day).padStart(2, '0')
      return `${mm}-${dd}:${name}`
    }
    case 'easter': {
      const offset = Number(form.easterOffset) || 0
      if (offset === 0) return `easter+0:${name}`
      return `easter${form.easterSign}${offset}:${name}`
    }
    case 'advent': {
      const offset = Number(form.easterOffset) || 0
      if (offset === 0) return `advent+0:${name}`
      return `advent${form.easterSign}${offset}:${name}`
    }
    case 'last_weekday':
      return `last_${form.weekday}_${form.month}:${name}`
    case 'nth_weekday':
      return `${form.n}_${form.weekday}_${form.month}:${name}`
    default:
      return null
  }
}

const previewEntry = computed(() => {
  if (!form.name.trim()) return null
  return buildEntry()
})

const canAdd = computed(() => {
  if (!form.name.trim()) return false
  if (form.type === 'fixed' && (form.day < 1 || form.day > 31)) return false
  return true
})

function addEntry() {
  if (!canAdd.value) return
  const entry = buildEntry()
  if (!entry) return
  emit('update:modelValue', [...props.modelValue, entry])
  form.name = ''
  form.day = 1
  form.easterOffset = 1
}

function removeEntry(index) {
  const updated = [...props.modelValue]
  updated.splice(index, 1)
  emit('update:modelValue', updated)
}

// ── Display formatting ────────────────────────────────────────────────────

function formatEntry(entry) {
  const colonIdx = entry.indexOf(':')
  const expr = colonIdx >= 0 ? entry.slice(0, colonIdx).trim() : entry.trim()
  const name = colonIdx >= 0 ? entry.slice(colonIdx + 1).trim() : ''
  const label = name || 'Feiertag'
  const exprUp = expr.toUpperCase()

  // Fixed date MM-TT
  const fixedMatch = expr.match(/^(\d{1,2})-(\d{1,2})$/)
  if (fixedMatch) {
    const mon = MONTHS[parseInt(fixedMatch[1], 10) - 1]
    const day = parseInt(fixedMatch[2], 10)
    return `${day}. ${mon ? mon.label : fixedMatch[1]}: ${label}`
  }

  // Ostern-relativ
  const easterMatch = exprUp.match(/^EASTER([+-]\d+)?$/)
  if (easterMatch) {
    const offset = easterMatch[1] ? parseInt(easterMatch[1], 10) : 0
    if (offset === 0) return `Ostersonntag: ${label}`
    return `Ostern ${offset > 0 ? '+' : ''}${offset} Tage: ${label}`
  }

  // 1. Advent-relativ
  const adventMatch = exprUp.match(/^ADVENT([+-]\d+)?$/)
  if (adventMatch) {
    const offset = adventMatch[1] ? parseInt(adventMatch[1], 10) : 0
    if (offset === 0) return `1. Advent: ${label}`
    return `1. Advent ${offset > 0 ? '+' : ''}${offset} Tage: ${label}`
  }

  // Last / Nth weekday
  const wdMatch = exprUp.match(/^(LAST|\d+)_([A-Z]{2,3})_([A-Z]{2,3})$/)
  if (wdMatch) {
    const [, nth, wd, mon] = wdMatch
    const wdName = WEEKDAY_MAP[wd]?.label ?? wd
    const monObj = MONTH_MAP[mon] ?? MONTH_MAP[mon === 'MRZ' ? 'MAR' : mon === 'OCT' ? 'OKT' : mon === 'DEC' ? 'DEZ' : mon]
    const monName = monObj?.label ?? mon
    if (nth === 'LAST') return `Letzter ${wdName} im ${monName}: ${label}`
    return `${nth}. ${wdName} im ${monName}: ${label}`
  }

  return entry
}
</script>
