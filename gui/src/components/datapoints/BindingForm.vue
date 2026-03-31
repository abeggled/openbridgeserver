<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">

    <!-- Tab-Leiste -->
    <div class="flex gap-0 border-b border-slate-200 dark:border-slate-700 -mt-1">
      <button
        v-for="tab in visibleTabs" :key="tab.id"
        type="button"
        @click="activeTab = tab.id"
        class="tab-btn"
        :class="{ 'tab-active': activeTab === tab.id }"
      >
        {{ tab.label }}
        <span v-if="tab.badge" class="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-blue-400"></span>
      </button>
    </div>

    <!-- ── TAB: Verbindung ── -->
    <div v-show="activeTab === 'conn'" class="flex flex-col gap-4">

      <div class="grid grid-cols-2 gap-4">
        <div class="form-group">
          <label class="label">Adapter-Instanz *</label>
          <div v-if="props.initial" class="input bg-slate-100 dark:bg-slate-800/50 text-slate-400 cursor-not-allowed">
            {{ currentInstanceName }}
          </div>
          <select v-else v-model="form.adapter_instance_id" class="input" required>
            <option value="">Instanz wählen …</option>
            <optgroup v-for="group in groupedInstances" :key="group.type" :label="group.type">
              <option v-for="inst in group.items" :key="inst.id" :value="inst.id">{{ inst.name }}</option>
            </optgroup>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Direction *</label>
          <select v-model="form.direction" class="input">
            <option value="SOURCE">SOURCE — Adapter → System</option>
            <option value="DEST">DEST — System → Adapter</option>
            <option value="BOTH">BOTH — beidseitig</option>
          </select>
        </div>
      </div>

      <!-- KNX -->
      <template v-if="selectedAdapterType === 'KNX'">
        <div class="section-header">KNX Binding</div>
        <div class="form-group">
          <label class="label">Gruppenadresse *</label>
          <GaCombobox v-model="cfg.group_address" placeholder="z.B. 1/2/3 oder Name suchen …" @select="onGaSelect" />
        </div>
        <div class="form-group">
          <label class="label">DPT *</label>
          <select v-model="cfg.dpt_id" class="input" required>
            <option value="">DPT wählen …</option>
            <optgroup v-for="group in groupedDpts" :key="group.family" :label="group.label">
              <option v-for="dpt in group.dpts" :key="dpt.dpt_id" :value="dpt.dpt_id">
                {{ dpt.dpt_id }} — {{ dpt.name }}<template v-if="dpt.unit"> [{{ dpt.unit }}]</template>
              </option>
            </optgroup>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Status-Gruppenadresse <span class="optional">(optional)</span></label>
          <GaCombobox v-model="cfg.state_group_address" placeholder="z.B. 1/2/4 oder Name suchen …" />
          <p class="hint">Rückmelde-GA für den Ist-Wert (DEST / BOTH)</p>
        </div>
        <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="flex items-start gap-2">
          <input
            type="checkbox"
            id="respond_to_read"
            v-model="cfg.respond_to_read"
            :disabled="!props.dpPersistValue"
            class="w-4 h-4 rounded mt-0.5"
          />
          <div>
            <label
              for="respond_to_read"
              class="text-sm"
              :class="props.dpPersistValue ? 'text-slate-600 dark:text-slate-300' : 'text-slate-400 dark:text-slate-500 cursor-not-allowed'"
            >Antworte auf Leseanfragen</label>
            <p class="hint">
              Sendet den aktuellen Wert als GroupValueResponse wenn eine Leseanfrage eingeht.
              <template v-if="!props.dpPersistValue"> Erfordert aktiviertes „Letzten Wert speichern" am DataPoint.</template>
            </p>
          </div>
        </div>
      </template>

      <!-- Modbus -->
      <template v-if="selectedAdapterType === 'MODBUS_TCP' || selectedAdapterType === 'MODBUS_RTU'">
        <div class="section-header">Modbus Binding</div>
        <div class="grid grid-cols-3 gap-4">
          <div class="form-group">
            <label class="label">Adresse *</label>
            <input v-model.number="cfg.address" type="number" min="0" max="65535" class="input" required />
          </div>
          <div class="form-group">
            <label class="label">Registertyp *</label>
            <select v-model="cfg.register_type" class="input">
              <option value="holding">Holding Register</option>
              <option value="input">Input Register</option>
              <option value="coil">Coil</option>
              <option value="discrete_input">Discrete Input</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Datenformat *</label>
            <select v-model="cfg.data_format" class="input">
              <optgroup label="16-Bit">
                <option value="uint16">UINT16</option>
                <option value="int16">INT16</option>
              </optgroup>
              <optgroup label="32-Bit">
                <option value="uint32">UINT32</option>
                <option value="int32">INT32</option>
                <option value="float32">FLOAT32</option>
              </optgroup>
              <optgroup label="64-Bit">
                <option value="uint64">UINT64</option>
                <option value="int64">INT64</option>
              </optgroup>
            </select>
          </div>
        </div>
        <div class="optional-divider">Optionale Einstellungen</div>
        <div class="grid grid-cols-4 gap-4">
          <div class="form-group">
            <label class="label">Unit ID</label>
            <input v-model.number="cfg.unit_id" type="number" min="0" max="255" class="input" />
            <p class="hint">Standard: 1</p>
          </div>
          <div class="form-group">
            <label class="label">Anz. Register</label>
            <input v-model.number="cfg.count" type="number" min="1" max="125" class="input" />
            <p class="hint">Standard: 1</p>
          </div>
          <div class="form-group">
            <label class="label">Skalierung</label>
            <input v-model.number="cfg.scale_factor" type="number" step="any" class="input" />
            <p class="hint">Standard: 1.0</p>
          </div>
          <div class="form-group">
            <label class="label">Intervall (s)</label>
            <input v-model.number="cfg.poll_interval" type="number" step="0.1" min="0.1" class="input" />
            <p class="hint">Standard: 1.0</p>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">Byte-Reihenfolge</label>
            <select v-model="cfg.byte_order" class="input">
              <option value="big">Big Endian</option>
              <option value="little">Little Endian</option>
            </select>
          </div>
          <div class="form-group">
            <label class="label">Word-Reihenfolge</label>
            <select v-model="cfg.word_order" class="input">
              <option value="big">Big Endian</option>
              <option value="little">Little Endian</option>
            </select>
          </div>
        </div>
      </template>

      <!-- MQTT -->
      <template v-if="selectedAdapterType === 'MQTT'">
        <div class="section-header">MQTT Binding</div>
        <div class="form-group">
          <label class="label">Topic *</label>
          <input v-model="cfg.topic" class="input" placeholder="z.B. haus/wohnzimmer/temperatur" required />
          <p class="hint">SOURCE/BOTH: abonniertes Topic; DEST: Publish-Topic</p>
        </div>
        <div class="optional-divider">Optionale Einstellungen</div>
        <div class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">Publish-Topic <span class="optional">(optional)</span></label>
            <input v-model="cfg.publish_topic" class="input" placeholder="z.B. …/set" />
            <p class="hint">Separates Topic für DEST/BOTH</p>
          </div>
          <div class="form-group flex flex-col justify-end">
            <div class="flex items-center gap-2 mt-6">
              <input type="checkbox" id="mqtt_retain" v-model="cfg.retain" class="w-4 h-4 rounded" />
              <label for="mqtt_retain" class="text-sm text-slate-600 dark:text-slate-300">Retain</label>
            </div>
            <p class="hint">Broker speichert letzten Wert</p>
          </div>
        </div>
      </template>

      <!-- 1-Wire -->
      <template v-if="selectedAdapterType === 'ONEWIRE'">
        <div class="section-header">1-Wire Binding</div>
        <div class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">Sensor-ID *</label>
            <input v-model="cfg.sensor_id" class="input" placeholder="z.B. 28-000000000001" required />
          </div>
          <div class="form-group">
            <label class="label">Sensor-Typ</label>
            <input v-model="cfg.sensor_type" class="input" placeholder="DS18B20" />
            <p class="hint">Standard: DS18B20</p>
          </div>
        </div>
      </template>

      <div v-if="!selectedAdapterType && !props.initial" class="p-3 bg-slate-100/80 dark:bg-slate-800/40 rounded-lg text-sm text-slate-500 text-center">
        Bitte zuerst eine Adapter-Instanz wählen
      </div>

    </div><!-- /TAB Verbindung -->

    <!-- ── TAB: Transformation ── -->
    <div v-show="activeTab === 'transform'" class="flex flex-col gap-4">
      <div class="section-header">Wert-Transformation</div>
      <div class="form-group">
        <label class="label">
          Formel
          <span class="text-slate-500 font-normal ml-1">— Variable: <code class="text-blue-400">x</code></span>
        </label>
        <div class="flex gap-2">
          <select class="input w-52 shrink-0" v-model="form.formula_preset" @change="onPresetSelect">
            <option value="">— Preset wählen —</option>
            <optgroup label="Multiplizieren">
              <option value="x * 86400">× 86.400 (Tage → Sekunden)</option>
              <option value="x * 3600">× 3.600 (Stunden → Sekunden)</option>
              <option value="x * 1440">× 1.440 (Tage → Minuten)</option>
              <option value="x * 1000">× 1.000</option>
              <option value="x * 100">× 100</option>
              <option value="x * 60">× 60 (Minuten → Sekunden)</option>
              <option value="x * 10">× 10</option>
            </optgroup>
            <optgroup label="Dividieren">
              <option value="x / 10">÷ 10 (Festkomma)</option>
              <option value="x / 60">÷ 60 (Sekunden → Minuten)</option>
              <option value="x / 100">÷ 100 (Festkomma)</option>
              <option value="x / 1000">÷ 1.000 (Festkomma)</option>
              <option value="x / 1440">÷ 1.440 (Minuten → Tage)</option>
              <option value="x / 3600">÷ 3.600 (Sekunden → Stunden)</option>
              <option value="x / 86400">÷ 86.400 (Sekunden → Tage)</option>
            </optgroup>
            <optgroup label="Benutzerdefiniert">
              <option value="__custom__">Eigene Formel …</option>
            </optgroup>
          </select>
          <input
            v-model="form.value_formula"
            type="text"
            placeholder="z.B. x * 0.1 + 20"
            class="input flex-1 font-mono text-sm"
            @input="form.formula_preset = '__custom__'"
          />
        </div>
        <p class="hint mt-1">
          Verfügbar: <code class="text-blue-400">abs round min max sqrt floor ceil</code>
          und alle <code class="text-blue-400">math.*</code>-Funktionen. Leer = keine Transformation.
        </p>
      </div>
    </div><!-- /TAB Transformation -->

    <!-- ── TAB: Filter ── -->
    <div v-show="activeTab === 'filter'" class="flex flex-col gap-4">
      <div class="section-header">Zeitlicher Filter</div>
      <div class="form-group">
        <label class="label">Min. Zeitabstand zwischen zwei Sends</label>
        <div class="flex gap-2">
          <input v-model.number="form.throttle_value" type="number" min="0" step="1" placeholder="0 = kein Filter" class="input flex-1" />
          <select v-model="form.throttle_unit" class="input w-24">
            <option value="ms">ms</option>
            <option value="s">s</option>
            <option value="min">min</option>
            <option value="h">h</option>
          </select>
        </div>
        <p class="hint">Sends innerhalb des Intervalls werden verworfen.</p>
      </div>

      <div class="section-header">Wert-Filter</div>
      <div class="flex items-center gap-2">
        <input type="checkbox" id="send_on_change" v-model="form.send_on_change" class="w-4 h-4 rounded" />
        <label for="send_on_change" class="text-sm text-slate-600 dark:text-slate-300">Nur senden wenn Wert sich geändert hat (kein Duplikat)</label>
      </div>
      <div class="form-group">
        <label class="label">Nur senden bei Mindest-Abweichung</label>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-slate-400 mb-1 block">Absolut</label>
            <input v-model.number="form.send_min_delta" type="number" min="0" step="any" placeholder="z.B. 0.5" class="input" />
          </div>
          <div>
            <label class="text-xs text-slate-400 mb-1 block">Relativ (%)</label>
            <input v-model.number="form.send_min_delta_pct" type="number" min="0" step="any" placeholder="z.B. 2" class="input" />
          </div>
        </div>
        <p class="hint">Leer = inaktiv. Beide aktiv = beide müssen erfüllt sein. Nur für numerische Werte.</p>
      </div>
    </div><!-- /TAB Filter -->

    <!-- Aktiviert -->
    <div class="flex items-center gap-2 border-t border-slate-200 dark:border-slate-700/60 pt-3">
      <input type="checkbox" id="enabled" v-model="form.enabled" class="w-4 h-4 rounded" />
      <label for="enabled" class="text-sm text-slate-600 dark:text-slate-300">Aktiviert</label>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">Abbrechen</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        Speichern
      </button>
    </div>

  </form>
