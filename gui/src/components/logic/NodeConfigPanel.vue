<template>
  <div v-if="node" class="h-full flex flex-col bg-surface-800 border-l border-slate-200 dark:border-slate-700/60 w-72">

    <!-- Header -->
    <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-200">{{ nodeDef?.label ?? node.type }}</h3>
      <button @click="$emit('close')" class="btn-icon text-slate-500">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>

    <!-- ── DataPoint nodes: tab UI ────────────────────────────────────── -->
    <template v-if="isDatapointNode">

      <!-- Tab bar -->
      <div class="flex border-b border-slate-200 dark:border-slate-700/60">
        <button v-for="tab in tabs" :key="tab.id"
          @click="activeTab = tab.id"
          :class="['tab-btn', activeTab === tab.id && 'tab-btn--active']">
          {{ tab.label }}
          <span v-if="tab.dot" class="tab-dot">•</span>
        </button>
      </div>

      <div class="flex-1 overflow-y-auto">

        <!-- Verbindung -->
        <div v-show="activeTab === 'connection'" class="p-4 flex flex-col gap-3">
          <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>
          <div class="form-group">
            <label class="label">Objekt</label>
            <input v-model="dpSearch" type="text" class="input text-sm" placeholder="Suchen…" @input="searchDps" />
            <div v-if="dpResults.length"
              class="mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden max-h-40 overflow-y-auto">
              <button v-for="dp in dpResults" :key="dp.id"
                @click="selectDp(dp)"
                class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200">
                {{ dp.name }}
                <span class="text-slate-500 ml-1">{{ dp.data_type }}</span>
              </button>
            </div>
            <div v-if="localData.datapoint_name" class="mt-1 text-xs text-teal-400">
              ✓ {{ localData.datapoint_name }}
            </div>
          </div>
        </div>

        <!-- Transformation -->
        <div v-show="activeTab === 'transform'" class="p-4 flex flex-col gap-3">
          <div class="section-label">Wert-Transformation</div>
          <div class="form-group">
            <label class="label">Formel <span class="text-slate-500 font-normal">— Variable: <code class="text-teal-400">x</code></span></label>
            <div class="flex gap-2">
              <select v-model="formulaPreset" @change="onPresetChange" class="input text-xs flex-1 min-w-0">
                <option value="">— Preset wählen —</option>
                <optgroup label="Multiplizieren">
                  <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup label="Dividieren">
                  <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup label="Benutzerdefiniert">
                  <option value="__custom__">Eigene Formel …</option>
                </optgroup>
              </select>
              <input
                v-model="localData.value_formula"
                @input="onFormulaInput"
                @change="emitUpdate"
                class="input text-xs font-mono w-28 shrink-0"
                placeholder="x * 100" />
            </div>
            <p class="text-xs text-slate-500 mt-1">
              Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
              und alle <code class="text-slate-400">math.*</code>-Funktionen.
              Leer = keine Transformation.
            </p>
          </div>

          <div class="section-label mt-1">Wertzuordnung</div>
          <div class="form-group">
            <label class="label">Wertzuordnung <span class="text-slate-500 font-normal text-xs">(optional)</span></label>
            <select v-model="valueMapPreset" @change="onValueMapPresetChange" class="input text-xs">
              <option v-for="p in VALUE_MAP_PRESETS" :key="p.key" :value="p.key">{{ p.label }}</option>
            </select>
            <div v-if="valueMapPreset === 'custom'" class="mt-1">
              <textarea
                v-model="valueMapCustom"
                @change="onValueMapCustomChange"
                class="input text-xs font-mono h-16 resize-y"
                placeholder='{"0": "off", "1": "on"}'
              />
              <p class="text-xs text-slate-500 mt-0.5">JSON-Objekt mit String-Schlüsseln und -Werten.</p>
            </div>
            <p class="text-xs text-slate-500 mt-1">Wird nach der Formel angewendet.</p>
          </div>
        </div>

        <!-- Filter -->
        <div v-show="activeTab === 'filter'" class="p-4 flex flex-col gap-4">

          <div>
            <div class="section-label">Zeitlicher Filter</div>
            <label class="label mt-2">Min. Zeitabstand zwischen zwei {{ isWrite ? 'Schreibvorgängen' : 'Auslösungen' }}</label>
            <div class="flex gap-2 mt-1">
              <input
                v-model="localData.throttle_value"
                @change="emitUpdate"
                type="number" min="0"
                class="input text-sm flex-1"
                placeholder="z.B. 1" />
              <select v-model="localData.throttle_unit" @change="emitUpdate" class="input text-sm w-20 shrink-0">
                <option value="ms">ms</option>
                <option value="s">s</option>
                <option value="min">min</option>
                <option value="h">h</option>
              </select>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              {{ isWrite ? 'Schreibvorgänge' : 'Auslösungen' }} innerhalb des Intervalls werden verworfen.
            </p>
          </div>

          <div>
            <div class="section-label">Wert-Filter</div>

            <label class="flex items-start gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                :checked="boolVal(isWrite ? 'only_on_change' : 'trigger_on_change')"
                @change="e => { setBool(isWrite ? 'only_on_change' : 'trigger_on_change', e.target.checked); emitUpdate() }"
                class="mt-0.5 accent-teal-500" />
              <span class="text-xs text-slate-600 dark:text-slate-300 leading-snug">
                {{ isWrite
                  ? 'Nur schreiben wenn Wert sich geändert hat (kein Duplikat)'
                  : 'Nur auslösen wenn Wert sich geändert hat (kein Duplikat)' }}
              </span>
            </label>

            <label class="label mt-3">Nur {{ isWrite ? 'schreiben' : 'auslösen' }} bei Mindest-Abweichung</label>
            <div class="flex gap-2 mt-1">
              <div class="flex-1">
                <input
                  v-model="localData.min_delta"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  placeholder="z.B. 0.5" />
                <p class="text-xs text-slate-600 mt-0.5">Absolut</p>
              </div>
              <div v-if="!isWrite" class="flex-1">
                <input
                  v-model="localData.min_delta_pct"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  placeholder="z.B. 2" />
                <p class="text-xs text-slate-600 mt-0.5">Relativ (%)</p>
              </div>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              Leer = inaktiv. {{ !isWrite ? 'Beide aktiv = beide müssen erfüllt sein. ' : '' }}Nur für numerische Werte.
            </p>
          </div>
        </div>

      </div>
    </template>

    <!-- ── Trigger node: cron builder ──────────────────────────────────── -->
    <template v-else-if="isCronNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Presets -->
        <div class="form-group">
          <label class="label">Vorgefertigte Zeitpläne</label>
          <select :value="cronPresetValue" @change="onCronPresetChange" class="input text-sm">
            <option value="">— Preset wählen —</option>
            <optgroup label="Minuten-Intervalle">
              <option v-for="p in CRON_PRESETS_INTERVAL" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Stunden-Intervalle">
              <option v-for="p in CRON_PRESETS_HOURLY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Täglich">
              <option v-for="p in CRON_PRESETS_DAILY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Wöchentlich / Monatlich">
              <option v-for="p in CRON_PRESETS_OTHER" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
          </select>
          <p v-if="cronDescription" class="text-xs text-amber-400 mt-1">▶ {{ cronDescription }}</p>
        </div>

        <!-- Visual field builder -->
        <div class="form-group">
          <label class="label">Zeitplan anpassen</label>
          <div class="cron-grid mt-1">
            <div v-for="f in cronFields" :key="f.key" class="cron-field">
              <input
                v-model="f.value"
                @input="onCronFieldChange"
                class="input text-sm text-center font-mono px-1"
                :placeholder="f.placeholder"
                :title="f.label + ' (' + f.hint + ')'"
              />
              <span class="cron-field-label">{{ f.label }}</span>
              <span class="cron-field-hint">{{ f.hint }}</span>
            </div>
          </div>
          <div class="cron-legend mt-2">
            <span><code>*</code> jeden</span>
            <span><code>*/5</code> alle 5</span>
            <span><code>1-5</code> Bereich</span>
            <span><code>1,3</code> Liste</span>
          </div>
        </div>

        <!-- Raw expression -->
        <div class="form-group">
          <label class="label">Ausdruck (direkt bearbeiten)</label>
          <input
            v-model="localData.cron"
            @change="onCronExprChange"
            class="input text-sm font-mono"
            placeholder="0 7 * * *"
          />
          <p class="text-xs text-slate-500 mt-1">
            Felder: <code class="text-slate-400">Minute · Stunde · Tag · Monat · Wochentag</code>
            — <a href="https://crontab.guru" target="_blank" rel="noopener"
               class="text-amber-400 hover:underline">crontab.guru ↗</a>
          </p>
        </div>
      </div>
    </template>

    <!-- ── math_formula: Formel + Ausgangs-Transformation ──────────────── -->
    <template v-else-if="isMathFormulaNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Hauptformel -->
        <div class="form-group">
          <div class="section-label">Hauptformel</div>
          <label class="label">Formel <span class="text-slate-500 font-normal">— Variablen: <code class="text-teal-400">a</code>, <code class="text-teal-400">b</code></span></label>
          <input
            v-model="localData.formula"
            @change="emitUpdate"
            class="input text-sm font-mono"
            placeholder="a + b" />
          <p class="text-xs text-slate-500 mt-1">
            Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
            und alle <code class="text-slate-400">math.*</code>-Funktionen.
          </p>
        </div>

        <!-- Ausgangs-Transformation -->
        <div class="form-group">
          <div class="section-label">Ausgangs-Transformation</div>
          <label class="label">Formel <span class="text-slate-500 font-normal">— Variable: <code class="text-teal-400">x</code> (= Ergebnis)</span></label>
          <div class="flex gap-2">
            <select :value="outputFormulaPreset" @change="onOutputPresetChange" class="input text-xs flex-1 min-w-0">
              <option value="">— Preset wählen —</option>
              <optgroup label="Multiplizieren">
                <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup label="Dividieren">
                <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup label="Benutzerdefiniert">
                <option value="__custom__">Eigene Formel …</option>
              </optgroup>
            </select>
            <input
              v-model="localData.output_formula"
              @change="emitUpdate"
              class="input text-xs font-mono w-28 shrink-0"
              placeholder="x * 100" />
          </div>
          <p class="text-xs text-slate-500 mt-1">
            Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
            und alle <code class="text-slate-400">math.*</code>-Funktionen.
            Leer = keine Transformation.
          </p>
        </div>
      </div>
    </template>

    <!-- ── All other node types: generic rendering ─────────────────────── -->
    <template v-else>
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p v-if="nodeDef?.description" class="text-xs text-slate-500">{{ nodeDef.description }}</p>
        <template v-if="nodeDef?.config_schema">
          <div v-for="(schema, key) in configFields" :key="key" class="form-group">
            <label class="label">{{ schema.label ?? key }}</label>
            <textarea v-if="schema.type === 'string' && key === 'script'"
              v-model="localData[key]" rows="6"
              class="input text-xs font-mono resize-y" @change="emitUpdate" />
            <select v-else-if="schema.enum"
              v-model="localData[key]" class="input text-sm" @change="emitUpdate">
              <option v-for="opt in schema.enum" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <input v-else-if="schema.type === 'boolean'"
              type="checkbox" v-model="localData[key]"
              class="text-sm" @change="emitUpdate" />
            <input v-else
              v-model="localData[key]"
              :type="schema.type === 'number' ? 'number' : 'text'"
              class="input text-sm" @change="emitUpdate" />
          </div>

        </template>
      </div>
    </template>

    <!-- Footer -->
    <div class="p-3 border-t border-slate-200 dark:border-slate-700/60">
      <button @click="emitUpdate" class="btn-primary w-full btn-sm">Übernehmen</button>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { dpApi, searchApi } from '@/api/client'

