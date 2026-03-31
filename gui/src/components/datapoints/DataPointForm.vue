<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">
    <div class="form-group">
      <label class="label">Name *</label>
      <input v-model="form.name" type="text" class="input" placeholder="z.B. Wohnzimmer Temperatur" required autofocus />
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">Datentyp *</label>
        <select v-model="form.data_type" class="input" required>
          <option v-for="dt in datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">Einheit</label>
        <select v-model="unitSelect" class="input">
          <option value="">— keine —</option>
          <optgroup v-for="cat in UNIT_CATEGORIES" :key="cat.label" :label="cat.label">
            <option v-for="u in cat.units" :key="u" :value="u">{{ u }}</option>
          </optgroup>
          <option value="__other__">Andere (Freitext) …</option>
        </select>
        <input
          v-if="unitSelect === '__other__'"
          v-model="unitCustom"
          type="text"
          class="input mt-1"
          placeholder="Einheit eingeben …"
          autofocus
        />
      </div>
    </div>

    <div class="form-group">
      <label class="label">Tags <span class="text-slate-500 font-normal">(kommagetrennt)</span></label>
      <input v-model="tagsInput" type="text" class="input" placeholder="heizung, eg, wohnzimmer" />
    </div>

    <div class="form-group">
      <label class="label">MQTT Alias <span class="text-slate-500 font-normal">(optional)</span></label>
      <input v-model="form.mqtt_alias" type="text" class="input" placeholder="alias/eg/wohnzimmer/temperatur/value" />
    </div>

    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input v-model="form.persist_value" type="checkbox" class="w-4 h-4 rounded accent-blue-500" />
      <span class="text-sm text-slate-700 dark:text-slate-200">Letzten Wert speichern <span class="text-slate-500 font-normal">(nach Neustart sofort verfügbar)</span></span>
    </label>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
      {{ error }}
    </div>

    <div class="flex justify-end gap-3 pt-2">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">Abbrechen</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        {{ saving ? 'Speichern …' : 'Speichern' }}
      </button>
    </div>
  </form>
</template>

<script setup>
import { reactive, ref, watch, computed } from 'vue'
import Spinner from '@/components/ui/Spinner.vue'

// ---------------------------------------------------------------------------
// ISO-Einheiten — kategorisiert
// ---------------------------------------------------------------------------
const UNIT_CATEGORIES = [
  {
    label: 'Temperatur',
    units: ['°C', '°F', 'K'],
  },
  {
    label: 'Feuchte & Luftqualität',
    units: ['%', '%rH', 'ppm', 'ppb', 'g/m³', 'μg/m³', 'mg/m³'],
  },
  {
    label: 'Druck',
    units: ['Pa', 'hPa', 'kPa', 'bar', 'mbar'],
  },
  {
    label: 'Elektrizität',
    units: ['V', 'mV', 'A', 'mA', 'W', 'kW', 'MW', 'VA', 'kVA', 'var', 'kvar', 'Wh', 'kWh', 'MWh', 'Ω', 'Hz', 'cos φ'],
  },
  {
    label: 'Licht',
    units: ['lx', 'lm', 'cd', 'W/m²'],
  },
  {
    label: 'Geschwindigkeit & Beschleunigung',
    units: ['m/s', 'km/h', 'm/s²'],
  },
  {
    label: 'Volumen & Durchfluss',
    units: ['m³', 'l', 'dl', 'cl', 'm³/h', 'l/h', 'l/min'],
  },
  {
    label: 'Länge & Fläche',
    units: ['mm', 'cm', 'm', 'km', 'm²', 'km²'],
  },
  {
    label: 'Masse',
    units: ['g', 'kg', 't'],
  },
  {
    label: 'Zeit',
    units: ['ms', 's', 'min', 'h', 'd'],
  },
  {
    label: 'Sonstiges',
    units: ['1', 'dB', 'dBA', 'pH', 'Bq/m³'],
  },
]

// Flat set for fast lookup
const KNOWN_UNITS = new Set(UNIT_CATEGORIES.flatMap(c => c.units))

// ---------------------------------------------------------------------------
// Props / emits
// ---------------------------------------------------------------------------
const props = defineProps({
  initial:      { type: Object,   default: null },
  datatypes:    { type: Array,    default: () => [] },
  saveHandler:  { type: Function, required: true },
})
const emit = defineEmits(['cancel'])

const saving    = ref(false)
const error     = ref(null)
const tagsInput = ref('')

// Dropdown selection & free-text fallback
const unitSelect = ref('')
const unitCustom = ref('')

// Effective unit value used when submitting
const effectiveUnit = computed(() => {
  if (unitSelect.value === '__other__') return unitCustom.value.trim() || null
  return unitSelect.value || null
})

const form = reactive({
  name:          '',
  data_type:     'FLOAT',
  mqtt_alias:    '',
  persist_value: true,
})

// Helper: initialise unit controls from a raw string
function applyUnit(raw) {
  const val = raw ?? ''
  if (!val) {
    unitSelect.value = ''
    unitCustom.value = ''
  } else if (KNOWN_UNITS.has(val)) {
    unitSelect.value = val
    unitCustom.value = ''
  } else {
    unitSelect.value = '__other__'
    unitCustom.value = val
  }
}

watch(() => props.initial, (val) => {
  if (val) {
    form.name          = val.name
    form.data_type     = val.data_type
    form.mqtt_alias    = val.mqtt_alias ?? ''
    form.persist_value = val.persist_value ?? true
    tagsInput.value    = val.tags?.join(', ') ?? ''
    applyUnit(val.unit)
  } else {
    form.name = ''; form.data_type = 'FLOAT'; form.mqtt_alias = ''
    form.persist_value = true
    tagsInput.value = ''
    applyUnit('')
  }
}, { immediate: true })

async function submit() {
  error.value  = null
  saving.value = true
  try {
    const tags = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean)
    await props.saveHandler({
      name:          form.name,
      data_type:     form.data_type,
      unit:          effectiveUnit.value,
      tags,
      mqtt_alias:    form.mqtt_alias || null,
      persist_value: form.persist_value,
    })
  } catch (e) {
    error.value = e.response?.data?.detail ?? e.message ?? 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}
</script>