</template>

<script setup>
import { ref, reactive, watch, computed, onMounted } from 'vue'
import { dpApi, adapterApi } from '@/api/client'
import Spinner    from '@/components/ui/Spinner.vue'
import GaCombobox from '@/components/ui/GaCombobox.vue'

const props = defineProps({
  dpId:           { type: String,  required: true },
  initial:        { type: Object,  default: null },
  dpPersistValue: { type: Boolean, default: false },
})
const emit = defineEmits(['save', 'cancel'])

const saving       = ref(false)
const error        = ref(null)
const allInstances = ref([])
const allDpts      = ref([])
const activeTab    = ref('conn')

// ---------------------------------------------------------------------------
// Form-State
// ---------------------------------------------------------------------------

const THROTTLE_FACTORS = { ms: 1, s: 1000, min: 60_000, h: 3_600_000 }

const form = reactive({
  adapter_instance_id: '',
  direction:           'SOURCE',
  enabled:             true,
  value_formula:       '',
  formula_preset:      '',
  throttle_value:      0,
  throttle_unit:       's',
  send_on_change:      false,
  send_min_delta:      null,
  send_min_delta_pct:  null,
})

const cfg = reactive({
  group_address: '', dpt_id: 'DPT9.001', state_group_address: '', respond_to_read: false,
  address: 0, register_type: 'holding', data_format: 'uint16',
  unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0,
  byte_order: 'big', word_order: 'big',
  topic: '', publish_topic: '', retain: false,
  sensor_id: '', sensor_type: 'DS18B20',
})

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const selectedAdapterType = computed(() => {
  if (props.initial) return props.initial.adapter_type ?? null
  const inst = allInstances.value.find(i => i.id === form.adapter_instance_id)
  return inst?.adapter_type ?? null
})

