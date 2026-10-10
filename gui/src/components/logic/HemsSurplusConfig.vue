<template>
  <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4" data-testid="hems-config">
    <p class="text-xs text-slate-500">{{ description }}</p>

    <!-- ── Netzanschluss & Regelung ───────────────────────────────────── -->
    <div class="flex flex-col gap-3">
      <span class="section-label">{{ $t('logic.nodeConfig.hemsSurplus.gridSection') }}</span>

      <div class="form-group">
        <label class="label">{{ $t('logic.nodeConfig.hemsSurplus.gridMode') }}</label>
        <select
          :value="gridMode"
          class="input text-xs"
          data-testid="hems-grid-mode"
          @change="setGlobal('grid_mode', $event.target.value)"
        >
          <option v-for="m in GRID_MODES" :key="m" :value="m">{{ $t('logic.nodeConfig.hemsSurplus.gridModeOptions.' + m) }}</option>
        </select>
        <p class="text-xs text-slate-500 mt-1" data-testid="hems-grid-mode-hint">{{ $t('logic.nodeConfig.hemsSurplus.gridModeHints.' + gridMode) }}</p>
        <p v-if="gridMode === 'feed_in_only'" class="text-xs text-amber-500 mt-1" data-testid="hems-feed-in-warning">
          {{ $t('logic.nodeConfig.hemsSurplus.feedInOnlyWarning') }}
        </p>
      </div>

      <div v-for="f in GLOBAL_FIELDS" :key="f.key" class="form-group">
        <label class="label">{{ $t('logic.nodeConfig.hemsSurplus.global.' + f.key) }}</label>
        <input
          v-if="f.type === 'number'"
          type="number"
          :min="f.min" :max="f.max" step="any"
          :value="data[f.key] ?? f.default"
          class="input text-xs"
          :data-testid="'hems-global-' + f.key"
          @change="setGlobal(f.key, toNumber($event.target.value, f.default, f.min, f.max))"
        />
        <select
          v-else-if="f.type === 'select'"
          :value="data[f.key] ?? f.default"
          class="input text-xs"
          :data-testid="'hems-global-' + f.key"
          @change="setGlobal(f.key, $event.target.value)"
        >
          <option v-for="o in f.options" :key="o" :value="o">{{ $t('logic.nodeConfig.hemsSurplus.global.' + f.key + 'Options.' + o) }}</option>
        </select>
        <label v-else class="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            :checked="data[f.key] !== false && data[f.key] !== 'false'"
            :data-testid="'hems-global-' + f.key"
            @change="setGlobal(f.key, $event.target.checked)"
          />
          <span class="text-xs text-slate-600 dark:text-slate-300">{{ $t('logic.nodeConfig.hemsSurplus.global.' + f.key + 'Hint') }}</span>
        </label>
        <p v-if="f.hint" class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.hemsSurplus.global.' + f.key + 'Hint') }}</p>
      </div>
    </div>

    <!-- ── Verbraucher ────────────────────────────────────────────────── -->
    <div class="flex flex-col gap-3">
      <div class="flex items-center justify-between">
        <span class="section-label">{{ $t('logic.nodeConfig.hemsSurplus.consumers') }}</span>
        <button class="btn-secondary btn-sm text-teal-400" data-testid="hems-consumer-add" @click="addConsumer">
          {{ $t('logic.nodeConfig.hemsSurplus.add') }}
        </button>
      </div>
      <p class="text-xs text-slate-500">{{ $t('logic.nodeConfig.hemsSurplus.priorityHint') }}</p>
      <p v-if="!consumers.length" class="text-xs text-slate-500" data-testid="hems-consumers-empty">
        {{ $t('logic.nodeConfig.hemsSurplus.noConsumers') }}
      </p>

      <VueDraggable
        v-model="consumers"
        :animation="150"
        handle=".hems-drag-handle"
        class="flex flex-col gap-3"
        data-testid="hems-consumer-list"
      >
        <div
          v-for="(c, i) in consumers"
          :key="c.id"
          class="rule-row"
          :data-testid="'hems-consumer-' + i"
        >
          <div class="flex items-center gap-2">
            <span
              class="hems-drag-handle cursor-grab text-slate-400 select-none"
              :title="$t('logic.nodeConfig.hemsSurplus.dragHint')"
              :data-testid="'hems-consumer-handle-' + i"
            >⠿</span>
            <span class="text-xs font-mono text-slate-400 w-5 shrink-0" :data-testid="'hems-consumer-position-' + i">{{ i + 1 }}</span>
            <input
              :value="c.name"
              class="input text-xs flex-1"
              :placeholder="$t('logic.nodeConfig.hemsSurplus.namePlaceholder')"
              :data-testid="'hems-consumer-name-' + i"
              @input="setConsumer(i, 'name', $event.target.value)"
            />
            <button
              class="text-xs text-red-400 hover:text-red-300 shrink-0"
              :title="$t('logic.nodeConfig.hemsSurplus.remove')"
              :data-testid="'hems-consumer-remove-' + i"
              @click="removeConsumer(i)"
            >{{ $t('logic.nodeConfig.hemsSurplus.removeShort') }}</button>
          </div>

          <label class="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              :checked="c.active !== false"
              :data-testid="'hems-consumer-active-' + i"
              @change="setConsumer(i, 'active', $event.target.checked)"
            />
            <span class="text-xs text-slate-600 dark:text-slate-300">{{ $t('logic.nodeConfig.hemsSurplus.fields.active') }}</span>
          </label>

          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.hemsSurplus.fields.mode') }}</label>
            <select
              :value="c.mode"
              class="input text-xs"
              :data-testid="'hems-consumer-mode-' + i"
              @change="setConsumer(i, 'mode', $event.target.value)"
            >
              <option v-for="m in CONSUMER_MODES" :key="m" :value="m">{{ $t('logic.nodeConfig.hemsSurplus.fields.modeOptions.' + m) }}</option>
            </select>
          </div>

          <div v-for="f in fieldsFor(c)" :key="f.key" class="form-group">
            <label class="label">{{ $t(fieldLabelKey(c, f)) }}</label>
            <select
              v-if="f.type === 'select'"
              :value="c[f.key] ?? f.default"
              class="input text-xs"
              :data-testid="'hems-consumer-' + f.key + '-' + i"
              @change="setConsumer(i, f.key, $event.target.value)"
            >
              <option v-for="o in f.options" :key="o" :value="o">{{ $t('logic.nodeConfig.hemsSurplus.fields.' + f.key + 'Options.' + o) }}</option>
            </select>
            <input
              v-else
              type="number"
              :min="f.min" :max="f.max" step="any"
              :value="c[f.key] ?? (f.optional ? '' : f.default)"
              :placeholder="f.optional ? $t('logic.nodeConfig.hemsSurplus.fields.' + f.key + 'Placeholder') : undefined"
              class="input text-xs"
              :data-testid="'hems-consumer-' + f.key + '-' + i"
              @change="setConsumerNumber(i, f, $event.target.value)"
            />
          </div>
        </div>
      </VueDraggable>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { VueDraggable } from 'vue-draggable-plus'

