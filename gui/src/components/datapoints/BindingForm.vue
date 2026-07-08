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

      <div class="grid gap-4" :class="selectedAdapterType === 'ANWESENHEITSSIMULATION' ? 'grid-cols-1' : 'grid-cols-2'">
        <div class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.adapterInstanceLabel') }}</label>
          <div v-if="props.initial" class="input bg-slate-100 dark:bg-slate-800/50 text-slate-400 cursor-not-allowed">
            {{ currentInstanceName }}
          </div>
          <select v-else v-model="form.adapter_instance_id" class="input" required data-testid="select-adapter-instance">
            <option value="">{{ $t('adapters.bindingForm.selectInstance') }}</option>
            <optgroup v-for="group in groupedInstances" :key="group.type" :label="group.type">
              <option v-for="inst in group.items" :key="inst.id" :value="inst.id">{{ inst.name }}</option>
            </optgroup>
          </select>
        </div>
        <div v-if="selectedAdapterType !== 'ANWESENHEITSSIMULATION' && selectedAdapterType !== 'MESSAGE'" class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.directionLabel') }}</label>
          <select
            v-model="form.direction"
            class="input"
            :disabled="selectedAdapterType === 'ZEITSCHALTUHR'"
            data-testid="select-direction"
          >
            <option value="SOURCE">{{ $t('adapters.bindingForm.directionRead') }}</option>
            <option v-if="selectedAdapterType !== 'ZEITSCHALTUHR'" value="DEST">{{ $t('adapters.bindingForm.directionWrite') }}</option>
            <option v-if="selectedAdapterType !== 'ZEITSCHALTUHR'" value="BOTH">{{ $t('adapters.bindingForm.directionReadWrite') }}</option>
          </select>
          <p v-if="selectedAdapterType === 'ZEITSCHALTUHR'" class="hint">
            {{ $t('adapters.bindingForm.timerReadOnlyHint') }}
          </p>
        </div>
      </div>

      <!-- KNX -->
      <BindingFormKnx
        v-if="selectedAdapterType === 'KNX'"
          :cfg="cfg"
          :form="form"
          :grouped-dpts="groupedDpts"
          :dp-persist-value="props.dpPersistValue"
          @ga-select="onGaSelect"
        />

      <!-- Modbus -->
      <BindingFormModbus
        v-if="selectedAdapterType === 'MODBUS_TCP' || selectedAdapterType === 'MODBUS_RTU'"
          :cfg="cfg"
        />

      <!-- MQTT -->
      <BindingFormMqtt
        v-if="selectedAdapterType === 'MQTT'"
          :cfg="cfg"
          :form="form"
          :mqtt-source-types="MQTT_SOURCE_TYPES"
          :mqtt-type-compat="mqttTypeCompat"
          :dp-data-type="props.dpDataType"
          :mqtt-browse-topics="mqttBrowseTopics"
          :mqtt-browse-loading="mqttBrowseLoading"
          :mqtt-browse-error="mqttBrowseError"
          v-model:mqtt-json-sample="mqttJsonSample"
          :mqtt-json-keys="mqttJsonKeys"
          :mqtt-json-parse-error="mqttJsonParseError"
          v-model:mqtt-xml-sample="mqttXmlSample"
          :mqtt-xml-elements="mqttXmlElements"
          :mqtt-xml-parse-error="mqttXmlParseError"
          :mqtt-sample-loading="mqttSampleLoading"
          @mqtt-browse="mqttBrowse"
          @select-mqtt-topic="selectMqttTopic"
          @load-mqtt-sample="loadMqttSample"
          @mqtt-json-sample-input="onMqttJsonSampleInput"
          @mqtt-xml-sample-input="onMqttXmlSampleInput"
        />

      <!-- 1-Wire -->
      <BindingFormOnewire
        v-if="selectedAdapterType === 'ONEWIRE'"
          :cfg="cfg"
        />

      <!-- Home Assistant -->
      <BindingFormHomeAssistant
        v-if="selectedAdapterType === 'HOME_ASSISTANT'"
          :cfg="cfg"
        />

      <!-- ioBroker -->
      <BindingFormIoBroker
        v-if="selectedAdapterType === 'IOBROKER'"
          :cfg="cfg"
          :form="form"
          :selected-instance-id="selectedInstanceId"
          :iobroker-states="iobrokerStates"
          :iobroker-browse-loading="iobrokerBrowseLoading"
          :iobroker-browse-error="iobrokerBrowseError"
          :show-advanced-tabs="showAdvancedTabs"
          @iobroker-state-input="onIoBrokerStateInput"
          @browse-iobroker-states="browseIoBrokerStates"
          @select-iobroker-state="selectIoBrokerState"
          @toggle-advanced-tabs="showAdvancedTabs = !showAdvancedTabs"
        />

      <!-- Zeitschaltuhr -->
      <BindingFormTimer
        v-if="selectedAdapterType === 'ZEITSCHALTUHR'"
          :cfg="cfg"
          :zt-holidays="ztHolidays"
          :zt-holidays-loading="ztHolidaysLoading"
          :zt-holidays-error="ztHolidaysError"
          :weekday-shorts="WEEKDAY_SHORTS"
          :month-shorts="MONTH_SHORTS"
          :win-months="WIN_MONTHS"
          :win-from="winFrom"
          :win-to="winTo"
          :build-win-expr="buildWinExpr"
          :describe-win-ep="describeWinEp"
          @zt-toggle-holiday="ztToggleHoliday"
          @load-zsu-holidays="loadZsuHolidays"
          @zt-toggle-weekday="ztToggleWeekday"
          @zt-toggle-month="ztToggleMonth"
          @win-type-change="onWinTypeChange"
        />

      <!-- Anwesenheitssimulation — per-Binding Overrides -->
      <BindingFormPresenceSimulation
        v-if="selectedAdapterType === 'ANWESENHEITSSIMULATION'"
          :cfg="cfg"
          v-model:anw-offset-select="anwOffsetSelect"
          @anw-offset-select-change="onAnwOffsetSelectChange"
          @anw-offset-custom-input="onAnwOffsetCustomInput"
        />

      <!-- SNMP -->
      <BindingFormSnmp
        v-if="selectedAdapterType === 'SNMP'"
          :cfg="cfg"
          :form="form"
          :selected-instance-id="selectedInstanceId"
          v-model:snmp-walk-root="snmpWalkRoot"
          :snmp-walk-results="snmpWalkResults"
          :snmp-walk-loading="snmpWalkLoading"
          :snmp-walk-error="snmpWalkError"
          :snmp-walk-has-more="snmpWalkHasMore"
          @snmp-walk="snmpWalk"
        />

      <!-- MESSAGE -->
      <BindingFormMessage
        v-if="selectedAdapterType === 'MESSAGE'"
          :cfg="cfg"
          :selected-instance="selectedInstance"
        />

      <div v-if="!selectedAdapterType && !props.initial" class="p-3 bg-slate-100/80 dark:bg-slate-800/40 rounded-lg text-sm text-slate-500 text-center">
        {{ $t('adapters.bindingForm.selectAdapterInstanceFirst') }}
      </div>

    </div><!-- /TAB Verbindung -->

    <!-- ── TAB: Transformation ── -->
    <div v-show="activeTab === 'transform'" class="flex flex-col gap-4">
      <div class="section-header">{{ $t('adapters.bindingForm.transformSection') }}</div>
      <div class="form-group">
        <label class="label">
          {{ $t('adapters.bindingForm.formulaLabel') }}
          <span class="text-slate-500 font-normal ml-1">— {{ $t('adapters.bindingForm.formulaVariable') }}: <code class="text-blue-400">x</code></span>
        </label>
        <div class="flex gap-2">
          <select class="input w-52 shrink-0" v-model="form.formula_preset" @change="onPresetSelect">
            <option value="">{{ $t('adapters.bindingForm.formulaPresetSelect') }}</option>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupMultiply')">
              <option value="x * 86400">{{ $t('adapters.bindingForm.formulaMul86400') }}</option>
              <option value="x * 3600">{{ $t('adapters.bindingForm.formulaMul3600') }}</option>
              <option value="x * 1440">{{ $t('adapters.bindingForm.formulaMul1440') }}</option>
              <option value="x * 1000">× 1.000</option>
              <option value="x * 100">× 100</option>
              <option value="x * 60">{{ $t('adapters.bindingForm.formulaMul60') }}</option>
              <option value="x * 10">× 10</option>
            </optgroup>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupDivide')">
              <option value="x / 10">{{ $t('adapters.bindingForm.formulaDiv10') }}</option>
              <option value="x / 60">{{ $t('adapters.bindingForm.formulaDiv60') }}</option>
              <option value="x / 100">{{ $t('adapters.bindingForm.formulaDiv100') }}</option>
              <option value="x / 1000">{{ $t('adapters.bindingForm.formulaDiv1000') }}</option>
              <option value="x / 1440">{{ $t('adapters.bindingForm.formulaDiv1440') }}</option>
              <option value="x / 3600">{{ $t('adapters.bindingForm.formulaDiv3600') }}</option>
              <option value="x / 86400">{{ $t('adapters.bindingForm.formulaDiv86400') }}</option>
            </optgroup>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupCustom')">
              <option value="__custom__">{{ $t('adapters.bindingForm.formulaCustom') }}</option>
            </optgroup>
          </select>
          <input
            v-model="form.value_formula"
            type="text"
            :placeholder="$t('adapters.bindingForm.formulaPlaceholder')"
            class="input flex-1 font-mono text-sm"
            @input="form.formula_preset = '__custom__'"
          />
        </div>
        <p class="hint mt-1">
          {{ $t('adapters.bindingForm.formulaHintPrefix') }} <code class="text-blue-400">{{ $t('adapters.bindingForm.formulaFunctions') }}</code>
          {{ $t('adapters.bindingForm.formulaHintSuffix') }} <code class="text-blue-400">{{ $t('adapters.bindingForm.formulaMathNamespace') }}</code>{{ $t('adapters.bindingForm.formulaHintEnd') }}
        </p>
      </div>

      <div class="optional-divider">{{ $t('adapters.bindingForm.valueMapDivider') }}</div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.valueMapLabel') }} <span class="optional">{{ $t('adapters.bindingForm.iobOptional') }}</span></label>
        <select v-model="form.value_map_preset" class="input" @change="onValueMapPresetChange">
          <option v-for="p in VALUE_MAP_PRESETS" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>
        <div v-if="form.value_map_preset === 'custom'" class="mt-2">
          <textarea
            v-model="form.value_map_custom"
            @input="onValueMapCustomInput"
            class="input font-mono text-sm h-28 resize-y"
            :placeholder="$t('adapters.bindingForm.valueMapCustomPlaceholder')"
          />
          <p v-if="form.value_map_custom_error" class="text-xs text-red-400 mt-0.5">{{ form.value_map_custom_error }}</p>
          <p class="hint">{{ $t('adapters.bindingForm.valueMapCustomHint') }}</p>
        </div>
        <p class="hint mt-1">{{ $t('adapters.bindingForm.valueMapHint') }}</p>
      </div>
    </div><!-- /TAB Transformation -->

    <!-- ── TAB: Filter ── -->
    <div v-show="activeTab === 'filter'" class="flex flex-col gap-4">
      <div class="section-header">{{ $t('adapters.bindingForm.timeFilterSection') }}</div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.throttleLabel') }}</label>
        <div class="flex gap-2">
          <input v-model.number="form.throttle_value" type="number" min="0" step="1" :placeholder="$t('adapters.bindingForm.throttlePlaceholder')" class="input flex-1" />
          <select v-model="form.throttle_unit" class="input w-24">
            <option value="ms">ms</option>
            <option value="s">s</option>
            <option value="min">min</option>
            <option value="h">h</option>
          </select>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.throttleHint') }}</p>
      </div>

      <div class="section-header">{{ $t('adapters.bindingForm.valueFilterSection') }}</div>
      <div class="flex items-center gap-2">
        <input type="checkbox" id="send_on_change" v-model="form.send_on_change" class="w-4 h-4 rounded" />
        <label for="send_on_change" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.sendOnChangeLabel') }}</label>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.minDeltaLabel') }}</label>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-slate-400 mb-1 block">{{ $t('adapters.bindingForm.minDeltaAbsolute') }}</label>
            <input v-model.number="form.send_min_delta" type="number" min="0" step="any" :placeholder="$t('adapters.bindingForm.minDeltaAbsolutePlaceholder')" class="input" />
          </div>
          <div>
            <label class="text-xs text-slate-400 mb-1 block">{{ $t('adapters.bindingForm.minDeltaRelative') }}</label>
            <input v-model.number="form.send_min_delta_pct" type="number" min="0" step="any" :placeholder="$t('adapters.bindingForm.minDeltaRelativePlaceholder')" class="input" />
          </div>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.minDeltaHint') }}</p>
      </div>
    </div><!-- /TAB Filter -->

    <!-- Aktiviert -->
    <div class="flex items-center gap-2 border-t border-slate-200 dark:border-slate-700/60 pt-3">
      <input type="checkbox" id="enabled" v-model="form.enabled" class="w-4 h-4 rounded" />
      <label for="enabled" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('common.enabled') }}</label>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">{{ $t('common.cancel') }}</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        {{ $t('common.save') }}
      </button>
    </div>

  </form>