const currentInstanceName = computed(() => {
  if (!props.initial) return ''
  if (props.initial.instance_name) return `${props.initial.instance_name} (${props.initial.adapter_type})`
  return props.initial.adapter_type
})

const visibleTabs = computed(() => {
  const tabs = [{ id: 'conn', label: 'Verbindung', badge: false }]
  if (selectedAdapterType.value) {
    const hasFormula = !!form.value_formula?.trim()
    tabs.push({ id: 'transform', label: 'Transformation', badge: hasFormula })
    if (form.direction === 'DEST' || form.direction === 'BOTH') {
      const hasFilter = form.throttle_value > 0 || form.send_on_change
        || (form.send_min_delta ?? 0) > 0 || (form.send_min_delta_pct ?? 0) > 0
      tabs.push({ id: 'filter', label: 'Filter', badge: hasFilter })
    }
  }
  return tabs
})

watch(visibleTabs, tabs => {
  if (!tabs.find(t => t.id === activeTab.value)) activeTab.value = 'conn'
})

const groupedDpts = computed(() => {
  const familyLabels = {
    DPT1: 'DPT 1.x — 1-Bit (Boolean)', DPT5: 'DPT 5.x — 8-Bit unsigned',
    DPT6: 'DPT 6.x — 8-Bit signed',    DPT7: 'DPT 7.x — 16-Bit unsigned',
    DPT8: 'DPT 8.x — 16-Bit signed',   DPT9: 'DPT 9.x — 16-Bit Float',
    DPT10: 'DPT 10.x — Time of Day',   DPT11: 'DPT 11.x — Date',
    DPT12: 'DPT 12.x — 32-Bit unsigned', DPT13: 'DPT 13.x — 32-Bit signed',
    DPT14: 'DPT 14.x — 32-Bit IEEE Float', DPT16: 'DPT 16.x — 14-Byte String',
    DPT18: 'DPT 18.x — Scene Control', DPT19: 'DPT 19.x — Date and Time',
    DPT219: 'DPT 219.x — AlarmInfo',
  }
  const families = {}
  for (const dpt of allDpts.value) {
    const family = dpt.dpt_id.replace(/\.\d+$/, '')
    if (!families[family]) families[family] = []
    families[family].push(dpt)
  }
  return Object.entries(families).map(([family, dpts]) => ({
    family, label: familyLabels[family] ?? family, dpts,
  }))
})

