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
          <label class="label">Richtung *</label>
          <select v-model="form.direction" class="input">
            <option value="SOURCE">Lesen (von Adapter)</option>
            <option value="DEST">Schreiben (auf Adapter)</option>
            <option value="BOTH">Lesen/Schreiben (von/auf Adapter)</option>
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

        <!-- Topic with browser -->
        <div class="form-group">
          <label class="label">Topic *</label>
          <div class="flex gap-2">
            <input v-model="cfg.topic" class="input flex-1" placeholder="z.B. haus/wohnzimmer/temperatur" required />
            <button
              type="button"
              class="btn-secondary px-3 text-sm whitespace-nowrap"
              :disabled="!form.adapter_instance_id || mqttBrowseLoading"
              @click="mqttBrowse"
            >
              <span v-if="mqttBrowseLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
              {{ mqttBrowseLoading ? 'Scannen …' : 'Browse' }}
            </button>
          </div>
          <p class="hint">Lesen/Lesen+Schreiben: abonniertes Topic; Schreiben: Publish-Topic</p>

          <!-- Browse results -->
          <div
            v-if="mqttBrowseTopics.length > 0"
            class="mt-1 max-h-44 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
          >
            <button
              v-for="t in mqttBrowseTopics"
              :key="t"
              type="button"
              class="w-full text-left px-3 py-1.5 text-sm font-mono hover:bg-slate-50 dark:hover:bg-slate-700/50 truncate"
              @click="selectMqttTopic(t)"
            >{{ t }}</button>
          </div>
          <p v-if="mqttBrowseError" class="text-xs text-red-400 mt-1">{{ mqttBrowseError }}</p>
        </div>

        <div class="optional-divider">Optionale Einstellungen</div>
        <div class="grid grid-cols-2 gap-4">
          <!-- Publish-Topic: nur bei Lesen/Schreiben (BOTH) sichtbar -->
          <div v-if="form.direction === 'BOTH'" class="form-group">
            <label class="label">Publish-Topic <span class="optional">(optional)</span></label>
            <input v-model="cfg.publish_topic" class="input" placeholder="z.B. …/set" />
            <p class="hint">Topic für Schreiben — leer = Topic wird verwendet</p>
          </div>
          <!-- Retain: nur bei Schreiben (DEST) oder Lesen/Schreiben (BOTH) -->
          <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="form-group flex flex-col justify-end">
            <div class="flex items-center gap-2 mt-6">
              <input type="checkbox" id="mqtt_retain" v-model="cfg.retain" class="w-4 h-4 rounded" />
              <label for="mqtt_retain" class="text-sm text-slate-600 dark:text-slate-300">Retain</label>
            </div>
            <p class="hint">Broker speichert letzten Wert</p>
          </div>
        </div>

        <!-- Payload Template — only for DEST / BOTH -->
        <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="form-group">
          <label class="label">Payload-Template <span class="optional">(optional)</span></label>
          <input
            v-model="cfg.payload_template"
            class="input font-mono text-sm"
            placeholder='z.B. {"value": "###DP###", "unit": "°C"}'
          />
          <p class="hint"><code class="text-blue-400">###DP###</code> wird durch den Datenpunktwert ersetzt. Leer = Wert direkt als Payload.</p>
        </div>

        <!-- Source Data Type — SOURCE / BOTH only -->
        <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
          <label class="label">Quell-Datentyp <span class="optional">(optional)</span></label>
          <div class="flex gap-2 items-start">
            <select v-model="cfg.source_data_type" class="input flex-1">
              <option v-for="t in MQTT_SOURCE_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
            </select>
            <span v-if="mqttTypeCompat" class="mt-1.5 shrink-0 text-xs px-2 py-1 rounded-full font-medium" :class="mqttTypeCompat.cls">
              {{ mqttTypeCompat.label }}
            </span>
          </div>
          <p class="hint">
            Wie der eingehende Payload interpretiert wird.
            DataPoint-Typ: <code class="text-blue-400">{{ props.dpDataType }}</code>
          </p>

          <!-- JSON key extraction panel -->
          <div v-if="cfg.source_data_type === 'json'" class="mt-3 flex flex-col gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50">
            <div class="form-group">
              <div class="flex justify-between items-center mb-1">
                <label class="text-xs font-medium text-slate-500">Sample Payload</label>
                <button
                  type="button"
                  class="text-xs text-blue-500 hover:text-blue-400 disabled:opacity-40"
                  :disabled="!cfg.topic || mqttSampleLoading"
                  @click="loadMqttSample"
                >{{ mqttSampleLoading ? 'Lädt…' : '↺ Vom Topic laden' }}</button>
              </div>
              <textarea
                v-model="mqttJsonSample"
                class="input font-mono text-xs h-20 resize-y"
                placeholder='{"temperature": 22.5, "humidity": 65}'
                @input="onMqttJsonSampleInput"
              />
              <p v-if="mqttJsonParseError" class="text-xs text-red-400 mt-0.5">{{ mqttJsonParseError }}</p>
            </div>
            <div class="form-group">
              <label class="text-xs font-medium text-slate-500 mb-1 block">JSON-Schlüssel *</label>
              <div class="flex gap-2">
                <input
                  v-model="cfg.json_key"
                  class="input flex-1 font-mono text-sm"
                  placeholder="z.B. temperature"
                />
                <select
                  v-if="mqttJsonKeys.length"
                  v-model="cfg.json_key"
                  class="input w-52 shrink-0"
                >
                  <option value="">— aus Sample —</option>
                  <option v-for="k in mqttJsonKeys" :key="k.key" :value="k.key">
                    {{ k.key }}<template v-if="k.text"> = {{ k.text }}</template>
                  </option>
                </select>
              </div>
              <p class="hint">Schlüssel im JSON-Objekt, dessen Wert übernommen wird.</p>
            </div>
          </div>

          <!-- XML element-path extraction panel -->
          <div v-if="cfg.source_data_type === 'xml'" class="mt-3 flex flex-col gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50">
            <div class="form-group">
              <div class="flex justify-between items-center mb-1">
                <label class="text-xs font-medium text-slate-500">Sample Payload</label>
                <button
                  type="button"
                  class="text-xs text-blue-500 hover:text-blue-400 disabled:opacity-40"
                  :disabled="!cfg.topic || mqttSampleLoading"
                  @click="loadMqttSample"
                >{{ mqttSampleLoading ? 'Lädt…' : '↺ Vom Topic laden' }}</button>
              </div>
              <textarea
                v-model="mqttXmlSample"
                class="input font-mono text-xs h-20 resize-y"
                placeholder='<sensor><temperature>22.5</temperature><humidity>65</humidity></sensor>'
                @input="onMqttXmlSampleInput"
              />
              <p v-if="mqttXmlParseError" class="text-xs text-red-400 mt-0.5">{{ mqttXmlParseError }}</p>
            </div>
            <div class="form-group">
              <label class="text-xs font-medium text-slate-500 mb-1 block">Element-Pfad *</label>
              <div class="flex gap-2">
                <input
                  v-model="cfg.xml_path"
                  class="input flex-1 font-mono text-sm"
                  placeholder="z.B. temperature oder data/sensors/temperature"
                />
                <select
                  v-if="mqttXmlElements.length"
                  v-model="cfg.xml_path"
                  class="input w-52 shrink-0"
                >
                  <option value="">— aus Sample —</option>
                  <option v-for="el in mqttXmlElements" :key="el.path" :value="el.path">
                    {{ el.path }}<template v-if="el.text"> = {{ el.text }}</template>
                  </option>
                </select>
              </div>
              <p class="hint">
                Pfad relativ zum Root-Element (ET-XPath, z.B. <code class="text-blue-400">data/temperature</code>).
                Numerische Textwerte werden automatisch konvertiert.
              </p>
            </div>
          </div>
        </div>

        <!-- Value Mapping -->
        <div class="form-group">
          <label class="label">Wertzuordnung <span class="optional">(optional)</span></label>
          <select v-model="cfg.value_map_preset" class="input" @change="onValueMapPresetChange">
            <option v-for="p in VALUE_MAP_PRESETS" :key="p.key" :value="p.key">{{ p.label }}</option>
          </select>
          <div v-if="cfg.value_map_preset === 'custom'" class="mt-2">
            <textarea
              v-model="cfg.value_map_custom"
              class="input font-mono text-sm h-20 resize-y"
              placeholder='{"0": "off", "1": "on"}'
            />
            <p class="hint">JSON-Objekt mit String-Schlüsseln und -Werten.</p>
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
  dpDataType:     { type: String,  default: 'UNKNOWN' },  // DataPoint.data_type for compat check
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