const props = defineProps({
  node:      { type: Object, default: null },
  nodeTypes: { type: Array,  default: () => [] },
})
const emit = defineEmits(['update', 'close'])

// ── State ──────────────────────────────────────────────────────────────────
const localData      = ref({})
const dpSearch       = ref('')
const dpResults      = ref([])
const activeTab      = ref('connection')
const valueMapPreset = ref('')
const valueMapCustom = ref('')

// ── Value Map Presets ──────────────────────────────────────────────────────
const VALUE_MAP_PRESETS = [
  { key: '',            label: '— keine Wertzuordnung —',            map: null },
  { key: 'num_invert',  label: '0 ↔ 1 (numerisch invertieren)',       map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: 'true/false → on/off',                 map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: 'on/off → true/false',                 map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: '0/1 → off/on',                        map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: 'off/on → 0/1',                        map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: 'Benutzerdefiniert (JSON) …',           map: null },
]

// ── Formula Presets ────────────────────────────────────────────────────────
const MULTIPLY_PRESETS = [
  { f: 'x * 86400',  label: '× 86.400 (Tage → Sekunden)'    },
  { f: 'x * 3600',   label: '× 3.600 (Stunden → Sekunden)'  },
  { f: 'x * 1440',   label: '× 1.440 (Tage → Minuten)'      },
  { f: 'x * 1000',   label: '× 1.000'                        },
  { f: 'x * 100',    label: '× 100'                          },
  { f: 'x * 60',     label: '× 60 (Minuten → Sekunden)'      },
  { f: 'x * 10',     label: '× 10'                           },
]
const DIVIDE_PRESETS = [
  { f: 'round(x / 10, 1)',    label: '÷ 10 (Festkomma)'              },
  { f: 'x / 60',              label: '÷ 60 (Sekunden → Minuten)'     },
  { f: 'round(x / 100, 2)',   label: '÷ 100 (Festkomma)'             },
  { f: 'round(x / 1000, 3)',  label: '÷ 1.000 (Festkomma)'           },
  { f: 'x / 1440',            label: '÷ 1.440 (Minuten → Tage)'      },
  { f: 'x / 3600',            label: '÷ 3.600 (Sekunden → Stunden)'  },
  { f: 'x / 86400',           label: '÷ 86.400 (Sekunden → Tage)'    },
]
const ALL_PRESETS = [...MULTIPLY_PRESETS, ...DIVIDE_PRESETS]