const groupedInstances = computed(() => {
  const groups = {}
  for (const inst of allInstances.value) {
    if (!groups[inst.adapter_type]) groups[inst.adapter_type] = []
    groups[inst.adapter_type].push(inst)
  }
  return Object.entries(groups).map(([type, items]) => ({ type, items }))
})

// ---------------------------------------------------------------------------
// Init beim Bearbeiten
// ---------------------------------------------------------------------------

watch(() => props.initial, val => {
  if (!val) return
  form.adapter_instance_id = val.adapter_instance_id ?? ''
  form.direction           = val.direction
  form.enabled             = val.enabled
  Object.assign(cfg, val.config ?? {})
  if (cfg.state_group_address == null) cfg.state_group_address = ''
  if (cfg.publish_topic       == null) cfg.publish_topic = ''
  if (cfg.respond_to_read     == null) cfg.respond_to_read = false
  const ms = val.send_throttle_ms ?? 0
  if      (ms === 0)               { form.throttle_value = 0;            form.throttle_unit = 's'   }
  else if (ms % 3_600_000 === 0)   { form.throttle_value = ms/3_600_000; form.throttle_unit = 'h'   }
  else if (ms % 60_000 === 0)      { form.throttle_value = ms/60_000;    form.throttle_unit = 'min' }
  else if (ms % 1000 === 0)        { form.throttle_value = ms/1000;      form.throttle_unit = 's'   }
  else                             { form.throttle_value = ms;            form.throttle_unit = 'ms'  }
  form.send_on_change     = val.send_on_change     ?? false
  form.send_min_delta     = val.send_min_delta     ?? null
  form.send_min_delta_pct = val.send_min_delta_pct ?? null
  const f = val.value_formula ?? ''
  form.value_formula  = f
  form.formula_preset = f ? '__custom__' : ''
}, { immediate: true })

