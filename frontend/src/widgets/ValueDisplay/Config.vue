<script setup lang="ts">
import { reactive, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconPicker from '@/components/IconPicker.vue'
import DataPointPicker from '@/components/DataPointPicker.vue'
import { TIME_RANGE_PRESETS, DEFAULT_TIME_RANGE } from '@/widgets/Chart/timeRangePresets'

type CondFn = 'eq' | 'lt' | 'lte' | 'gt' | 'gte'
type DisplayMode = 'value' | 'history' | 'icon_only' | 'gauge_arc' | 'gauge_circle'

interface Rule {
  fn: CondFn | 'default'
  threshold: string
  icon: string
  color: string
  output_type: 'value' | 'text'
  calculation: string
  prefix: string
  text: string
  decimals: number
  postfix: string
}

interface Cfg {
  label: string
  mode: DisplayMode
  rules: Rule[]
  history_time_range: string
  secondary_dp_id: string
  secondary_label: string
  secondary_decimals: number
  gauge_min: number
  gauge_max: number
  gauge_colors: string[]
}

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

// ── Constants ─────────────────────────────────────────────────────────────────








const { t } = useI18n()

const MODES = computed(() => [
  { value: 'value'        as DisplayMode, label: t('widgets.valuedisplay.modeValueIcon') },
  { value: 'history'      as DisplayMode, label: t('widgets.valuedisplay.modeValueHistory') },
  { value: 'icon_only'    as DisplayMode, label: t('widgets.valuedisplay.modeIconOnly') },
  { value: 'gauge_arc'    as DisplayMode, label: t('widgets.valuedisplay.modeGaugeArc') },
  { value: 'gauge_circle' as DisplayMode, label: t('widgets.valuedisplay.modeGaugeCircle') },
])

function isGaugeMode(m: DisplayMode): boolean {
  return m === 'gauge_arc' || m === 'gauge_circle'
}

const FN_OPTIONS = computed(() => [
  { value: 'eq'  as CondFn, label: t('widgets.valuedisplay.fnEq') },
  { value: 'lt'  as CondFn, label: t('widgets.valuedisplay.fnLt') },
  { value: 'lte' as CondFn, label: t('widgets.valuedisplay.fnLte') },
  { value: 'gt'  as CondFn, label: t('widgets.valuedisplay.fnGt') },
  { value: 'gte' as CondFn, label: t('widgets.valuedisplay.fnGte') },
])

const CALC_OPTIONS = computed(() => [
  { value: '',       label: t('widgets.valuedisplay.calcNone') },
  { value: '* 1000', label: t('widgets.valuedisplay.calcMul1000') },
  { value: '/ 1000', label: t('widgets.valuedisplay.calcDiv1000') },
  { value: '* 100',  label: t('widgets.valuedisplay.calcMul100') },
  { value: '/ 100',  label: t('widgets.valuedisplay.calcDiv100') },
  { value: '* 10',   label: t('widgets.valuedisplay.calcMul10') },
  { value: '/ 10',   label: t('widgets.valuedisplay.calcDiv10') },
])

const DECIMAL_OPTIONS = computed(() => [
  { value: 0, label: t('widgets.valuedisplay.dec0') },
  { value: 1, label: t('widgets.valuedisplay.dec1') },
  { value: 2, label: t('widgets.valuedisplay.dec2') },
  { value: 3, label: t('widgets.valuedisplay.dec3') },
])

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeRule(src?: Partial<Rule>): Rule {
  return {
    fn:          src?.fn          ?? 'eq',
    threshold:   src?.threshold   ?? '0',
    icon:        src?.icon        ?? '❓',
    color:       src?.color       ?? '#6b7280',
    output_type: src?.output_type ?? 'value',
    calculation: src?.calculation ?? '',
    prefix:      src?.prefix      ?? '',
    text:        src?.text        ?? '',
    decimals:    src?.decimals    ?? 1,
    postfix:     src?.postfix     ?? '',
  }
}

function makeDefaultRule(src?: Partial<Rule>): Rule {
  return makeRule({ fn: 'default', threshold: '', ...src })
}

function normalizeRules(raw: Partial<Rule>[] | undefined): Rule[] {
  const list = (raw ?? []).map(r => makeRule(r))
  const nonDefault = list.filter(r => r.fn !== 'default')
  const def = list.find(r => r.fn === 'default') ?? makeDefaultRule()
  return [...nonDefault, def]
}

// ── Reactive config ───────────────────────────────────────────────────────────

function resolveHistoryTimeRange(raw: Record<string, unknown>): string {
  if (raw.history_time_range && typeof raw.history_time_range === 'string') return raw.history_time_range as string
  return DEFAULT_TIME_RANGE
}

const cfg = reactive<Cfg>({
  label:              (props.modelValue.label              as string)      ?? '',
  mode:               (props.modelValue.mode               as DisplayMode) ?? 'value',
  rules:              normalizeRules(props.modelValue.rules as Partial<Rule>[] | undefined),
  history_time_range: resolveHistoryTimeRange(props.modelValue),
  secondary_dp_id:    (props.modelValue.secondary_dp_id    as string)      ?? '',
  secondary_label:    (props.modelValue.secondary_label    as string)      ?? '',
  secondary_decimals: (props.modelValue.secondary_decimals as number)      ?? 1,
  gauge_min:          (props.modelValue.gauge_min          as number)      ?? 0,
  gauge_max:          (props.modelValue.gauge_max          as number)      ?? 100,
  gauge_colors:       (props.modelValue.gauge_colors       as string[])    ?? ['#22c55e', '#f59e0b', '#ef4444'],
})

watch(cfg, () => {
  emit('update:modelValue', {
    label:               cfg.label,
    mode:                cfg.mode,
    rules:               cfg.rules,
    history_time_range:  cfg.mode === 'history' ? cfg.history_time_range : undefined,
    secondary_dp_id:     cfg.mode === 'value' && cfg.secondary_dp_id ? cfg.secondary_dp_id : undefined,
    secondary_label:     cfg.mode === 'value' && cfg.secondary_dp_id ? cfg.secondary_label : undefined,
    secondary_decimals:  cfg.mode === 'value' && cfg.secondary_dp_id ? cfg.secondary_decimals : undefined,
    gauge_min:           isGaugeMode(cfg.mode) ? cfg.gauge_min    : undefined,
    gauge_max:           isGaugeMode(cfg.mode) ? cfg.gauge_max    : undefined,
    gauge_colors:        isGaugeMode(cfg.mode) ? cfg.gauge_colors : undefined,
  })
}, { deep: true })

// ── Rule list actions ─────────────────────────────────────────────────────────

function addRule() {
  const defIdx = cfg.rules.findIndex(r => r.fn === 'default')
  cfg.rules.splice(defIdx, 0, makeRule())
}

function removeRule(i: number) {
  cfg.rules.splice(i, 1)
}

function dupRule(i: number) {
  const defIdx = cfg.rules.findIndex(r => r.fn === 'default')
  cfg.rules.splice(defIdx, 0, { ...cfg.rules[i] })
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Label -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Raumtemperatur"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Mode -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('common.mode') }}</label>
      <div class="flex flex-wrap gap-1">
        <button
          v-for="m in MODES"
          :key="m.value"
          type="button"
          :class="[
            'flex-1 min-w-[6rem] py-1.5 text-xs rounded border',
            cfg.mode === m.value
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.mode = m.value"
        >{{ m.label }}</button>
      </div>
    </div>

    <!-- ── Rules table ─────────────────────────────────────────────────────── -->
    <div>
      <div class="flex items-center justify-between mb-1">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">{{ $t('widgets.valuedisplay.rules') }}</p>
      </div>

      <div class="space-y-1">
        <div
          v-for="(rule, i) in cfg.rules"
          :key="i"
          class="border rounded overflow-hidden"
          :class="rule.fn === 'default' ? 'border-gray-700 bg-gray-800/40' : 'border-gray-700'"
        >
          <!-- ── Default rule ─────────────────────────────────────────────── -->
          <template v-if="rule.fn === 'default'">
            <div class="px-2 pt-2 pb-1">
              <p class="text-xs font-semibold text-gray-400">{{ $t('widgets.valuedisplay.defaultRule') }}</p>
              <p class="text-xs text-gray-600 mb-2">{{ $t('widgets.valuedisplay.defaultRuleHint') }}</p>

              <!-- Icon + color row -->
              <div class="flex gap-2 items-center mb-2">
                <span class="text-xs text-gray-500 w-8 shrink-0">Icon</span>
                <IconPicker v-model="rule.icon" :dark="true" />
                <input
                  v-model="rule.color"
                  type="color"
                  class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
                  title="Farbe"
                />
              </div>

              <!-- Display (hidden in icon_only) -->
              <template v-if="cfg.mode !== 'icon_only'">
                <div class="flex flex-wrap gap-1 items-center">
                  <select
                    v-model="rule.output_type"
                    class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                  >
                    <option value="value">{{ $t('widgets.valuedisplay.outputValue') }}</option>
                    <option value="text">{{ $t('widgets.valuedisplay.outputText') }}</option>
                  </select>

                  <template v-if="rule.output_type === 'value'">
                    <select
                      v-model="rule.calculation"
                      class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                    >
                      <option v-for="c in CALC_OPTIONS" :key="c.value" :value="c.value">{{ c.label }}</option>
                    </select>
                    <input
                      v-model="rule.prefix"
                      type="text"
                      placeholder="Präfix"
                      class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                    <select
                      v-model.number="rule.decimals"
                      class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                    >
                      <option v-for="d in DECIMAL_OPTIONS" :key="d.value" :value="d.value">{{ d.label }}</option>
                    </select>
                    <input
                      v-model="rule.postfix"
                      type="text"
                      placeholder="Einheit / Postfix"
                      class="w-20 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                  </template>

                  <template v-else>
                    <input
                      v-model="rule.prefix"
                      type="text"
                      placeholder="Präfix"
                      class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                    <input
                      v-model="rule.text"
                      type="text"
                      placeholder="Anzeigetext"
                      class="flex-1 min-w-20 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                    <input
                      v-model="rule.postfix"
                      type="text"
                      placeholder="Postfix"
                      class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                    />
                  </template>
                </div>
              </template>
            </div>
          </template>

          <!-- ── Regular rule ────────────────────────────────────────────── -->
          <template v-else>
            <!-- Row 1: condition + icon + color + actions -->
            <div class="flex gap-1 items-center px-2 pt-2 pb-1">
              <select
                v-model="rule.fn"
                class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500 min-w-0"
              >
                <option v-for="opt in FN_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
              <input
                v-model="rule.threshold"
                type="text"
                placeholder="Wert"
                class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500 font-mono shrink-0"
              />
              <div class="flex-1" />
              <IconPicker v-model="rule.icon" :dark="true" />
              <input
                v-model="rule.color"
                type="color"
                class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
                title="Farbe"
              />
              <button
                type="button"
                class="text-gray-500 hover:text-blue-400 px-0.5 shrink-0"
                :title="$t('widgets.valuedisplay.duplicateTitle')"
                @click="dupRule(i)"
              >⧉</button>
              <button
                type="button"
                class="text-gray-500 hover:text-red-400 px-0.5 shrink-0"
                :title="$t('widgets.valuedisplay.deleteTitle')"
                @click="removeRule(i)"
              >🗑</button>
            </div>

            <!-- Row 2: display config (hidden in icon_only) -->
            <template v-if="cfg.mode !== 'icon_only'">
              <div class="flex flex-wrap gap-1 items-center px-2 pb-2 border-t border-gray-700/50 pt-1.5">
                <select
                  v-model="rule.output_type"
                  class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                >
                  <option value="value">Wert</option>
                  <option value="text">Text</option>
                </select>

                <template v-if="rule.output_type === 'value'">
                  <select
                    v-model="rule.calculation"
                    class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                  >
                    <option v-for="c in CALC_OPTIONS" :key="c.value" :value="c.value">{{ c.label }}</option>
                  </select>
                  <input
                    v-model="rule.prefix"
                    type="text"
                    placeholder="Präfix"
                    class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                  <select
                    v-model.number="rule.decimals"
                    class="bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-200 focus:outline-none focus:border-blue-500"
                  >
                    <option v-for="d in DECIMAL_OPTIONS" :key="d.value" :value="d.value">{{ d.label }}</option>
                  </select>
                  <input
                    v-model="rule.postfix"
                    type="text"
                    placeholder="Einheit / Postfix"
                    class="w-20 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </template>

                <template v-else>
                  <input
                    v-model="rule.prefix"
                    type="text"
                    placeholder="Präfix"
                    class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                  <input
                    v-model="rule.text"
                    type="text"
                    placeholder="Anzeigetext"
                    class="flex-1 min-w-20 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                  <input
                    v-model="rule.postfix"
                    type="text"
                    placeholder="Postfix"
                    class="w-14 bg-gray-800 border border-gray-700 rounded px-1 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </template>
              </div>
            </template>
          </template>
        </div>
      </div>

      <button
        type="button"
        class="mt-2 text-xs text-blue-400 hover:text-blue-300"
        @click="addRule"
      >{{ $t('widgets.valuedisplay.addRule') }}</button>
    </div>

    <!-- ── Secondary value (value mode only) ─────────────────────────────── -->
    <div v-if="cfg.mode === 'value'" class="border-t border-gray-700 pt-3 space-y-2">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">{{ $t('widgets.valuedisplay.secondValue') }}</p>
      <DataPointPicker
        :model-value="cfg.secondary_dp_id || null"
        :compatible-types="['FLOAT', 'INTEGER', 'BOOLEAN', 'STRING']"
        @update:model-value="id => (cfg.secondary_dp_id = id ?? '')"
      />
      <template v-if="cfg.secondary_dp_id">
        <div class="flex gap-2">
          <div class="flex-1">
            <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.valuedisplay.secondaryLabel') }}</label>
            <input
              v-model="cfg.secondary_label"
              type="text"
              placeholder="z.B. Soll"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div class="w-20">
            <label class="block text-xs text-gray-400 mb-1">Dezimalst.</label>
            <input
              v-model.number="cfg.secondary_decimals"
              type="number"
              min="0"
              max="4"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- ── History time range (history mode only) ───────────────────────── -->
    <div v-if="cfg.mode === 'history'" class="border-t border-gray-700 pt-3">
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.valuedisplay.defaultHistoryRange') }}</label>
      <select
        v-model="cfg.history_time_range"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option v-for="p in TIME_RANGE_PRESETS" :key="p.value" :value="p.value">{{ $t(p.label) }}</option>
      </select>
    </div>

    <!-- ── Gauge settings (gauge_arc / gauge_circle only) ─────────────────── -->
    <div v-if="isGaugeMode(cfg.mode)" class="border-t border-gray-700 pt-3 space-y-3">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Gauge Einstellungen</p>

      <!-- Min / Max -->
      <div class="flex gap-2">
        <div class="flex-1">
          <label class="block text-xs text-gray-400 mb-1">Minimum</label>
          <input
            v-model.number="cfg.gauge_min"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div class="flex-1">
          <label class="block text-xs text-gray-400 mb-1">Maximum</label>
          <input
            v-model.number="cfg.gauge_max"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <!-- Gradient colors -->
      <div>
        <label class="block text-xs text-gray-400 mb-1">Farbe / Farbverlauf (1–4 Farben, niedrig → hoch)</label>
        <div class="flex items-center gap-2 flex-wrap">
          <input
            v-for="(_, i) in cfg.gauge_colors"
            :key="i"
            v-model="cfg.gauge_colors[i]"
            type="color"
            class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            :title="`Farbe ${i + 1}`"
          />
          <button
            v-if="cfg.gauge_colors.length < 4"
            type="button"
            class="text-xs text-blue-400 hover:text-blue-300 px-2 py-1 border border-gray-700 rounded"
            @click="cfg.gauge_colors.push('#6b7280')"
          >+</button>
          <button
            v-if="cfg.gauge_colors.length > 1"
            type="button"
            class="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-gray-700 rounded"
            @click="cfg.gauge_colors.splice(cfg.gauge_colors.length - 1, 1)"
          >−</button>
        </div>
      </div>
    </div>

  </div>
</template>
