<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">

    <!-- Adapter-Instanz + Direction -->
    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">Adapter-Instanz *</label>
        <div v-if="props.initial" class="input bg-slate-800/50 text-slate-400 cursor-not-allowed">
          {{ currentInstanceName }}
        </div>
        <select v-else v-model="form.adapter_instance_id" class="input" required>
          <option value="">Instanz wählen …</option>
          <optgroup v-for="group in groupedInstances" :key="group.type" :label="group.type">
            <option v-for="inst in group.items" :key="inst.id" :value="inst.id">
              {{ inst.name }}
            </option>
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

    <!-- ── KNX Felder ── -->
    <template v-if="selectedAdapterType === 'KNX'">
      <div class="section-header">KNX Binding</div>
      <div class="grid grid-cols-2 gap-4">
        <div class="form-group">
          <label class="label">Gruppenadresse *</label>
          <GaCombobox
            v-model="cfg.group_address"
            placeholder="z.B. 1/2/3 oder Name suchen …"
            @select="onGaSelect"
          />
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
      </div>
      <div class="form-group">
        <label class="label">Status-Gruppenadresse <span class="optional">(optional)</span></label>
        <input v-model="cfg.state_group_address" class="input" placeholder="z.B. 1/2/4 — nur für DEST-Bindings" />
        <p class="hint">Rückmelde-GA für den Ist-Wert (DEST / BOTH)</p>
      </div>
    </template>

    <!-- ── Modbus TCP / RTU Felder ── -->
    <template v-if="selectedAdapterType === 'MODBUS_TCP' || selectedAdapterType === 'MODBUS_RTU'">
      <div class="section-header">Modbus Binding</div>
      <!-- Pflichtfelder -->
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
      <!-- Optionale Felder -->
      <div class="optional-divider">Optionale Einstellungen</div>
      <div class="grid grid-cols-4 gap-4">
        <div class="form-group">
          <label class="label">Unit ID</label>
          <input v-model.number="cfg.unit_id" type="number" min="0" max="255" class="input" />
          <p class="hint">Standard: 1</p>
        </div>
        <div class="form-group">
          <label class="label">Anzahl Register</label>
          <input v-model.number="cfg.count" type="number" min="1" max="125" class="input" />
          <p class="hint">Standard: 1</p>
        </div>
        <div class="form-group">
          <label class="label">Skalierungsfaktor</label>
          <input v-model.number="cfg.scale_factor" type="number" step="any" class="input" />
          <p class="hint">Standard: 1.0</p>
        </div>
        <div class="form-group">
          <label class="label">Abfrageintervall (s)</label>
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

    <!-- ── MQTT Felder ── -->
    <template v-if="selectedAdapterType === 'MQTT'">
      <div class="section-header">MQTT Binding</div>
      <div class="form-group">
        <label class="label">Topic *</label>
        <input v-model="cfg.topic" class="input" placeholder="z.B. haus/wohnzimmer/temperatur" required />
        <p class="hint">SOURCE/BOTH: abonniertes Topic; DEST: Publish-Topic (wenn kein separates Publish-Topic gesetzt)</p>
      </div>
      <div class="optional-divider">Optionale Einstellungen</div>
      <div class="grid grid-cols-2 gap-4">
        <div class="form-group">
          <label class="label">Publish-Topic <span class="optional">(optional)</span></label>
          <input v-model="cfg.publish_topic" class="input" placeholder="z.B. haus/wohnzimmer/temperatur/set" />
          <p class="hint">Separates Topic für DEST/BOTH (falls leer: Topic wird verwendet)</p>
        </div>
        <div class="form-group flex flex-col justify-end">
          <div class="flex items-center gap-2 mt-6">
            <input type="checkbox" id="mqtt_retain" v-model="cfg.retain" class="w-4 h-4 rounded" />
            <label for="mqtt_retain" class="text-sm text-slate-300">Retain</label>
          </div>
          <p class="hint">Broker speichert letzten Wert für neue Subscriber</p>
        </div>
      </div>
    </template>

    <!-- ── 1-Wire Felder ── -->
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

    <!-- Kein Adapter gewählt -->
    <div v-if="!selectedAdapterType && !props.initial" class="p-3 bg-slate-800/40 rounded-lg text-sm text-slate-500 text-center">
      Bitte zuerst eine Adapter-Instanz wählen
    </div>

    <!-- Aktiviert -->
    <div class="flex items-center gap-2">
      <input type="checkbox" id="enabled" v-model="form.enabled" class="w-4 h-4 rounded" />
      <label for="enabled" class="text-sm text-slate-300">Aktiviert</label>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3 pt-2">
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
import Spinner     from '@/components/ui/Spinner.vue'
import GaCombobox  from '@/components/ui/GaCombobox.vue'

const props = defineProps({
  dpId:    { type: String, required: true },
  initial: { type: Object, default: null },
})
const emit = defineEmits(['save', 'cancel'])

const saving       = ref(false)
const error        = ref(null)
const allInstances = ref([])
const allDpts      = ref([])