// Editor of the hems_surplus block (HEMS Lite, issue #1327). The consumer list
// order *is* the priority: it is edited by drag-and-drop only, the position
// number is display-only, and every consumer keeps a stable `id` — the id (not
// the position) names its ports, so reordering never re-wires an edge.
const props = defineProps({
  data:        { type: Object, required: true },
  description: { type: String, default: '' },
})
const emit = defineEmits(['update'])

const GRID_MODES = ['bidirectional', 'split', 'feed_in_only']
const CONSUMER_MODES = ['percent', 'onoff', 'trigger']

const GLOBAL_FIELDS = [
  { key: 'interval_s',        type: 'number', min: 5, max: 3600, default: 30 },
  { key: 'target_w',          type: 'number', default: 0 },
  { key: 'on_delay_s',        type: 'number', min: 0, default: 60 },
  { key: 'off_delay_s',       type: 'number', min: 0, default: 180 },
  { key: 'max_age_s',         type: 'number', min: 0, default: 0, hint: true },
  { key: 'invalid_behavior',  type: 'select', default: 'off', options: ['off', 'hold'] },
  { key: 'only_on_change',    type: 'boolean', default: true },
]

const POWER_FIELDS = [
  { key: 'power_source', type: 'select', default: 'fixed', options: ['fixed', 'input'] },
]

