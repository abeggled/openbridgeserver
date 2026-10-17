<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">
    <div class="form-group">
      <label class="label">{{ $t('datapoints.form.name') }}</label>
      <input v-model="form.name" type="text" class="input" :placeholder="$t('datapoints.form.namePlaceholder')" required autofocus data-testid="input-name" />
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">{{ $t('datapoints.form.datatype') }}</label>
        <select v-model="form.data_type" class="input" required data-testid="select-datatype">
          <option v-for="dt in datatypes" :key="dt.name" :value="dt.name">{{ dt.name }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('datapoints.form.unit') }}</label>
        <select v-model="unitSelect" class="input" data-testid="select-unit">
          <option value="">{{ $t('datapoints.form.noUnit') }}</option>
          <optgroup v-for="cat in unitCategories" :key="cat.key" :label="cat.label">
            <option v-for="u in cat.units" :key="u" :value="u">{{ u }}</option>
          </optgroup>
          <option value="__other__">{{ $t('datapoints.form.otherUnit') }}</option>
        </select>
        <input
          v-if="unitSelect === '__other__'"
          v-model="unitCustom"
          type="text"
          class="input mt-1"
          :placeholder="$t('datapoints.form.unitPlaceholder')"
          autofocus
          data-testid="input-unit-custom"
        />
      </div>
    </div>

    <div class="form-group">
      <label class="label">{{ $t('datapoints.form.tags') }} <span class="text-slate-500 font-normal">({{ $t('datapoints.form.tagsHint') }})</span></label>
      <input v-model="tagsInput" type="text" class="input" :placeholder="$t('datapoints.form.tagsPlaceholder')" />
    </div>

    <div class="form-group">
      <label class="label">{{ $t('datapoints.form.mqttAlias') }} <span class="text-slate-500 font-normal">({{ $t('datapoints.form.optional') }})</span></label>
      <input v-model="form.mqtt_alias" type="text" class="input" placeholder="alias/eg/wohnzimmer/temperatur/value" />
    </div>

    <label class="flex items-center gap-2 cursor-pointer select-none">
      <input v-model="form.persist_value" type="checkbox" class="w-4 h-4 rounded accent-blue-500" />
      <span class="text-sm text-slate-700 dark:text-slate-200">{{ $t('datapoints.form.persistValue') }} <span class="text-slate-500 font-normal">({{ $t('datapoints.form.persistValueHint') }})</span></span>
    </label>

    <label class="flex items-center gap-2 cursor-pointer select-none" data-testid="label-record-history">
      <input v-model="form.record_history" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="checkbox-record-history" />
      <span class="text-sm text-slate-700 dark:text-slate-200">{{ $t('datapoints.form.recordHistory') }} <span class="text-slate-500 font-normal">({{ $t('datapoints.form.recordHistoryHint') }})</span></span>
    </label>

    <label class="flex items-center gap-2 cursor-pointer select-none" data-testid="label-external-write-enabled">
      <input v-model="form.external_write_enabled" type="checkbox" class="w-4 h-4 rounded accent-blue-500" data-testid="checkbox-external-write-enabled" />
      <span class="text-sm text-slate-700 dark:text-slate-200">{{ $t('datapoints.form.externalWriteEnabled') }} <span class="text-slate-500 font-normal">({{ $t('datapoints.form.externalWriteEnabledHint') }})</span></span>
    </label>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
      {{ error }}
    </div>

    <div class="flex justify-end gap-3 pt-2">
      <button type="button" @click="$emit('cancel')" class="btn-secondary" data-testid="btn-cancel">{{ $t('common.cancel') }}</button>
      <button type="submit" class="btn-primary" :disabled="saving" data-testid="btn-save">
        <Spinner v-if="saving" size="sm" color="white" />
        {{ saving ? $t('common.saving') : $t('common.save') }}
      </button>
    </div>
  </form>
</template>