// ── Cron Presets ───────────────────────────────────────────────────────────
const CRON_PRESETS_INTERVAL = [
  { expr: '* * * * *',      label: 'Jede Minute'     },
  { expr: '*/5 * * * *',    label: 'Alle 5 Minuten'  },
  { expr: '*/10 * * * *',   label: 'Alle 10 Minuten' },
  { expr: '*/15 * * * *',   label: 'Alle 15 Minuten' },
  { expr: '*/30 * * * *',   label: 'Alle 30 Minuten' },
]
const CRON_PRESETS_HOURLY = [
  { expr: '0 * * * *',     label: 'Jede Stunde'     },
  { expr: '0 */2 * * *',   label: 'Alle 2 Stunden'  },
  { expr: '0 */4 * * *',   label: 'Alle 4 Stunden'  },
  { expr: '0 */6 * * *',   label: 'Alle 6 Stunden'  },
  { expr: '0 */12 * * *',  label: 'Alle 12 Stunden' },
]
const CRON_PRESETS_DAILY = [
  { expr: '0 0 * * *',       label: 'Täglich um 00:00 (Mitternacht)' },
  { expr: '0 6 * * *',       label: 'Täglich um 06:00'               },
  { expr: '0 7 * * *',       label: 'Täglich um 07:00'               },
  { expr: '0 8 * * *',       label: 'Täglich um 08:00'               },
  { expr: '0 9 * * *',       label: 'Täglich um 09:00'               },
  { expr: '0 12 * * *',      label: 'Täglich um 12:00'               },
  { expr: '0 17 * * *',      label: 'Täglich um 17:00'               },
  { expr: '0 18 * * *',      label: 'Täglich um 18:00'               },
  { expr: '0 22 * * *',      label: 'Täglich um 22:00'               },
  { expr: '0 6,18 * * *',    label: 'Täglich um 06:00 und 18:00'     },
  { expr: '0 8,12,18 * * *', label: 'Täglich um 08:00, 12:00, 18:00' },
  { expr: '0 6 * * 1-5',     label: 'Werktags (Mo–Fr) um 06:00'      },
  { expr: '0 7 * * 1-5',     label: 'Werktags (Mo–Fr) um 07:00'      },
  { expr: '0 9 * * 1-5',     label: 'Werktags (Mo–Fr) um 09:00'      },
  { expr: '0 8-17 * * 1-5',  label: 'Werktags, stündlich 08–17 Uhr'  },
]
const CRON_PRESETS_OTHER = [
  { expr: '0 9 * * 1',    label: 'Jeden Montag um 09:00'           },
  { expr: '0 0 * * 0',    label: 'Jeden Sonntag um Mitternacht'    },
  { expr: '0 0 * * 1',    label: 'Jeden Montag um Mitternacht'     },
  { expr: '0 0 1 * *',    label: 'Ersten Tag des Monats (00:00)'   },
  { expr: '0 0 15 * *',   label: '15. des Monats (00:00)'          },
  { expr: '0 0 1 1 *',    label: 'Einmal jährlich (1. Januar)'     },
  { expr: '0 0 1 4 *',    label: 'Einmal jährlich (1. April)'      },
  { expr: '0 0 1 10 *',   label: 'Einmal jährlich (1. Oktober)'    },
]
const ALL_CRON_PRESETS = [
  ...CRON_PRESETS_INTERVAL,
  ...CRON_PRESETS_HOURLY,
  ...CRON_PRESETS_DAILY,
  ...CRON_PRESETS_OTHER,
]