// DPTs nach Familie gruppiert (DPT1, DPT5, DPT9, …)
const groupedDpts = computed(() => {
  const families = {}
  const familyLabels = {
    DPT1:  'DPT 1.x — 1-Bit (Boolean)',
    DPT5:  'DPT 5.x — 8-Bit unsigned',
    DPT6:  'DPT 6.x — 8-Bit signed',
    DPT7:  'DPT 7.x — 16-Bit unsigned',
    DPT8:  'DPT 8.x — 16-Bit signed',
    DPT9:  'DPT 9.x — 16-Bit Float (Temperatur, Feuchte …)',
    DPT12: 'DPT 12.x — 32-Bit unsigned',
    DPT13: 'DPT 13.x — 32-Bit signed',
    DPT14: 'DPT 14.x — 32-Bit IEEE Float (Leistung, Spannung …)',
    DPT16: 'DPT 16.x — 14-Byte String',
  }
  for (const dpt of allDpts.value) {
    const family = dpt.dpt_id.replace(/\.\d+$/, '')   // "DPT9.001" → "DPT9"
    if (!families[family]) families[family] = []
    families[family].push(dpt)
  }
  return Object.entries(families).map(([family, dpts]) => ({
    family,
    label: familyLabels[family] ?? family,
    dpts,
  }))
})

const form = reactive({
  adapter_instance_id: '',
  direction:           'SOURCE',
  enabled:             true,
})

// Flaches Config-Objekt mit allen möglichen Feldern (Defaults)
const cfg = reactive({
  // KNX
  group_address:       '',
  dpt_id:              'DPT9.001',
  state_group_address: '',
  // Modbus
  address:             0,
  register_type:       'holding',
  data_format:         'uint16',
  unit_id:             1,
  count:               1,
  scale_factor:        1.0,
  poll_interval:       1.0,
  byte_order:          'big',
  word_order:          'big',
  // MQTT
  topic:               '',
  publish_topic:       '',
  retain:              false,
  // 1-Wire
  sensor_id:           '',
  sensor_type:         'DS18B20',
})

// Instanzen nach Typ gruppiert
const groupedInstances = computed(() => {
  const groups = {}
  for (const inst of allInstances.value) {
    if (!groups[inst.adapter_type]) groups[inst.adapter_type] = []
    groups[inst.adapter_type].push(inst)
  }
  return Object.entries(groups).map(([type, items]) => ({ type, items }))
})

// Aktuell gewählter Adapter-Typ
const selectedAdapterType = computed(() => {
  if (props.initial) return props.initial.adapter_type ?? null
  const inst = allInstances.value.find(i => i.id === form.adapter_instance_id)
  return inst?.adapter_type ?? null
})

// Name der aktuellen Instanz (beim Bearbeiten)
const currentInstanceName = computed(() => {
  if (!props.initial) return ''
  if (props.initial.instance_name) return `${props.initial.instance_name} (${props.initial.adapter_type})`
  return props.initial.adapter_type
})

// Init beim Bearbeiten: Config-Felder aus initial befüllen
watch(() => props.initial, val => {
  if (!val) return
  form.adapter_instance_id = val.adapter_instance_id ?? ''
  form.direction           = val.direction
  form.enabled             = val.enabled
  const c = val.config ?? {}
  Object.assign(cfg, c)
  // Leere Strings für optionale Felder sicherstellen
  if (cfg.state_group_address == null) cfg.state_group_address = ''
  if (cfg.publish_topic       == null) cfg.publish_topic = ''
}, { immediate: true })

onMounted(async () => {
  try {
    const [instRes, dptRes] = await Promise.all([
      adapterApi.listInstances(),
      adapterApi.knxDpts(),
    ])
    allInstances.value = instRes.data
    allDpts.value      = dptRes.data
  } catch {}
})

// GA aus Combobox gewählt → DPT als Vorschlag übernehmen
function onGaSelect(item) {
  if (item.dpt && item.dpt !== cfg.dpt_id) {
    cfg.dpt_id = item.dpt
  }
}

// Config-Objekt aus aktuellen cfg-Feldern zusammenbauen (nur relevante Felder)
function buildConfig() {
  const type = selectedAdapterType.value
  if (type === 'KNX') {
    const c = { group_address: cfg.group_address, dpt_id: cfg.dpt_id || 'DPT9.001' }
    if (cfg.state_group_address?.trim()) c.state_group_address = cfg.state_group_address.trim()
    return c
  }
  if (type === 'MODBUS_TCP' || type === 'MODBUS_RTU') {
    return {
      unit_id:       cfg.unit_id,
      register_type: cfg.register_type,
      address:       cfg.address,
      count:         cfg.count,
      data_format:   cfg.data_format,
      scale_factor:  cfg.scale_factor,
      byte_order:    cfg.byte_order,
      word_order:    cfg.word_order,
      poll_interval: cfg.poll_interval,
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
    const config = buildConfig()
    if (props.initial) {
      await dpApi.updateBinding(props.dpId, props.initial.id, {
        direction: form.direction, config, enabled: form.enabled,
      })
    } else {
      if (!form.adapter_instance_id) {
        error.value = 'Bitte eine Adapter-Instanz wählen'; saving.value = false; return
      }
      await dpApi.createBinding(props.dpId, {
        adapter_instance_id: form.adapter_instance_id,
        direction: form.direction, config, enabled: form.enabled,
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
.section-header {
  @apply text-xs font-semibold uppercase tracking-wider text-blue-400 border-b border-slate-700 pb-1;
}
.optional-divider {
  @apply text-xs text-slate-500 border-b border-slate-700/50 pb-1 mt-1;
}
.optional {
  @apply text-slate-500 font-normal text-xs ml-1;
}
.hint {
  @apply text-xs text-slate-500 mt-0.5;
}
</style>