<script setup>
import { reactive, ref, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Spinner from '@/components/ui/Spinner.vue'

// ---------------------------------------------------------------------------
// ISO-Einheiten — kategorisiert
// ---------------------------------------------------------------------------
// labelKey resolves via datapoints.form.unitCategories.<key> so the <optgroup>
// labels stay reactive to the active locale.
const UNIT_CATEGORY_DEFS = [
  {
    labelKey: 'temperature',
    units: ['°C', '°F', 'K'],
  },
  {
    labelKey: 'humidityAirQuality',
    units: ['%', '%rH', 'ppm', 'ppb', 'g/m³', 'μg/m³', 'mg/m³'],
  },
  {
    labelKey: 'pressure',
    units: ['Pa', 'hPa', 'kPa', 'bar', 'mbar'],
  },
  {
    labelKey: 'electricity',
    units: ['V', 'mV', 'A', 'mA', 'W', 'kW', 'MW', 'VA', 'kVA', 'var', 'kvar', 'Wh', 'kWh', 'MWh', 'Ω', 'Hz', 'cos φ'],
  },
  {
    labelKey: 'light',
    units: ['lx', 'lm', 'cd', 'W/m²'],
  },
  {
    labelKey: 'speedAcceleration',
    units: ['m/s', 'km/h', 'm/s²'],
  },
  {
    labelKey: 'angle',
    units: ['°'],
  },
  {
    labelKey: 'volumeFlow',
    units: ['m³', 'l', 'dl', 'cl', 'm³/h', 'l/h', 'l/min', 'mm/h'],
  },
  {
    labelKey: 'lengthArea',
    units: ['mm', 'cm', 'm', 'km', 'm²', 'km²'],
  },
  {
    labelKey: 'mass',
    units: ['g', 'kg', 't'],
  },
  {
    labelKey: 'time',
    units: ['ms', 's', 'min', 'h', 'd'],
  },
  {
    labelKey: 'radiation',
    units: ['nSv/h', 'mSv/h'],
  },
  {
    labelKey: 'other',
    units: ['1', 'dB', 'dBA', 'pH', 'Bq/m³'],
  },
]

// Flat set for fast lookup — unit symbols are not translated, so this can
// stay a plain derived value independent of locale.
const KNOWN_UNITS = new Set(UNIT_CATEGORY_DEFS.flatMap(c => c.units))

// ---------------------------------------------------------------------------
// Props / emits
// ---------------------------------------------------------------------------
const props = defineProps({
  initial:      { type: Object,   default: null },
  datatypes:    { type: Array,    default: () => [] },
  saveHandler:  { type: Function, required: true },
})
const emit = defineEmits(['cancel'])

const { t } = useI18n()
const saving    = ref(false)
const error     = ref(null)
const tagsInput = ref('')

const unitCategories = computed(() =>
  UNIT_CATEGORY_DEFS.map(cat => ({
    key: cat.labelKey,
    label: t(`datapoints.form.unitCategories.${cat.labelKey}`),
    units: cat.units,
  }))
)

// Dropdown selection & free-text fallback
const unitSelect = ref('')
const unitCustom = ref('')

// Effective unit value used when submitting
const effectiveUnit = computed(() => {
  if (unitSelect.value === '__other__') return unitCustom.value.trim() || null
  return unitSelect.value || null
})

const form = reactive({
  name:           '',
  data_type:      'FLOAT',
  mqtt_alias:     '',
  persist_value:  true,
  record_history: true,
  external_write_enabled: false,
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
    form.name           = val.name
    form.data_type      = val.data_type
    form.mqtt_alias     = val.mqtt_alias ?? ''
    form.persist_value  = val.persist_value ?? true
    form.record_history = val.record_history ?? true
    form.external_write_enabled = val.external_write_enabled ?? false
    tagsInput.value     = val.tags?.join(', ') ?? ''
    applyUnit(val.unit)
  } else {
    form.name = ''; form.data_type = 'FLOAT'; form.mqtt_alias = ''
    form.persist_value = true; form.record_history = true
    form.external_write_enabled = false
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
      name:           form.name,
      data_type:      form.data_type,
      unit:           effectiveUnit.value,
      tags,
      mqtt_alias:     form.mqtt_alias || null,
      persist_value:  form.persist_value,
      record_history: form.record_history,
      external_write_enabled: form.external_write_enabled,
    })
  } catch (e) {
    error.value = e.response?.data?.detail ?? e.message ?? t('datapoints.form.saveError')
  } finally {
    saving.value = false
  }
}
</script>