// ── Cron field state ───────────────────────────────────────────────────────
const cronFields = ref([
  { key: 'min',     value: '0', label: 'Min', placeholder: '0', hint: '0–59'        },
  { key: 'hour',    value: '7', label: 'Std', placeholder: '*', hint: '0–23'        },
  { key: 'day',     value: '*', label: 'Tag', placeholder: '*', hint: '1–31'        },
  { key: 'month',   value: '*', label: 'Mon', placeholder: '*', hint: '1–12'        },
  { key: 'weekday', value: '*', label: 'WT',  placeholder: '*', hint: '0–6 (0=So)'  },
])

function parseCronToFields(expr) {
  const parts = (expr || '0 7 * * *').trim().split(/\s+/)
  if (parts.length === 5) {
    cronFields.value[0].value = parts[0]
    cronFields.value[1].value = parts[1]
    cronFields.value[2].value = parts[2]
    cronFields.value[3].value = parts[3]
    cronFields.value[4].value = parts[4]
  }
}

function cronFieldsToExpr() {
  return cronFields.value.map(f => f.value || '*').join(' ')
}

function onCronFieldChange() {
  localData.value.cron = cronFieldsToExpr()
  emitUpdate()
}

function onCronExprChange() {
  parseCronToFields(localData.value.cron)
  emitUpdate()
}

