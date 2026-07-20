<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconPicker from '@/components/IconPicker.vue'

type Mode = 'sequence' | 'select-save' | 'select-direct'

interface Option {
  label: string
  value: string
  icon: string
  color: string
}

interface Cfg {
  label: string
  mode: Mode
  options: Option[]
}

const MIN_OPTIONS = 2
const MAX_OPTIONS = 10
const DEFAULT_OFF_LABEL = 'widgets.stufenschalter.defaultOffLabel'
const DEFAULT_STEP_LABEL = 'widgets.stufenschalter.defaultStepLabel'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const { t } = useI18n()

function defaultStepLabelForValue(value: unknown, index: number): string {
  return t(defaultStepLabelKeyForValue(value, index), defaultStepLabelParamsForValue(value, index))
}

function defaultStepLabelKeyForValue(value: unknown, index: number): string {
  const numericValue = Number(value)
  if (String(value ?? '') === '0') return DEFAULT_OFF_LABEL
  if (Number.isInteger(numericValue) && numericValue > 0) return DEFAULT_STEP_LABEL
  return index === 0 ? DEFAULT_OFF_LABEL : DEFAULT_STEP_LABEL
}

function defaultStepLabelParamsForValue(value: unknown, index: number): Record<string, number> {
  const numericValue = Number(value)
  if (Number.isInteger(numericValue) && numericValue > 0) return { n: numericValue }
  return { n: index + 1 }
}

function defaultStepLabelKey(index: number, value?: unknown): string {
  return index === 0 && String(value ?? '') === '0' ? DEFAULT_OFF_LABEL : DEFAULT_STEP_LABEL
}

function normalizeOptionLabel(raw: unknown, index: number, value?: unknown): string {
  const defaultKey = defaultStepLabelKey(index, value)
  if (typeof raw !== 'string') return defaultKey
  const label = raw.trim()
  if (!label || label === DEFAULT_STEP_LABEL || label === DEFAULT_OFF_LABEL || label === defaultStepLabelForValue(value, index)) {
    return defaultKey
  }
  return raw
}

function parseOptions(raw: unknown): Option[] {
  const arr = raw as Partial<Option>[] | undefined
  if (!Array.isArray(arr) || arr.length < MIN_OPTIONS) {
    return [
      { label: DEFAULT_OFF_LABEL, value: '0', icon: '', color: '#6b7280' },
      { label: DEFAULT_STEP_LABEL, value: '1', icon: '', color: '#3b82f6' },
      { label: DEFAULT_STEP_LABEL, value: '2', icon: '', color: '#10b981' },
    ]
  }
  return arr.map((option, index) => ({
    label: normalizeOptionLabel(option.label, index, option.value),
    value: String(option.value ?? ''),
    icon: option.icon ?? '',
    color: option.color ?? '#6b7280',
  }))
}

function optionInput(config: Record<string, unknown>): unknown {
  if (Array.isArray(config.steps) && config.mode !== 'select-save' && config.mode !== 'select-direct') {
    return config.steps
  }
  return config.options ?? config.steps
}

function parseMode(raw: unknown): Mode {
  if (raw === 'select-save' || raw === 'select-direct') return raw
  return 'sequence'
}

const cfg = reactive<Cfg>({
  label: (props.modelValue.label as string) ?? '',
  mode: parseMode(props.modelValue.mode),
  options: parseOptions(optionInput(props.modelValue)),
})

function serializedConfig(): Record<string, unknown> {
  return {
    label: cfg.label,
    mode: cfg.mode,
    options: cfg.options.map((option, index) => ({
      ...option,
      label: normalizeOptionLabel(option.label, index, option.value),
    })),
  }
}

watch(cfg, () => emit('update:modelValue', serializedConfig()), { deep: true })

const optionLabel = computed(() =>
  cfg.mode === 'sequence'
    ? t('widgets.stufenschalter.stepsCount', { n: cfg.options.length, max: MAX_OPTIONS })
    : t('widgets.stufenschalter.optionsCount', { n: cfg.options.length, max: MAX_OPTIONS }),
)

const hint = computed(() => {
  if (cfg.mode === 'select-save') return t('widgets.stufenschalter.hintSelectSave')
  if (cfg.mode === 'select-direct') return t('widgets.stufenschalter.hintSelectDirect')
  return t('widgets.stufenschalter.hint')
})