const FIELDS = {
  percent: [
    ...POWER_FIELDS,
    { key: 'power_w', type: 'number', min: 0, default: 1000, needsFixedPower: true },
    { key: 'min_power_w', type: 'number', min: 0, default: 0 },
    { key: 'min_setpoint', type: 'number', min: 0, max: 100, default: 0 },
    { key: 'max_setpoint', type: 'number', min: 0, max: 100, default: 100 },
    { key: 'step', type: 'number', min: 0.1, max: 100, default: 1 },
    { key: 'below_min', type: 'select', default: 'off', options: ['off', 'hold_min'] },
    { key: 'min_runtime_s', type: 'number', min: 0, default: 0 },
    { key: 'min_off_s', type: 'number', min: 0, default: 0 },
    { key: 'on_delay_s', type: 'number', min: 0, optional: true },
    { key: 'off_delay_s', type: 'number', min: 0, optional: true },
  ],
  onoff: [
    ...POWER_FIELDS,
    { key: 'power_w', type: 'number', min: 0, default: 1000, needsFixedPower: true },
    { key: 'on_threshold_w', type: 'number', min: 0, optional: true },
    { key: 'off_threshold_w', type: 'number', min: 0, optional: true },
    { key: 'min_runtime_s', type: 'number', min: 0, default: 0 },
    { key: 'min_off_s', type: 'number', min: 0, default: 0 },
    { key: 'on_delay_s', type: 'number', min: 0, optional: true },
    { key: 'off_delay_s', type: 'number', min: 0, optional: true },
  ],
  trigger: [
    { key: 'min_surplus_w', type: 'number', min: 0, default: 1000 },
    { key: 'on_delay_s', type: 'number', min: 0, optional: true },
    { key: 'pulse_s', type: 'number', min: 0.1, default: 1 },
    { key: 'lockout_s', type: 'number', min: 0, default: 3600 },
    { key: 'rearm', type: 'select', default: 'after_drop', options: ['after_lockout', 'after_drop', 'reset_input'] },
    { key: 'reserve_w', type: 'number', min: 0, default: 0 },
  ],
}

const gridMode = computed(() => (GRID_MODES.includes(props.data.grid_mode) ? props.data.grid_mode : 'bidirectional'))

function parseConsumers(raw) {
  let list = raw
  if (typeof raw === 'string') {
    try { list = JSON.parse(raw || '[]') } catch (_) { list = [] }
  }
  return Array.isArray(list) ? list.filter(c => c && typeof c === 'object' && !Array.isArray(c)) : []
}

const consumers = computed({
  get: () => parseConsumers(props.data.consumers),
  set: rows => saveConsumers(rows),
})

function saveConsumers(rows) {
  emit('update', { ...props.data, consumers: JSON.stringify(rows) })
}

function setGlobal(key, value) {
  emit('update', { ...props.data, [key]: value })
}

function toNumber(raw, fallback, min, max) {
  let n = Number(raw)
  if (raw === '' || !Number.isFinite(n)) n = fallback
  if (min !== undefined && n < min) n = min
  if (max !== undefined && n > max) n = max
  return n
}

function newId() {
  const taken = new Set(consumers.value.map(c => c.id))
  let id
  do { id = Math.random().toString(36).slice(2, 8) } while (taken.has(id) || !/^[a-z]/.test(id))
  return id
}

function addConsumer() {
  // New consumers go to the end of the list = lowest priority.
  saveConsumers([...consumers.value, { id: newId(), name: '', active: true, mode: 'percent', power_w: 1000 }])
}

function removeConsumer(i) {
  saveConsumers(consumers.value.filter((_, idx) => idx !== i))
}

function setConsumer(i, key, value) {
  saveConsumers(consumers.value.map((c, idx) => (idx === i ? { ...c, [key]: value } : c)))
}

function setConsumerNumber(i, field, raw) {
  // An emptied optional field falls back to the global value (null).
  if (raw === '') {
    setConsumer(i, field.key, field.optional ? null : field.default)
    return
  }
  setConsumer(i, field.key, toNumber(raw, field.default ?? 0, field.min, field.max))
}

function fieldsFor(c) {
  const fields = FIELDS[c.mode] ?? FIELDS.percent
  return fields.filter(f => !(f.needsFixedPower && c.power_source === 'input'))
}

function fieldLabelKey(c, f) {
  // The trigger reuses the on-delay as "surplus must hold for" and the
  // rated power as "maximum"/"nominal" depending on the control type.
  if (c.mode === 'trigger' && f.key === 'on_delay_s') return 'logic.nodeConfig.hemsSurplus.fields.on_delay_s_trigger'
  if (c.mode === 'onoff' && f.key === 'power_w') return 'logic.nodeConfig.hemsSurplus.fields.power_w_onoff'
  return 'logic.nodeConfig.hemsSurplus.fields.' + f.key
}
</script>