</template>

<script setup>
import { ref, reactive, watch, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { dpApi, adapterApi } from '@/api/client'
import Spinner    from '@/components/ui/Spinner.vue'
import BindingFormKnx from '@/components/datapoints/binding-form/BindingFormKnx.vue'
import BindingFormModbus from '@/components/datapoints/binding-form/BindingFormModbus.vue'
import BindingFormMqtt from '@/components/datapoints/binding-form/BindingFormMqtt.vue'
import BindingFormOnewire from '@/components/datapoints/binding-form/BindingFormOnewire.vue'
import BindingFormHomeAssistant from '@/components/datapoints/binding-form/BindingFormHomeAssistant.vue'
import BindingFormIoBroker from '@/components/datapoints/binding-form/BindingFormIoBroker.vue'
import BindingFormTimer from '@/components/datapoints/binding-form/BindingFormTimer.vue'
import BindingFormPresenceSimulation from '@/components/datapoints/binding-form/BindingFormPresenceSimulation.vue'
import BindingFormSnmp from '@/components/datapoints/binding-form/BindingFormSnmp.vue'
import BindingFormMessage from '@/components/datapoints/binding-form/BindingFormMessage.vue'

const props = defineProps({
  dpId:           { type: String,  required: true },
  initial:        { type: Object,  default: null },
  dpPersistValue: { type: Boolean, default: false },
  dpDataType:     { type: String,  default: 'UNKNOWN' },  // DataPoint.data_type for compat check
})
const emit = defineEmits(['save', 'cancel'])
const { t } = useI18n()

const saving       = ref(false)
const error        = ref(null)
const allInstances = ref([])
const allDpts      = ref([])
const activeTab    = ref('conn')
const showAdvancedTabs = ref(false)
const anwOffsetSelect  = ref('')  // '' | '1' | '7' | '14' | 'custom'

// ---------------------------------------------------------------------------
// Form-State
// ---------------------------------------------------------------------------

const THROTTLE_FACTORS = { ms: 1, s: 1000, min: 60_000, h: 3_600_000 }

const form = reactive({
  adapter_instance_id:  '',
  direction:            'SOURCE',
  enabled:              true,
  value_formula:        '',
  formula_preset:       '',
  value_map_preset:     '',
  value_map_custom:     '',
  value_map_custom_error: '',
  throttle_value:       0,
  throttle_unit:        's',
  send_on_change:       false,
  send_min_delta:       null,
  send_min_delta_pct:   null,
})

const VALUE_MAP_PRESETS = [
  { key: '',            label: t('adapters.bindingForm.noValueMapping'),            map: null },
  { key: 'num_invert',  label: t('adapters.bindingForm.valueMapNumInvert'),         map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: t('adapters.bindingForm.valueMapBoolOnOff'),         map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: t('adapters.bindingForm.valueMapOnOffBool'),         map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: t('adapters.bindingForm.valueMapNumOnOff'),          map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: t('adapters.bindingForm.valueMapOnOffNum'),          map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: t('adapters.bindingForm.customValueMapping'),         map: null },
]

const cfg = reactive({
  group_address: '', dpt_id: 'DPT9.001', state_group_address: '', respond_to_read: false,
  address: 0, register_type: 'holding', data_format: 'uint16',
  unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0,
  byte_order: 'big', word_order: 'big',
  topic: '', publish_topic: '', retain: false, payload_template: '',
  source_data_type: '', json_key: '', xml_path: '',
  sensor_id: '', sensor_type: 'DS18B20',
  // HOME_ASSISTANT
  entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '',
  // IOBROKER
  state_id: '', command_state_id: '', ack: false,
  // ANWESENHEITSSIMULATION
  offset_override: null,
  on_presence_override: null,
  on_presence_value: '',
  // SNMP
  host: '192.168.1.1',
  port: 161,
  oid: '',
  data_type: 'auto',
  timeout: 5.0,
  retries: 1,
  // MESSAGE
  operator: '==',
  compare_value: '',
  message: '###DPN###: ###DP### ###DPU###',
  title: '',
  providers: [],
  priority: null,
  cooldown_seconds: 0,
  send_on_change: true,
  // ZEITSCHALTUHR
  timer_type: 'daily', meta_type: 'none',
  weekdays: [0,1,2,3,4,5,6], months: [], day_of_month: 0,
  time_ref: 'absolute', hour: 0, minute: 0, offset_minutes: 0,
  solar_altitude_deg: 0.0, sun_direction: 'rising',
  every_hour: false, every_minute: false,
  holiday_mode: 'ignore', vacation_mode: 'ignore',
  selected_holidays: [],
  date_window_enabled: false,
  date_window_from: '',
  date_window_to: '',
  value: '1',
})

// MQTT source data type constants + compatibility map
const MQTT_SOURCE_TYPES = [
  { value: '',       label: t('adapters.bindingForm.noForcedType') },
  { value: 'string', label: 'string' },
  { value: 'int',    label: 'int' },
  { value: 'float',  label: 'float' },
  { value: 'bool',   label: 'bool' },
  { value: 'json',   label: t('adapters.bindingForm.jsonExtractKey') },
  { value: 'xml',    label: t('adapters.bindingForm.xmlExtractPath') },
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

// ioBroker state browser state
const iobrokerStates = ref([])
const iobrokerBrowseLoading = ref(false)
const iobrokerBrowseError = ref(null)
let iobrokerBrowseTimer = null

// SNMP Walk state
const snmpWalkResults = ref([])
const snmpWalkLoading = ref(false)
const snmpWalkError   = ref(null)
const snmpWalkHasMore = ref(false)
const snmpWalkRoot    = ref('1.3.6.1.2.1')

// Zeitschaltuhr holiday list state (for Feiertagsschaltuhr)
const ztHolidays = ref([])   // [{date, name}, …] sorted by date
const ztHolidaysLoading = ref(false)
const ztHolidaysError = ref(null)

// Date window UI state
const WIN_MONTHS = [
  { v: 1, l: t('common.months.january') }, { v: 2, l: t('common.months.february') }, { v: 3, l: t('common.months.march') },
  { v: 4, l: t('common.months.april') }, { v: 5, l: t('common.months.may') }, { v: 6, l: t('common.months.june') },
  { v: 7, l: t('common.months.july') }, { v: 8, l: t('common.months.august') }, { v: 9, l: t('common.months.september') },
  { v: 10, l: t('common.months.october') }, { v: 11, l: t('common.months.november') }, { v: 12, l: t('common.months.december') },
]
const WEEKDAY_SHORTS = [
  t('adapters.bindingForm.ztWeekdayMo'),
  t('adapters.bindingForm.ztWeekdayTu'),
  t('adapters.bindingForm.ztWeekdayWe'),
  t('adapters.bindingForm.ztWeekdayTh'),
  t('adapters.bindingForm.ztWeekdayFr'),
  t('adapters.bindingForm.ztWeekdaySa'),
  t('adapters.bindingForm.ztWeekdaySu'),
]
const MONTH_SHORTS = [
  t('adapters.bindingForm.ztMonthJan'),
  t('adapters.bindingForm.ztMonthFeb'),
  t('adapters.bindingForm.ztMonthMar'),
  t('adapters.bindingForm.ztMonthApr'),
  t('adapters.bindingForm.ztMonthMay'),
  t('adapters.bindingForm.ztMonthJun'),
  t('adapters.bindingForm.ztMonthJul'),
  t('adapters.bindingForm.ztMonthAug'),
  t('adapters.bindingForm.ztMonthSep'),
  t('adapters.bindingForm.ztMonthOct'),
  t('adapters.bindingForm.ztMonthNov'),
  t('adapters.bindingForm.ztMonthDec'),
]
const winFrom = reactive({ type: 'fixed', month: 1,  day: 1,  sign: '+', offset: 0, name: '' })
const winTo   = reactive({ type: 'fixed', month: 12, day: 31, sign: '+', offset: 0, name: '' })

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const selectedAdapterType = computed(() => {
  const explicitType = props.initial?.adapter_type
  if (explicitType) return explicitType
  const selectedId = props.initial?.adapter_instance_id ?? form.adapter_instance_id
  const inst = allInstances.value.find(i => String(i.id) === String(selectedId))
  return inst?.adapter_type ?? null
})

const currentInstanceName = computed(() => {
  if (!props.initial) return ''
  const type = selectedAdapterType.value
  if (props.initial.instance_name && type) return `${props.initial.instance_name} (${type})`
  if (props.initial.instance_name) return props.initial.instance_name
  if (type) return type
  return ''
})

const selectedInstanceId = computed(() => props.initial?.adapter_instance_id || form.adapter_instance_id)
const selectedInstance = computed(() => allInstances.value.find(i => String(i.id) === String(selectedInstanceId.value)) ?? null)

const visibleTabs = computed(() => {
  const tabs = [{ id: 'conn', label: t('logic.nodeConfig.tabs.connection'), badge: false }]
  if (selectedAdapterType.value && !['ZEITSCHALTUHR', 'ANWESENHEITSSIMULATION', 'MESSAGE'].includes(selectedAdapterType.value)) {
    if (selectedAdapterType.value === 'IOBROKER' && !showAdvancedTabs.value) return tabs
    const hasFormula = !!form.value_formula?.trim() || !!form.value_map_preset
    tabs.push({ id: 'transform', label: t('logic.nodeConfig.tabs.transform'), badge: hasFormula })
    const canUseFilter = selectedAdapterType.value === 'IOBROKER'
      || form.direction === 'DEST' || form.direction === 'BOTH'
    if (canUseFilter) {
      const hasFilter = form.throttle_value > 0 || form.send_on_change
        || (form.send_min_delta ?? 0) > 0 || (form.send_min_delta_pct ?? 0) > 0
      tabs.push({ id: 'filter', label: t('logic.nodeConfig.tabs.filter'), badge: hasFilter })
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
    return { cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400', label: t('adapters.bindingForm.compatible') }
  if (compat.warn.includes(sdt))
    return { cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400', label: t('adapters.bindingForm.conversionRequired') }
  return { cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400', label: t('adapters.bindingForm.incompatible') }
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
  // HOME_ASSISTANT defaults when loading
  if (cfg.entity_id        == null) cfg.entity_id        = ''
  if (cfg.attribute        == null) cfg.attribute        = ''
  if (cfg.service_domain   == null) cfg.service_domain   = ''
  if (cfg.service_name     == null) cfg.service_name     = ''
  if (cfg.service_data_key == null) cfg.service_data_key = ''
  // IOBROKER defaults when loading
  if (cfg.state_id         == null) cfg.state_id = ''
  if (cfg.command_state_id == null) cfg.command_state_id = ''
  if (cfg.ack              == null) cfg.ack = false
  // ZEITSCHALTUHR defaults when loading
  if (cfg.timer_type    == null) cfg.timer_type    = 'daily'
  if (cfg.meta_type     == null) cfg.meta_type     = 'none'
  if (cfg.weekdays      == null) cfg.weekdays      = [0,1,2,3,4,5,6]
  if (cfg.months        == null) cfg.months        = []
  if (cfg.day_of_month  == null) cfg.day_of_month  = 0
  if (cfg.time_ref      == null) cfg.time_ref      = 'absolute'
  if (cfg.hour          == null) cfg.hour          = 0
  if (cfg.minute        == null) cfg.minute        = 0
  if (cfg.offset_minutes == null) cfg.offset_minutes = 0
  if (cfg.solar_altitude_deg == null) cfg.solar_altitude_deg = 0.0
  if (cfg.sun_direction == null) cfg.sun_direction = 'rising'
  if (cfg.every_hour    == null) cfg.every_hour    = false
  if (cfg.every_minute  == null) cfg.every_minute  = false
  if (cfg.holiday_mode  == null) cfg.holiday_mode  = 'ignore'
  if (cfg.vacation_mode     == null) cfg.vacation_mode     = 'ignore'
  if (cfg.selected_holidays    == null) cfg.selected_holidays    = []
  if (cfg.date_window_enabled == null) cfg.date_window_enabled = false
  if (cfg.date_window_from    == null) cfg.date_window_from    = ''
  if (cfg.date_window_to      == null) cfg.date_window_to      = ''
  if (cfg.date_window_from) parseWinExprInto(cfg.date_window_from, winFrom)
  if (cfg.date_window_to)   parseWinExprInto(cfg.date_window_to,   winTo)
  if (cfg.value             == null) cfg.value             = '1'
  // ANWESENHEITSSIMULATION defaults + select sync
  if (cfg.offset_override      === undefined) cfg.offset_override      = null
  if (cfg.on_presence_override === undefined) cfg.on_presence_override = null
  if (cfg.on_presence_value    === undefined) cfg.on_presence_value    = ''
  // SNMP defaults when loading
  if (cfg.host     == null) cfg.host     = '192.168.1.1'
  if (cfg.port     == null) cfg.port     = 161
  if (cfg.oid      == null) cfg.oid      = ''
  if (cfg.data_type == null) cfg.data_type = 'auto'
  if (cfg.timeout  == null) cfg.timeout  = 5.0
  if (cfg.retries  == null) cfg.retries  = 1
  // MESSAGE defaults when loading
  if (cfg.operator == null) cfg.operator = '=='
  if (cfg.compare_value == null) cfg.compare_value = ''
  if (cfg.message == null) cfg.message = '###DPN###: ###DP### ###DPU###'
  if (cfg.title == null) cfg.title = ''
  if (cfg.providers == null) cfg.providers = []
  if (cfg.priority === undefined) cfg.priority = null
  if (cfg.cooldown_seconds == null) cfg.cooldown_seconds = 0
  if (cfg.send_on_change == null) cfg.send_on_change = true
  {
    const ANW_PRESETS = ['1', '7', '14']
    if (cfg.offset_override != null) {
      anwOffsetSelect.value = ANW_PRESETS.includes(String(cfg.offset_override)) ? String(cfg.offset_override) : 'custom'
    } else {
      anwOffsetSelect.value = ''
    }
  }
  // Restore value_map UI state from top-level binding field
  if (val.value_map && typeof val.value_map === 'object') {
    const mapStr = JSON.stringify(val.value_map)
    const preset = VALUE_MAP_PRESETS.find(p => p.map && JSON.stringify(p.map) === mapStr)
    form.value_map_preset = preset?.key ?? 'custom'
    form.value_map_custom = preset ? '' : JSON.stringify(val.value_map, null, 2)
  } else {
    form.value_map_preset = ''
    form.value_map_custom = ''
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
  // If editing an existing holiday-type binding, load holidays immediately
  if (cfg.timer_type === 'holiday' && selectedInstanceId.value) {
    await loadZsuHolidays()
  }
})

async function loadZsuHolidays() {
  const instanceId = selectedInstanceId.value
  if (!instanceId) return
  ztHolidaysLoading.value = true
  ztHolidaysError.value = null
  try {
    const { data } = await adapterApi.getZsuHolidays(instanceId)
    ztHolidays.value = data
  } catch (e) {
    ztHolidaysError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.holidaysLoadFailed')
  } finally {
    ztHolidaysLoading.value = false
  }
}

watch(() => [selectedAdapterType.value, cfg.timer_type, selectedInstanceId.value], ([type, timerType]) => {
  if (type === 'ZEITSCHALTUHR' && timerType === 'holiday' && selectedInstanceId.value) {
    loadZsuHolidays()
  }
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
    if (res.data.length === 0) mqttBrowseError.value = t('adapters.bindingForm.errors.noTopicsReceived')
  } catch (e) {
    mqttBrowseError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.topicsFetchFailed')
  } finally {
    mqttBrowseLoading.value = false
  }
}

function selectMqttTopic(topic) {
  cfg.topic = topic
  mqttBrowseTopics.value = []
  mqttBrowseError.value  = null
}

function onIoBrokerStateInput() {
  iobrokerBrowseError.value = null
  clearTimeout(iobrokerBrowseTimer)
  const q = cfg.state_id?.trim() ?? ''
  if (q.length < 2) {
    iobrokerStates.value = []
    return
  }
  iobrokerBrowseTimer = setTimeout(() => browseIoBrokerStates(), 350)
}

async function browseIoBrokerStates() {
  const instanceId = selectedInstanceId.value
  if (!instanceId) {
    iobrokerBrowseError.value = t('adapters.bindingForm.errors.selectIoBrokerInstanceFirst')
    return
  }
  iobrokerBrowseLoading.value = true
  iobrokerBrowseError.value = null
  try {
    const { data } = await adapterApi.iobrokerBrowseStates(instanceId, cfg.state_id?.trim() ?? '', 50)
    iobrokerStates.value = data
    if (data.length === 0) iobrokerBrowseError.value = t('adapters.bindingForm.errors.noStatesFound')
  } catch (e) {
    iobrokerBrowseError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.ioBrokerStatesLoadFailed')
  } finally {
    iobrokerBrowseLoading.value = false
  }
}

function selectIoBrokerState(state) {
  cfg.state_id = state.id
  if (state.type && !cfg.source_data_type) {
    const t = String(state.type).toLowerCase()
    if (t === 'boolean') cfg.source_data_type = 'bool'
    else if (t === 'number') cfg.source_data_type = 'float'
    else if (t === 'string') cfg.source_data_type = 'string'
  }
  if (!state.write && form.direction !== 'SOURCE') form.direction = 'SOURCE'
  iobrokerStates.value = []
  iobrokerBrowseError.value = null
}

async function snmpWalk(append = false) {
  const instanceId = selectedInstanceId.value
  if (!instanceId || !cfg.host) return
  snmpWalkLoading.value = true
  if (!append) {
    snmpWalkError.value = null
    snmpWalkResults.value = []
  }
  try {
    const rootOid  = snmpWalkRoot.value?.trim() || '1.3.6.1.2.1'
    const startOid = append && snmpWalkResults.value.length
      ? snmpWalkResults.value[snmpWalkResults.value.length - 1].oid
      : null
    const { data } = await adapterApi.snmpWalk(instanceId, cfg.host, rootOid, cfg.port || 161, 50, 10, startOid)
    if (append) {
      snmpWalkResults.value = [...snmpWalkResults.value, ...data]
    } else {
      snmpWalkResults.value = data
    }
    if (snmpWalkResults.value.length === 0) snmpWalkError.value = t('adapters.bindingForm.errors.noOidsFound')
    snmpWalkHasMore.value = data.length === 50
  } catch (e) {
    snmpWalkError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.snmpWalkFailed')
  } finally {
    snmpWalkLoading.value = false
  }
}

function onValueMapPresetChange() {
  if (form.value_map_preset !== 'custom') {
    form.value_map_custom = ''
    form.value_map_custom_error = ''
  }
}

function onValueMapCustomInput() {
  form.value_map_custom_error = ''
  if (!form.value_map_custom.trim()) return
  try {
    JSON.parse(form.value_map_custom)
  } catch (e) {
    form.value_map_custom_error = t('adapters.bindingForm.errors.invalidJson', { msg: e.message })
  }
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
    const msg = e.response?.data?.detail ?? t('adapters.bindingForm.errors.noPayloadReceived')
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

// Force direction to SOURCE for adapters that observe values instead of writing.
watch(selectedAdapterType, type => {
  if (type === 'ZEITSCHALTUHR' || type === 'MESSAGE') form.direction = 'SOURCE'
  if (type === 'IOBROKER') {
    activeTab.value = 'conn'
    showAdvancedTabs.value = false
  }
  if (type === 'SNMP' && !cfg.poll_interval) cfg.poll_interval = 30.0
})

watch(selectedInstanceId, (newId, oldId) => {
  if (oldId && newId !== oldId && selectedAdapterType.value === 'MESSAGE') {
    cfg.providers = []
  }
})

// Zeitschaltuhr helpers
function ztToggleWeekday(idx) {
  const i = cfg.weekdays.indexOf(idx)
  if (i >= 0) cfg.weekdays.splice(i, 1)
  else cfg.weekdays.push(idx)
}

function ztToggleMonth(m) {
  const i = cfg.months.indexOf(m)
  if (i >= 0) cfg.months.splice(i, 1)
  else cfg.months.push(m)
}

function ztToggleHoliday(name) {
  if (cfg.selected_holidays.length === 0) {
    // All selected (empty = no filter): unchecking one → select all except this one
    cfg.selected_holidays = ztHolidays.value.map(h => h.name).filter(n => n !== name)
  } else {
    const i = cfg.selected_holidays.indexOf(name)
    if (i >= 0) {
      cfg.selected_holidays.splice(i, 1)
      // If all removed → treat as "all selected" (empty list = no filter)
    } else {
      cfg.selected_holidays.push(name)
      // If now all are explicitly selected, collapse to empty (= no filter)
      if (cfg.selected_holidays.length === ztHolidays.value.length) {
        cfg.selected_holidays = []
      }
    }
  }
}

function buildWinExpr(ep) {
  switch (ep.type) {
    case 'fixed': {
      const mm = String(ep.month).padStart(2, '0')
      const dd = String(ep.day).padStart(2, '0')
      return `${mm}-${dd}`
    }
    case 'easter':
      return ep.offset === 0 ? 'easter+0' : `easter${ep.sign}${ep.offset}`
    case 'advent':
      return ep.offset === 0 ? 'advent+0' : `advent${ep.sign}${ep.offset}`
    case 'holiday_name':
      if (!ep.name) return ''
      return ep.offset === 0 ? `holiday:${ep.name}` : `holiday:${ep.name}${ep.sign}${ep.offset}`
    default:
      return ''
  }
}

function parseWinExprInto(expr, ep) {
  if (!expr) return
  const exprUp = expr.toUpperCase()
  const fixedM = expr.match(/^(\d{1,2})-(\d{1,2})$/)
  if (fixedM) {
    ep.type = 'fixed'; ep.month = parseInt(fixedM[1], 10); ep.day = parseInt(fixedM[2], 10)
    return
  }
  const easterM = exprUp.match(/^EASTER([+-])?(\d+)?$/)
  if (easterM) {
    ep.type = 'easter'; ep.sign = easterM[1] ?? '+'; ep.offset = parseInt(easterM[2] ?? '0', 10)
    return
  }
  const adventM = exprUp.match(/^ADVENT([+-])?(\d+)?$/)
  if (adventM) {
    ep.type = 'advent'; ep.sign = adventM[1] ?? '+'; ep.offset = parseInt(adventM[2] ?? '0', 10)
    return
  }
  if (exprUp.startsWith('HOLIDAY:')) {
    const remainder = expr.slice(8)
    const offsetM = remainder.match(/([+-])(\d+)$/)
    ep.type = 'holiday_name'
    if (offsetM) {
      ep.name = remainder.slice(0, offsetM.index).trim()
      ep.sign = offsetM[1]; ep.offset = parseInt(offsetM[2], 10)
    } else {
      ep.name = remainder.trim(); ep.sign = '+'; ep.offset = 0
    }
  }
}

function describeWinEp(ep) {
  switch (ep.type) {
    case 'fixed': {
      const mon = WIN_MONTHS.find(m => m.v === ep.month)?.l ?? String(ep.month)
      return `${ep.day}. ${mon}`
    }
    case 'easter':
      return ep.offset === 0 ? t('adapters.bindingForm.ztEasterSunday') : t('adapters.bindingForm.ztEasterOffset', { sign: ep.sign, n: ep.offset })
    case 'advent': {
      const presets = {
        '0': t('adapters.bindingForm.ztAdvent1'),
        '7': t('adapters.bindingForm.ztAdvent2'),
        '14': t('adapters.bindingForm.ztAdvent3'),
        '21': t('adapters.bindingForm.ztAdvent4'),
        '24': t('adapters.bindingForm.ztChristmasEve'),
      }
      if (ep.sign === '+' && presets[String(ep.offset)]) return presets[String(ep.offset)]
      return ep.offset === 0 ? t('adapters.bindingForm.ztAdvent1') : t('adapters.bindingForm.ztAdventOffset', { sign: ep.sign, n: ep.offset })
    }
    case 'holiday_name':
      if (!ep.name) return t('adapters.bindingForm.notSet')
      return ep.offset === 0 ? ep.name : t('adapters.bindingForm.ztHolidayOffset', { name: ep.name, sign: ep.sign, n: ep.offset })
    default:
      return t('adapters.bindingForm.notSet')
  }
}

async function onWinTypeChange(ep) {
  if (ep.type === 'holiday_name' && selectedInstanceId.value) await loadZsuHolidays()
}

function onAnwOffsetSelectChange() {
  if (anwOffsetSelect.value === '') {
    cfg.offset_override = null
  } else if (anwOffsetSelect.value !== 'custom') {
    cfg.offset_override = parseInt(anwOffsetSelect.value)
  }
}

function onAnwOffsetCustomInput() {
  if (cfg.offset_override != null) {
    cfg.offset_override = Math.min(30, Math.max(1, cfg.offset_override || 1))
  }
}

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
    mqttXmlParseError.value = t('adapters.bindingForm.errors.invalidXml', { msg: parseErr.textContent.split('\n')[0].trim() })
    return
  }
  mqttXmlElements.value = collectXmlLeafPaths(doc.documentElement, '')
  if (mqttXmlElements.value.length === 0)
    mqttXmlParseError.value = t('adapters.bindingForm.errors.noChildElementsFound')
}

// Flatten all leaf paths from a JSON object/array to dot-notation (max depth 6)
function _flattenJsonLeaves(obj, prefix = '', depth = 0) {
  if (depth > 6 || obj === null || typeof obj !== 'object') {
    return prefix ? [{ key: prefix, text: obj === null ? 'null' : String(obj) }] : []
  }
  const paths = []
  if (Array.isArray(obj)) {
    obj.forEach((item, i) => {
      const key = `${prefix}[${i}]`
      paths.push(..._flattenJsonLeaves(item, key, depth + 1))
    })
  } else {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k
      if (v !== null && typeof v === 'object') {
        paths.push(..._flattenJsonLeaves(v, key, depth + 1))
      } else {
        paths.push({ key, text: v === null ? 'null' : String(v) })
      }
    }
  }
  return paths
}

function onMqttJsonSampleInput() {
  mqttJsonParseError.value = null
  mqttJsonKeys.value = []
  const s = mqttJsonSample.value.trim()
  if (!s) return
  try {
    const obj = JSON.parse(s)
    if (obj !== null && typeof obj === 'object') {
      mqttJsonKeys.value = _flattenJsonLeaves(obj)
    } else {
      mqttJsonParseError.value = t('adapters.bindingForm.errors.sampleMustBeJsonObjectOrArray')
    }
  } catch (e) {
    mqttJsonParseError.value = t('adapters.bindingForm.errors.invalidJson', { msg: e.message })
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
    return c
  }
  if (type === 'ONEWIRE') {
    return { sensor_id: cfg.sensor_id, sensor_type: cfg.sensor_type || 'DS18B20' }
  }
  if (type === 'HOME_ASSISTANT') {
    const c = { entity_id: cfg.entity_id }
    if (cfg.attribute?.trim())        c.attribute        = cfg.attribute.trim()
    if (cfg.service_domain?.trim())   c.service_domain   = cfg.service_domain.trim()
    if (cfg.service_name?.trim())     c.service_name     = cfg.service_name.trim()
    if (cfg.service_data_key?.trim()) c.service_data_key = cfg.service_data_key.trim()
    return c
  }
  if (type === 'IOBROKER') {
    const c = { state_id: cfg.state_id }
    if (cfg.command_state_id?.trim()) c.command_state_id = cfg.command_state_id.trim()
    if (form.direction === 'DEST' || form.direction === 'BOTH') c.ack = !!cfg.ack
    if ((form.direction === 'SOURCE' || form.direction === 'BOTH') && cfg.source_data_type)
      c.source_data_type = cfg.source_data_type
    if (cfg.source_data_type === 'json' && cfg.json_key?.trim())
      c.json_key = cfg.json_key.trim()
    return c
  }
  if (type === 'ZEITSCHALTUHR') {
    const c = {
      timer_type:   cfg.timer_type,
      meta_type:    cfg.meta_type,
      weekdays:     [...cfg.weekdays],
      holiday_mode: cfg.holiday_mode,
      vacation_mode: cfg.vacation_mode,
    }
    if (cfg.timer_type === 'holiday') {
      c.selected_holidays = [...(cfg.selected_holidays ?? [])]
    }
    if (cfg.timer_type === 'annual') {
      c.months        = [...cfg.months]
      c.day_of_month  = cfg.day_of_month ?? 0
    }
    if (cfg.timer_type !== 'meta') {
      c.time_ref       = cfg.time_ref
      c.minute         = cfg.minute ?? 0
      c.every_hour     = cfg.every_hour
      c.every_minute   = cfg.every_minute
      c.value          = cfg.value || '1'
      if (cfg.time_ref === 'absolute') {
        c.hour = cfg.hour ?? 0
      } else {
        c.offset_minutes = cfg.offset_minutes ?? 0
      }
      if (cfg.time_ref === 'solar_altitude') {
        c.solar_altitude_deg = cfg.solar_altitude_deg ?? 0.0
        c.sun_direction      = cfg.sun_direction || 'rising'
      }
      if (cfg.date_window_enabled) {
        c.date_window_enabled = true
        c.date_window_from    = buildWinExpr(winFrom)
        c.date_window_to      = buildWinExpr(winTo)
      }
    }
    return c
  }
  if (type === 'ANWESENHEITSSIMULATION') {
    const c = {}
    if (cfg.offset_override != null) c.offset_override = cfg.offset_override
    if (cfg.on_presence_override != null) {
      c.on_presence_override = cfg.on_presence_override
      if (cfg.on_presence_override === 'setzen' && cfg.on_presence_value?.trim())
        c.on_presence_value = cfg.on_presence_value.trim()
    }
    return c
  }
  if (type === 'SNMP') {
    const c = {
      host: cfg.host || '192.168.1.1',
      oid:  cfg.oid  || '1.3.6.1.2.1.1.1.0',
    }
    if (cfg.port && cfg.port !== 161)            c.port        = cfg.port
    if (cfg.data_type && cfg.data_type !== 'auto') c.data_type = cfg.data_type
    if (cfg.poll_interval)                         c.poll_interval = cfg.poll_interval
    if (cfg.timeout && cfg.timeout !== 5.0)        c.timeout    = cfg.timeout
    if (cfg.retries !== undefined && cfg.retries !== 1) c.retries = cfg.retries
    return c
  }
  if (type === 'MESSAGE') {
    const c = {
      operator: cfg.operator || '==',
      compare_value: cfg.compare_value,
      message: cfg.message || '###DPN###: ###DP### ###DPU###',
      providers: [...(cfg.providers ?? [])],
      send_on_change: cfg.send_on_change ?? true,
      cooldown_seconds: cfg.cooldown_seconds ?? 0,
    }
    if (cfg.title?.trim()) c.title = cfg.title.trim()
    if (cfg.priority != null) c.priority = cfg.priority
    return c
  }
  return {}
}

async function submit() {
  error.value  = null
  saving.value = true
  try {
    const config     = buildConfig()
    const effectiveDirection = ['ANWESENHEITSSIMULATION', 'MESSAGE'].includes(selectedAdapterType.value) ? 'SOURCE' : form.direction
    const throttleMs = form.throttle_value > 0
      ? Math.round(form.throttle_value * THROTTLE_FACTORS[form.throttle_unit]) : null
    let resolvedValueMap = null
    if (form.value_map_preset === 'custom') {
      try { resolvedValueMap = JSON.parse(form.value_map_custom) } catch { /* invalid JSON — ignore */ }
    } else if (form.value_map_preset) {
      resolvedValueMap = VALUE_MAP_PRESETS.find(p => p.key === form.value_map_preset)?.map ?? null
    }
    const filterPayload = {
      value_formula:      form.value_formula?.trim() || null,
      value_map:          resolvedValueMap,
      send_throttle_ms:   throttleMs,
      send_on_change:     form.send_on_change,
      send_min_delta:     (form.send_min_delta ?? 0) > 0     ? form.send_min_delta     : null,
      send_min_delta_pct: (form.send_min_delta_pct ?? 0) > 0 ? form.send_min_delta_pct : null,
    }
    if (props.initial) {
      await dpApi.updateBinding(props.dpId, props.initial.id, {
        direction: effectiveDirection, config, enabled: form.enabled, ...filterPayload,
      })
    } else {
      if (!form.adapter_instance_id) {
        error.value = t('adapters.bindingForm.errors.selectAdapterInstance'); saving.value = false; return
      }
      await dpApi.createBinding(props.dpId, {
        adapter_instance_id: form.adapter_instance_id,
        direction: effectiveDirection, config, enabled: form.enabled, ...filterPayload,
      })
    }
    emit('save')
  } catch (e) {
    error.value = e.response?.data?.detail ?? t('common.saveError')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
@reference "tailwindcss";
.tab-btn {
  @apply flex items-center px-4 py-2 text-sm text-slate-500 dark:text-slate-400 border-b-2 border-transparent
         hover:text-slate-700 dark:hover:text-slate-200 hover:border-slate-400 dark:hover:border-slate-500 transition-colors cursor-pointer;
}
.tab-active {
  @apply text-blue-500 dark:text-blue-400 border-blue-500 dark:border-blue-400 font-medium;
}
</style>

<style>
@reference "tailwindcss";
.section-header {
  @apply text-xs font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400 border-b border-slate-200 dark:border-slate-700 pb-1;
}
.optional-divider {
  @apply text-xs text-slate-500 border-b border-slate-200/80 dark:border-slate-700/50 pb-1 mt-1;
}
.optional { @apply text-slate-500 font-normal text-xs ml-1; }
.hint     { @apply text-xs text-slate-500 mt-0.5; }
</style>