function displayOptionLabel(option: Option, index: number): string {
  const label = normalizeOptionLabel(option.label, index, option.value)
  return label === DEFAULT_OFF_LABEL || label === DEFAULT_STEP_LABEL ? defaultStepLabelForValue(option.value, index) : option.label
}

function updateOptionLabel(option: Option, index: number, event: Event) {
  const value = (event.target as HTMLInputElement).value
  option.label = normalizeOptionLabel(value, index, option.value)
}

function addOption() {
  if (cfg.options.length >= MAX_OPTIONS) return
  cfg.options.push({ label: DEFAULT_STEP_LABEL, value: String(cfg.options.length), icon: '', color: '#6b7280' })
}

function removeOption(i: number) {
  if (cfg.options.length <= MIN_OPTIONS) return
  cfg.options.splice(i, 1)
}

function moveUp(i: number) {
  if (i === 0) return
  ;[cfg.options[i - 1], cfg.options[i]] = [cfg.options[i], cfg.options[i - 1]]
}

function moveDown(i: number) {
  if (i === cfg.options.length - 1) return
  ;[cfg.options[i + 1], cfg.options[i]] = [cfg.options[i], cfg.options[i + 1]]
}
</script>

<template>
  <div class="space-y-4 text-sm">
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.stufenschalter.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.stufenschalter.mode') }}</label>
      <select
        v-model="cfg.mode"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="sequence">{{ $t('widgets.stufenschalter.modeSequence') }}</option>
        <option value="select-save">{{ $t('widgets.stufenschalter.modeSelectSave') }}</option>
        <option value="select-direct">{{ $t('widgets.stufenschalter.modeSelectDirect') }}</option>
      </select>
    </div>

    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          {{ optionLabel }}
        </p>
        <button
          type="button"
          :disabled="cfg.options.length >= MAX_OPTIONS"
          class="text-xs px-2 py-1 rounded border border-dashed border-gray-600 text-gray-400 hover:border-blue-500 hover:text-blue-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          @click="addOption"
        >{{ cfg.mode === 'sequence' ? $t('widgets.stufenschalter.addStep') : $t('widgets.stufenschalter.addOption') }}</button>
      </div>

      <p class="text-xs text-gray-600 mb-2">
        {{ hint }}
      </p>

      <div class="space-y-2">
        <div
          v-for="(option, i) in cfg.options"
          :key="i"
          class="border border-gray-700 rounded p-2 space-y-2"
        >
          <div class="flex items-center gap-1">
            <span class="text-xs font-semibold text-gray-500 w-4 shrink-0">{{ i + 1 }}</span>
            <div class="flex gap-0.5 ml-auto">
              <button
                type="button"
                :disabled="i === 0"
                class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs"
                :title="$t('widgets.stufenschalter.moveUp')"
                @click="moveUp(i)"
              >▲</button>
              <button
                type="button"
                :disabled="i === cfg.options.length - 1"
                class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs"
                :title="$t('widgets.stufenschalter.moveDown')"
                @click="moveDown(i)"
              >▼</button>
              <button
                type="button"
                :disabled="cfg.options.length <= MIN_OPTIONS"
                class="w-5 h-5 flex items-center justify-center rounded text-red-600 hover:text-red-400 disabled:opacity-20 text-xs"
                :title="$t('widgets.stufenschalter.remove')"
                @click="removeOption(i)"
              >✕</button>
            </div>
          </div>

          <div class="flex gap-2 items-center">
            <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.stufenschalter.icon') }}</span>
            <IconPicker v-model="option.icon" :dark="true" />
            <input
              v-model="option.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              :title="$t('widgets.stufenschalter.color')"
            />
          </div>

          <div class="flex gap-2">
            <div class="flex-1">
              <label class="block text-xs text-gray-500 mb-0.5">{{ $t('widgets.stufenschalter.name') }}</label>
              <input
                :value="displayOptionLabel(option, i)"
                type="text"
                :placeholder="$t(defaultStepLabelKeyForValue(option.value, i), defaultStepLabelParamsForValue(option.value, i))"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                @input="updateOptionLabel(option, i, $event)"
              />
            </div>
            <div class="w-24">
              <label class="block text-xs text-gray-500 mb-0.5">{{ $t('widgets.stufenschalter.value') }}</label>
              <input
                v-model="option.value"
                type="text"
                placeholder="0"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 font-mono focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
