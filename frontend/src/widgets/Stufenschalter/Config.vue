<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconPicker from '@/components/IconPicker.vue'

interface Step {
  label: string
  value: string
  icon: string
  color: string
}

interface Cfg {
  label: string
  steps: Step[]
}

const MIN_STEPS = 2
const MAX_STEPS = 10
const DEFAULT_OFF_LABEL = 'widgets.stufenschalter.defaultOffLabel'
const DEFAULT_STEP_LABEL = 'widgets.stufenschalter.defaultStepLabel'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

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

function normalizeStepLabel(raw: unknown, index: number, value?: unknown): string {
  const defaultKey = defaultStepLabelKey(index, value)
  if (typeof raw !== 'string') return defaultKey
  const label = raw.trim()
  if (!label || label === DEFAULT_STEP_LABEL || label === DEFAULT_OFF_LABEL || label === defaultStepLabelForValue(value, index)) {
    return defaultKey
  }
  return raw
}

function parseSteps(raw: unknown): Step[] {
  const arr = raw as Partial<Step>[] | undefined
  if (!Array.isArray(arr) || arr.length < MIN_STEPS) {
    return [
      { label: DEFAULT_OFF_LABEL, value: '0', icon: '', color: '#6b7280' },
      { label: DEFAULT_STEP_LABEL, value: '1', icon: '', color: '#3b82f6' },
      { label: DEFAULT_STEP_LABEL, value: '2', icon: '', color: '#10b981' },
    ]
  }
  return arr.map((s, index) => ({
    label: normalizeStepLabel(s.label, index, s.value),
    value: String(s.value ?? ''),
    icon:  s.icon  ?? '',
    color: s.color ?? '#6b7280',
  }))
}

const cfg = reactive<Cfg>({
  label: (props.modelValue.label as string) ?? '',
  steps: parseSteps(props.modelValue.steps),
})

function serializedConfig(): Record<string, unknown> {
  return {
    ...cfg,
    steps: cfg.steps.map((step, index) => ({
      ...step,
      label: normalizeStepLabel(step.label, index, step.value),
    })),
  }
}

watch(cfg, () => emit('update:modelValue', serializedConfig()), { deep: true })

function displayStepLabel(step: Step, index: number): string {
  const label = normalizeStepLabel(step.label, index, step.value)
  return label === DEFAULT_OFF_LABEL || label === DEFAULT_STEP_LABEL ? defaultStepLabelForValue(step.value, index) : step.label
}

function updateStepLabel(step: Step, index: number, event: Event) {
  const value = (event.target as HTMLInputElement).value
  step.label = normalizeStepLabel(value, index, step.value)
}

function addStep() {
  if (cfg.steps.length >= MAX_STEPS) return
  cfg.steps.push({ label: DEFAULT_STEP_LABEL, value: String(cfg.steps.length), icon: '', color: '#6b7280' })
}

function removeStep(i: number) {
  if (cfg.steps.length <= MIN_STEPS) return
  cfg.steps.splice(i, 1)
}

function moveUp(i: number) {
  if (i === 0) return
  ;[cfg.steps[i - 1], cfg.steps[i]] = [cfg.steps[i], cfg.steps[i - 1]]
}

function moveDown(i: number) {
  if (i === cfg.steps.length - 1) return
  ;[cfg.steps[i + 1], cfg.steps[i]] = [cfg.steps[i], cfg.steps[i + 1]]
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.stufenschalter.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Stufen -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          {{ $t('widgets.stufenschalter.stepsCount', { n: cfg.steps.length, max: MAX_STEPS }) }}
        </p>
        <button
          type="button"
          :disabled="cfg.steps.length >= MAX_STEPS"
          class="text-xs px-2 py-1 rounded border border-dashed border-gray-600 text-gray-400 hover:border-blue-500 hover:text-blue-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          @click="addStep"
        >{{ $t('widgets.stufenschalter.addStep') }}</button>
      </div>

      <p class="text-xs text-gray-600 mb-2">
        {{ $t('widgets.stufenschalter.hint') }}
      </p>

      <div class="space-y-2">
        <div
          v-for="(step, i) in cfg.steps"
          :key="i"
          class="border border-gray-700 rounded p-2 space-y-2"
        >
          <!-- Kopfzeile mit Reihenfolge-Buttons -->
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
                :disabled="i === cfg.steps.length - 1"
                class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs"
                :title="$t('widgets.stufenschalter.moveDown')"
                @click="moveDown(i)"
              >▼</button>
              <button
                type="button"
                :disabled="cfg.steps.length <= MIN_STEPS"
                class="w-5 h-5 flex items-center justify-center rounded text-red-600 hover:text-red-400 disabled:opacity-20 text-xs"
                :title="$t('widgets.stufenschalter.remove')"
                @click="removeStep(i)"
              >✕</button>
            </div>
          </div>

          <!-- Icon + Farbe -->
          <div class="flex gap-2 items-center">
            <span class="text-xs text-gray-500 w-8 shrink-0">{{ $t('widgets.stufenschalter.icon') }}</span>
            <IconPicker v-model="step.icon" :dark="true" />
            <input
              v-model="step.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              :title="$t('widgets.stufenschalter.color')"
            />
          </div>

          <!-- Name + Wert -->
          <div class="flex gap-2">
            <div class="flex-1">
              <label class="block text-xs text-gray-500 mb-0.5">{{ $t('widgets.stufenschalter.name') }}</label>
              <input
                :value="displayStepLabel(step, i)"
                type="text"
                :placeholder="$t(defaultStepLabelKeyForValue(step.value, i), defaultStepLabelParamsForValue(step.value, i))"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                @input="updateStepLabel(step, i, $event)"
              />
            </div>
            <div class="w-24">
              <label class="block text-xs text-gray-500 mb-0.5">{{ $t('widgets.stufenschalter.value') }}</label>
              <input
                v-model="step.value"
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