const VALUE_MAP_PRESETS = [
  { key: '',            label: '— keine Wertzuordnung —',            map: null },
  { key: 'num_invert',  label: '0 ↔ 1 (numerisch invertieren)',       map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: 'true/false → on/off',                 map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: 'on/off → true/false',                 map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: '0/1 → off/on',                        map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: 'off/on → 0/1',                        map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: 'Benutzerdefiniert (JSON) …',           map: null },
]

const cfg = reactive({
  group_address: '', dpt_id: 'DPT9.001', state_group_address: '', respond_to_read: false,
  address: 0, register_type: 'holding', data_format: 'uint16',
  unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0,
  byte_order: 'big', word_order: 'big',
  topic: '', publish_topic: '', retain: false, payload_template: '',
  value_map: null, value_map_preset: '', value_map_custom: '',
  source_data_type: '', json_key: '', xml_path: '',
  sensor_id: '', sensor_type: 'DS18B20',
})

// MQTT source data type constants + compatibility map
const MQTT_SOURCE_TYPES = [
  { value: '',       label: '— kein Typ erzwingen (Standard) —' },
  { value: 'string', label: 'string' },
  { value: 'int',    label: 'int' },
  { value: 'float',  label: 'float' },
  { value: 'bool',   label: 'bool' },
  { value: 'json',   label: 'JSON — Schlüssel extrahieren' },
  { value: 'xml',    label: 'XML — Element-Pfad extrahieren' },
]