onMounted(async () => {
  try {
    const [instRes, dptRes] = await Promise.all([adapterApi.listInstances(), adapterApi.knxDpts()])
    allInstances.value = instRes.data
    allDpts.value      = dptRes.data
  } catch {}
})

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

function onGaSelect(item) {
  if (item.dpt && item.dpt !== cfg.dpt_id) cfg.dpt_id = item.dpt
}

function onPresetSelect(e) {
  const val = e.target.value
  if (!val) {
    form.value_formula  = ''
    form.formula_preset = ''
  } else if (val !== '__custom__') {
    form.value_formula  = val
    form.formula_preset = val
  }
}

function buildConfig() {
  const type = selectedAdapterType.value
  if (type === 'KNX') {
    const c = { group_address: cfg.group_address, dpt_id: cfg.dpt_id || 'DPT9.001' }
    if (cfg.state_group_address?.trim()) c.state_group_address = cfg.state_group_address.trim()
    if (cfg.respond_to_read) c.respond_to_read = true
    return c
  }
  if (type === 'MODBUS_TCP' || type === 'MODBUS_RTU') {
    return {
      unit_id: cfg.unit_id, register_type: cfg.register_type, address: cfg.address,
      count: cfg.count, data_format: cfg.data_format, scale_factor: cfg.scale_factor,
      byte_order: cfg.byte_order, word_order: cfg.word_order, poll_interval: cfg.poll_interval,
    }
  }
  if (type === 'MQTT') {
    const c = { topic: cfg.topic, retain: cfg.retain }
    if (cfg.publish_topic?.trim()) c.publish_topic = cfg.publish_topic.trim()
    return c
  }
  if (type === 'ONEWIRE') {
    return { sensor_id: cfg.sensor_id, sensor_type: cfg.sensor_type || 'DS18B20' }
  }
  return {}
}

async function submit() {
  error.value  = null
  saving.value = true
  try {
    const config     = buildConfig()
    const throttleMs = form.throttle_value > 0
      ? Math.round(form.throttle_value * THROTTLE_FACTORS[form.throttle_unit]) : null
    const filterPayload = {
      value_formula:      form.value_formula?.trim() || null,
      send_throttle_ms:   throttleMs,
      send_on_change:     form.send_on_change,
      send_min_delta:     (form.send_min_delta ?? 0) > 0     ? form.send_min_delta     : null,
      send_min_delta_pct: (form.send_min_delta_pct ?? 0) > 0 ? form.send_min_delta_pct : null,
    }
    if (props.initial) {
      await dpApi.updateBinding(props.dpId, props.initial.id, {
        direction: form.direction, config, enabled: form.enabled, ...filterPayload,
      })
    } else {
      if (!form.adapter_instance_id) {
        error.value = 'Bitte eine Adapter-Instanz wählen'; saving.value = false; return
      }
      await dpApi.createBinding(props.dpId, {
        adapter_instance_id: form.adapter_instance_id,
        direction: form.direction, config, enabled: form.enabled, ...filterPayload,
      })
    }
    emit('save')
  } catch (e) {
    error.value = e.response?.data?.detail ?? 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.tab-btn {
  @apply flex items-center px-4 py-2 text-sm text-slate-500 dark:text-slate-400 border-b-2 border-transparent
         hover:text-slate-700 dark:hover:text-slate-200 hover:border-slate-400 dark:hover:border-slate-500 transition-colors cursor-pointer;
}
.tab-active {
  @apply text-blue-500 dark:text-blue-400 border-blue-500 dark:border-blue-400 font-medium;
}
.section-header {
  @apply text-xs font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400 border-b border-slate-200 dark:border-slate-700 pb-1;
}
.optional-divider {
  @apply text-xs text-slate-500 border-b border-slate-200/80 dark:border-slate-700/50 pb-1 mt-1;
}
.optional { @apply text-slate-500 font-normal text-xs ml-1; }
.hint     { @apply text-xs text-slate-500 mt-0.5; }
</style>