const cronPresetValue = computed(() => {
  const expr = (localData.value.cron || '').trim()
  return ALL_CRON_PRESETS.find(p => p.expr === expr)?.expr ?? ''
})

function onCronPresetChange(e) {
  const expr = e.target.value
  if (expr) {
    localData.value.cron = expr
    parseCronToFields(expr)
    emitUpdate()
  }
}

const cronDescription = computed(() => {
  const expr = (localData.value.cron || '').trim()
  return ALL_CRON_PRESETS.find(p => p.expr === expr)?.label ?? ''
})

// ── Computed ───────────────────────────────────────────────────────────────
const nodeDef = computed(() => props.nodeTypes.find(nt => nt.type === props.node?.type))

const isDatapointNode = computed(() =>
  props.node?.type === 'datapoint_read' || props.node?.type === 'datapoint_write'
)
const isWrite          = computed(() => props.node?.type === 'datapoint_write')
const isCronNode       = computed(() => props.node?.type === 'timer_cron')
const isMathFormulaNode = computed(() => props.node?.type === 'math_formula')


const configFields = computed(() => {
  const schema = nodeDef.value?.config_schema ?? {}
  return Object.fromEntries(
    Object.entries(schema).filter(([k]) => !k.startsWith('datapoint_'))
  )
})

const formulaPreset = computed({
  get() {
    const f = localData.value.value_formula || ''
    if (!f) return ''
    return ALL_PRESETS.find(p => p.f === f)?.f ?? '__custom__'
  },
  set(v) { void v },
})

const outputFormulaPreset = computed(() => {
  const f = localData.value.output_formula || ''
  if (!f) return ''
  return ALL_PRESETS.find(p => p.f === f)?.f ?? '__custom__'
})

const hasTransform = computed(() =>
  !!(localData.value.value_formula || '').trim() || !!valueMapPreset.value
)
const hasFilter    = computed(() => {
  const d = localData.value
  return boolVal('trigger_on_change') || boolVal('only_on_change') ||
         !!(d.min_delta || d.min_delta_pct || d.throttle_value)
})

const tabs = computed(() => [
  { id: 'connection', label: 'Verbindung',     dot: false              },
  { id: 'transform',  label: 'Transformation', dot: hasTransform.value },
  { id: 'filter',     label: 'Filter',         dot: hasFilter.value    },
])

// ── Helpers ────────────────────────────────────────────────────────────────
function boolVal(key) {
  const v = localData.value[key]
  return v === true || v === 'true'
}
function setBool(key, val) {
  localData.value[key] = val
}