// DataPoint type → which MQTT source types are ok / warn / bad
const MQTT_TYPE_COMPAT = {
  BOOLEAN:  { ok: ['bool', 'auto'], warn: ['int', 'string'], bad: ['float', 'json', 'xml'] },
  INTEGER:  { ok: ['int', 'auto'],  warn: ['float'],          bad: ['bool', 'string', 'json', 'xml'] },
  FLOAT:    { ok: ['float', 'int', 'auto'], warn: [],          bad: ['bool', 'string', 'json', 'xml'] },
  STRING:   { ok: ['string', 'auto'], warn: ['int', 'float', 'bool'], bad: ['json', 'xml'] },
  DATE:     { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
  TIME:     { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
  DATETIME: { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
}

// JSON sample state (UI-only — not persisted)
const mqttJsonSample     = ref('')
const mqttJsonKeys       = ref([])   // [{ key: 'temperature', type: 'number' }, …]
const mqttJsonParseError = ref(null)

// XML sample state (UI-only — not persisted)
const mqttXmlSample      = ref('')
const mqttXmlElements    = ref([])   // [{ path: 'data/temperature', text: '22.5' }, …]
const mqttXmlParseError  = ref(null)

// Shared loading state for sample fetch
const mqttSampleLoading  = ref(false)

// MQTT topic browser state
const mqttBrowseTopics = ref([])
const mqttBrowseLoading = ref(false)
const mqttBrowseError  = ref(null)

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

// Compatibility badge for MQTT source_data_type vs DataPoint data_type
const mqttTypeCompat = computed(() => {
  const sdt = cfg.source_data_type ?? 'auto'
  if (!sdt || sdt === 'json' || sdt === 'xml') return null  // no badge — depends on extracted value
  const dpType = (props.dpDataType ?? 'UNKNOWN').toUpperCase()
  const compat = MQTT_TYPE_COMPAT[dpType]
  if (!compat) return null                             // UNKNOWN → no badge
  if (compat.ok.includes(sdt))
    return { cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400', label: 'kompatibel' }
  if (compat.warn.includes(sdt))
    return { cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400', label: 'Konvertierung nötig' }
  return { cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400', label: 'inkompatibel' }
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
  if (cfg.payload_template    == null) cfg.payload_template = ''
  if (cfg.source_data_type   == null) cfg.source_data_type = ''
  if (cfg.json_key           == null) cfg.json_key = ''
  if (cfg.xml_path           == null) cfg.xml_path = ''
  // Restore value_map UI state from loaded config
  if (cfg.value_map && typeof cfg.value_map === 'object') {
    const mapStr = JSON.stringify(cfg.value_map)
    const preset = VALUE_MAP_PRESETS.find(p => p.map && JSON.stringify(p.map) === mapStr)
    cfg.value_map_preset = preset?.key ?? 'custom'
    cfg.value_map_custom = preset ? '' : JSON.stringify(cfg.value_map, null, 2)
  } else {
    cfg.value_map_preset = ''
    cfg.value_map_custom = ''
  }
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

async function mqttBrowse() {
  mqttBrowseLoading.value = true
  mqttBrowseError.value   = null
  mqttBrowseTopics.value  = []
  try {
    const res = await adapterApi.mqttBrowseTopics(form.adapter_instance_id)
    mqttBrowseTopics.value = res.data
    if (res.data.length === 0) mqttBrowseError.value = 'Keine Topics empfangen – Broker erreichbar?'
  } catch (e) {
    mqttBrowseError.value = e.response?.data?.detail ?? 'Fehler beim Abrufen der Topics'
  } finally {
    mqttBrowseLoading.value = false
  }
}

function selectMqttTopic(topic) {
  cfg.topic = topic
  mqttBrowseTopics.value = []
  mqttBrowseError.value  = null
}

function onValueMapPresetChange() {
  if (cfg.value_map_preset !== 'custom') cfg.value_map_custom = ''
}

async function loadMqttSample() {
  const instanceId = form.adapter_instance_id || props.initial?.adapter_instance_id
  const topic = cfg.topic?.trim()
  if (!instanceId || !topic) return
  mqttSampleLoading.value = true
  // Clear previous errors so the user sees the loading state
  mqttJsonParseError.value = null
  mqttXmlParseError.value  = null
  try {
    const { data } = await adapterApi.mqttSamplePayload(instanceId, topic)
    if (cfg.source_data_type === 'json') {
      mqttJsonSample.value = data.payload
      onMqttJsonSampleInput()
    } else if (cfg.source_data_type === 'xml') {
      mqttXmlSample.value = data.payload
      onMqttXmlSampleInput()
    }
  } catch (e) {
    const msg = e.response?.data?.detail ?? 'Kein Payload empfangen'
    if (cfg.source_data_type === 'json') mqttJsonParseError.value = msg
    if (cfg.source_data_type === 'xml')  mqttXmlParseError.value  = msg
  } finally {
    mqttSampleLoading.value = false
  }
}

// Auto-load payload when switching to JSON/XML mode (if topic already set)
watch(() => cfg.source_data_type, sdt => {
  if (sdt === 'json' || sdt === 'xml') loadMqttSample()
})

function collectXmlLeafPaths(el, prefix) {
  const result = []

  // Group children by tag name so we can detect repeated elements
  const byTag = {}
  for (const child of el.children) {
    ;(byTag[child.tagName] ??= []).push(child)
  }

  for (const [tag, siblings] of Object.entries(byTag)) {
    for (let i = 0; i < siblings.length; i++) {
      const child = siblings[i]

      // Build path segment — include attribute predicate when helpful
      let segment = tag
      if (siblings.length > 1 || child.attributes.length > 0) {
        // Prefer a named attribute (e.g. id) over positional index
        const attr = child.attributes[0]
        segment = attr
          ? `${tag}[@${attr.name}='${attr.value}']`
          : `${tag}[${i + 1}]`
      }

      const path = prefix ? `${prefix}/${segment}` : segment

      if (child.children.length === 0) {
        result.push({ path, text: child.textContent.trim() })
      } else {
        result.push(...collectXmlLeafPaths(child, path))
      }
    }
  }
  return result
}

function onMqttXmlSampleInput() {
  mqttXmlParseError.value = null
  mqttXmlElements.value = []
  const s = mqttXmlSample.value.trim()
  if (!s) return
  const parser = new DOMParser()
  const doc = parser.parseFromString(s, 'application/xml')
  const parseErr = doc.querySelector('parsererror')
  if (parseErr) {
    mqttXmlParseError.value = `Kein gültiges XML: ${parseErr.textContent.split('\n')[0].trim()}`
    return
  }
  mqttXmlElements.value = collectXmlLeafPaths(doc.documentElement, '')
  if (mqttXmlElements.value.length === 0)
    mqttXmlParseError.value = 'Keine Kind-Elemente gefunden'
}

function onMqttJsonSampleInput() {
  mqttJsonParseError.value = null
  mqttJsonKeys.value = []
  const s = mqttJsonSample.value.trim()
  if (!s) return
  try {
    const obj = JSON.parse(s)
    if (obj !== null && typeof obj === 'object' && !Array.isArray(obj)) {
      mqttJsonKeys.value = Object.entries(obj).map(([k, v]) => ({
        key: k,
        type: v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v,
        text: v === null ? 'null' : Array.isArray(v) || typeof v === 'object' ? JSON.stringify(v) : String(v),
      }))
    } else {
      mqttJsonParseError.value = 'Sample muss ein JSON-Objekt sein (kein Array / Primitivwert)'
    }
  } catch (e) {
    mqttJsonParseError.value = `Kein gültiges JSON: ${e.message}`
  }
}

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
    if (cfg.publish_topic?.trim())    c.publish_topic    = cfg.publish_topic.trim()
    if (cfg.payload_template?.trim()) c.payload_template = cfg.payload_template.trim()
    // source_data_type + json_key
    if (cfg.source_data_type) {
      c.source_data_type = cfg.source_data_type
      if (cfg.source_data_type === 'json' && cfg.json_key?.trim())
        c.json_key = cfg.json_key.trim()
      if (cfg.source_data_type === 'xml' && cfg.xml_path?.trim())
        c.xml_path = cfg.xml_path.trim()
    }
    // Resolve value_map from preset or custom JSON
    let valueMap = null
    if (cfg.value_map_preset === 'custom') {
      try { valueMap = JSON.parse(cfg.value_map_custom) } catch { /* invalid JSON — ignore */ }
    } else if (cfg.value_map_preset) {
      valueMap = VALUE_MAP_PRESETS.find(p => p.key === cfg.value_map_preset)?.map ?? null
    }
    if (valueMap) c.value_map = valueMap
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