// ── Watchers ───────────────────────────────────────────────────────────────
watch(() => props.node, (n) => {
  if (n) {
    localData.value = { ...n.data }
    dpSearch.value  = n.data.datapoint_name || ''
    dpResults.value = []
    activeTab.value = 'connection'
    if (n.type === 'timer_cron') {
      parseCronToFields(n.data.cron || '0 7 * * *')
    }
    if (n.type === 'datapoint_read' || n.type === 'datapoint_write') {
      searchDps()
      // Restore value_map UI state
      const vm = n.data.value_map
      if (vm && typeof vm === 'object') {
        const mapStr = JSON.stringify(vm)
        const preset = VALUE_MAP_PRESETS.find(p => p.map && JSON.stringify(p.map) === mapStr)
        valueMapPreset.value = preset?.key ?? 'custom'
        valueMapCustom.value = preset ? '' : JSON.stringify(vm, null, 2)
      } else {
        valueMapPreset.value = ''
        valueMapCustom.value = ''
      }
    }
  }
}, { immediate: true })

// ── Preset / formula handlers ──────────────────────────────────────────────
function onPresetChange(e) {
  const val = e.target.value
  if (val && val !== '__custom__') {
    localData.value.value_formula = val
    emitUpdate()
  }
}
function onFormulaInput() { /* formulaPreset computed switches to __custom__ */ }

function onOutputPresetChange(e) {
  const val = e.target.value
  if (val && val !== '__custom__') {
    localData.value.output_formula = val
    emitUpdate()
  }
}

function onValueMapPresetChange() {
  if (valueMapPreset.value !== 'custom') valueMapCustom.value = ''
  const preset = VALUE_MAP_PRESETS.find(p => p.key === valueMapPreset.value)
  localData.value.value_map = preset?.map ?? null
  emitUpdate()
}

function onValueMapCustomChange() {
  try {
    localData.value.value_map = JSON.parse(valueMapCustom.value)
  } catch { localData.value.value_map = null }
  emitUpdate()
}

// ── DataPoint picker ───────────────────────────────────────────────────────
async function searchDps() {
  try {
    if (dpSearch.value.length < 1) {
      const { data } = await dpApi.list(0, 50)
      dpResults.value = data.items || data
    } else {
      const { data } = await searchApi.search({ q: dpSearch.value, size: 50 })
      dpResults.value = data.items || data
    }
  } catch { dpResults.value = [] }
}

function selectDp(dp) {
  localData.value.datapoint_id   = dp.id
  localData.value.datapoint_name = dp.name
  dpSearch.value  = dp.name
  dpResults.value = []
  emitUpdate()
}

// ── Emit ───────────────────────────────────────────────────────────────────
function emitUpdate() {
  emit('update', { ...localData.value })
}
</script>

<style scoped>
.tab-btn {
  flex: 1;
  padding: 8px 4px 6px;
  font-size: 11px;
  font-weight: 500;
  color: #475569;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color .15s, border-color .15s;
  white-space: nowrap;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
}
.tab-btn:hover   { color: #334155; }
.tab-btn--active { color: #0f172a; border-bottom-color: #0d9488; }
.tab-dot { color: #0d9488; font-size: 14px; line-height: 1; }

:global(.dark) .tab-btn          { color: #64748b; }
:global(.dark) .tab-btn:hover    { color: #94a3b8; }
:global(.dark) .tab-btn--active  { color: #e2e8f0; border-bottom-color: #14b8a6; }
:global(.dark) .tab-dot          { color: #14b8a6; }

.section-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: #0d9488;
  margin-bottom: 4px;
}
:global(.dark) .section-label { color: #14b8a6; }

.form-group { display: flex; flex-direction: column; gap: 4px; }
.label      { font-size: 11px; font-weight: 500; color: #475569; }
:global(.dark) .label { color: #94a3b8; }

/* ── Cron builder ─────────────────────────────────────────────────────── */
.cron-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 5px;
}
.cron-field {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.cron-field .input {
  width: 100%;
  min-width: 0;
  padding-left: 2px;
  padding-right: 2px;
}
.cron-field-label {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: #475569;
}
:global(.dark) .cron-field-label { color: #64748b; }

.cron-field-hint {
  font-size: 8px;
  color: #64748b;
  white-space: nowrap;
}
:global(.dark) .cron-field-hint { color: #475569; }

.cron-legend {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 9px;
  color: #64748b;
}
:global(.dark) .cron-legend { color: #475569; }

.cron-legend code {
  color: #475569;
  font-size: 9px;
}
:global(.dark) .cron-legend code { color: #94a3b8; }
</style>
