<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { datapoints } from '@/api/client'
import VisuIcon from '@/components/VisuIcon.vue'
import { useIcons } from '@/composables/useIcons'
import type { DataPointValue } from '@/types'

type Mode = 'sequence' | 'select-save' | 'select-direct'

interface Option {
  label: string
  value: string
  icon: string
  color: string
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const { getSvg, isSvgIcon, svgIconName } = useIcons()
const { t } = useI18n()
const DEFAULT_OFF_LABEL = 'widgets.stufenschalter.defaultOffLabel'
const DEFAULT_STEP_LABEL = 'widgets.stufenschalter.defaultStepLabel'

const label = computed(() => (props.config.label as string | undefined) ?? '')
const mode = computed<Mode>(() => {
  const raw = props.config.mode
  if (raw === 'select-save' || raw === 'select-direct') return raw
  return 'sequence'
})

const pending = ref(false)
const error = ref('')
const selectedValue = ref<string | null>(null)
const optimisticValue = ref<string | null>(null)

function sanitizeColor(value: unknown, fallback = '#6b7280'): string {
  if (typeof value !== 'string') return fallback
  const color = value.trim()
  if (/^#[0-9a-fA-F]{3}$/.test(color)) return color
  if (/^#[0-9a-fA-F]{6}$/.test(color)) return color
  return fallback
}

function defaultOptionLabel(value: unknown, index: number): string {
  const numericValue = Number(value)
  if (String(value ?? '') === '0') return t(DEFAULT_OFF_LABEL)
  if (Number.isInteger(numericValue) && numericValue > 0) {
    return t(DEFAULT_STEP_LABEL, { n: numericValue })
  }
  return t(DEFAULT_STEP_LABEL, { n: index + 1 })
}

function normalizeOptionLabel(raw: unknown, value: unknown, index: number): string {
  if (typeof raw !== 'string') return defaultOptionLabel(value, index)
  const label = raw.trim()
  if (raw === DEFAULT_OFF_LABEL) return t(DEFAULT_OFF_LABEL)
  if (raw === DEFAULT_STEP_LABEL) return defaultOptionLabel(value, index)
  if (label === 'Aus') return t(DEFAULT_OFF_LABEL)
  const legacyStepMatch = label.match(/^Stufe\s+(\d+)$/)
  if (legacyStepMatch) return t(DEFAULT_STEP_LABEL, { n: Number(legacyStepMatch[1]) })
  return raw
}

function optionInput(config: Record<string, unknown>): unknown {
  if (Array.isArray(config.steps) && config.mode !== 'select-save' && config.mode !== 'select-direct') {
    return config.steps
  }
  return config.options ?? config.steps
}

const options = computed<Option[]>(() => {
  const raw = optionInput(props.config) as Partial<Option>[] | undefined
  return (Array.isArray(raw) ? raw : []).map((option, index) => ({
    label: normalizeOptionLabel(option.label, option.value, index),
    value: String(option.value ?? ''),
    icon: option.icon ?? '',
    color: sanitizeColor(option.color),
  }))
})

function parseValue(s: string): unknown {
  if (s === 'true') return true
  if (s === 'false') return false
  const n = Number(s)
  if (s.trim() !== '' && !isNaN(n)) return n
  return s
}

function valuesMatch(dpVal: unknown, optionVal: string): boolean {
  const parsed = parseValue(optionVal)
  if (typeof dpVal === 'boolean') return dpVal === parsed
  if (typeof dpVal === 'number') return dpVal === parsed
  if (typeof dpVal === 'string') return dpVal === optionVal
  return false
}

const displayValue = computed(() => props.statusValue ?? props.value)

function findOptionIndex(v: DataPointValue | null): number {
  if (v === null) return -1
  return options.value.findIndex((option) => valuesMatch(v.v, option.value))
}

const committedIndex = computed(() => {
  if (optimisticValue.value !== null) {
    return options.value.findIndex((option) => option.value === optimisticValue.value)
  }
  return findOptionIndex(displayValue.value)
})

const committedValue = computed(() =>
  committedIndex.value >= 0 ? options.value[committedIndex.value].value : null,
)

watch(
  () => ({
    value: displayValue.value?.v,
    t: displayValue.value?.t,
    q: displayValue.value?.q,
    mode: mode.value,
    optionValues: options.value.map((option) => option.value).join('\u0000'),
  }),
  (current, previous) => {
    const valueChanged = !previous || !Object.is(current.value, previous.value)
    const selectionShapeChanged = !previous
      || current.mode !== previous.mode
      || current.optionValues !== previous.optionValues

    optimisticValue.value = null
    if (current.mode !== 'select-save' || valueChanged || selectionShapeChanged) {
      selectedValue.value = committedValue.value
    }
    error.value = ''
  },
  { immediate: true },
)

const isLocked = computed(() => props.editorMode || props.readonly || !props.datapointId)
const isSelectMode = computed(() => mode.value !== 'sequence')

const activeValue = computed(() =>
  isSelectMode.value ? (selectedValue.value ?? committedValue.value) : committedValue.value,
)

const activeOption = computed<Option | null>(() =>
  options.value.find((option) => option.value === activeValue.value) ?? null,
)

const hasChanges = computed(() =>
  selectedValue.value !== null && selectedValue.value !== committedValue.value,
)
const canSave = computed(() => mode.value === 'select-save' && !isLocked.value && !pending.value && hasChanges.value)

async function writeValue(value: string) {
  if (isLocked.value || pending.value || !props.datapointId) return
  pending.value = true
  error.value = ''
  try {
    await datapoints.write(props.datapointId, parseValue(value))
    optimisticValue.value = value
    selectedValue.value = value
  } catch (e) {
    optimisticValue.value = null
    if (mode.value === 'select-direct') selectedValue.value = committedValue.value
    error.value = e instanceof Error ? e.message : t('widgets.stufenschalter.writeError')
  } finally {
    pending.value = false
  }
}

async function advance() {
  if (mode.value !== 'sequence' || isLocked.value || pending.value) return
  if (options.value.length === 0) return
  const nextIndex = committedIndex.value < 0
    ? 0
    : (committedIndex.value + 1) % options.value.length
  await writeValue(options.value[nextIndex].value)
}

async function selectOption(value: string) {
  if (isLocked.value || pending.value) return
  selectedValue.value = value
  error.value = ''
  if (mode.value === 'select-direct') await writeValue(value)
}

async function save() {
  if (!canSave.value || selectedValue.value === null) return
  await writeValue(selectedValue.value)
}

const svgContent = ref('')

watch(
  () => activeOption.value?.icon,
  async (icon) => {
    if (!icon || !isSvgIcon(icon)) { svgContent.value = ''; return }
    svgContent.value = await getSvg(svgIconName(icon))
  },
  { immediate: true },
)

const coloredSvg = computed(() => {
  if (!svgContent.value || !activeOption.value) return ''
  const color = sanitizeColor(activeOption.value.color)
  const nonNoneFill = /\bfill\s*:\s*(?!none\b)/g
  return svgContent.value
    .replace(/<svg\b([^>]*)>/, (_, attrs: string) => {
      const updated = /\bfill=/.test(attrs)
        ? attrs.replace(/\bfill="(?!none\b)[^"]*"/, `fill="${color}"`)
        : `${attrs} fill="${color}"`
      return `<svg${updated}>`
    })
    .replace(/\bfill="(?!none\b)[^"]*"/g, `fill="${color}"`)
    .replace(/\bstroke="(?!none\b)[^"]*"/g, `stroke="${color}"`)
    .replace(/\bstyle="([^"]*)"/g, (_, s: string) =>
      `style="${s
        .replace(nonNoneFill, `fill:${color} `)
        .replace(/\bstroke\s*:\s*(?!none\b)[^;"]*/g, `stroke:${color}`)}"`)
    .replace(/(<style[^>]*>)([\s\S]*?)(<\/style>)/g, (_, open, css: string, close) =>
      `${open}${css
        .replace(nonNoneFill, `fill:${color} `)
        .replace(/\bstroke\s*:\s*(?!none\b)[^;}\n]*/g, `stroke:${color}`)}${close}`)
})

const activeColor = computed(() => sanitizeColor(activeOption.value?.color))
const activeIcon = computed(() => activeOption.value?.icon ?? '')
const activeLabel = computed(() => activeOption.value?.label || activeOption.value?.value || '—')
const saveLabel = computed(() => t('widgets.stufenschalter.save'))
</script>

<template>
  <div
    v-if="mode === 'sequence'"
    class="flex h-full flex-col items-center p-2 select-none"
    :class="[isLocked ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="advance"
  >
    <span
      v-if="label"
      class="mb-1 w-full shrink-0 truncate text-center text-xs text-gray-500 dark:text-gray-400"
    >{{ label }}</span>

    <div style="flex: 1" />

    <div
      data-testid="stufenschalter-icon"
      class="flex min-h-0 w-full items-center justify-center"
      style="flex: 3; aspect-ratio: 1; max-width: 100%"
      :style="{ color: activeColor }"
    >
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="flex h-full items-center leading-none select-none"
        style="font-size: min(100%, 4rem)"
      >{{ activeIcon }}</span>

      <span
        v-else-if="activeIcon && coloredSvg"
        class="h-full max-w-full [&>svg]:h-full [&>svg]:w-full"
        style="aspect-ratio: 1"
        v-html="coloredSvg"
      />

      <span
        v-else
        class="text-4xl leading-none opacity-60"
      >●</span>
    </div>

    <div style="flex: 0.5" />

    <div class="flex min-h-0 items-center justify-center text-center" style="flex: 1.5">
      <span
        data-testid="stufenschalter-label"
        class="text-sm font-semibold leading-tight"
        :style="{ color: activeColor }"
      >{{ activeLabel }}</span>
    </div>

    <p
      v-if="error"
      class="w-full truncate text-center text-xs text-red-600 dark:text-red-400"
      data-testid="stufenschalter-error"
    >{{ error }}</p>

    <div style="flex: 0.5" />
  </div>

  <div v-else class="flex h-full min-h-0 flex-col p-2 select-none" :class="isLocked ? 'opacity-60' : ''">
    <span
      v-if="label"
      class="mb-2 w-full shrink-0 truncate text-center text-xs text-gray-500 dark:text-gray-400"
    >{{ label }}</span>

    <div class="grid min-h-0 flex-1 auto-rows-min gap-1.5 overflow-y-auto pr-1" data-testid="stufenschalter-options">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="flex min-h-[2rem] items-center justify-center gap-1 rounded border px-2 py-1 text-xs font-semibold transition-colors disabled:cursor-not-allowed"
        :class="activeValue === option.value
          ? 'border-transparent bg-gray-900 text-white shadow-sm dark:bg-gray-100 dark:text-gray-950'
          : 'border-gray-300 bg-white text-gray-700 hover:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:border-gray-500'"
        :style="activeValue === option.value ? { backgroundColor: option.color } : { color: option.color }"
        :disabled="isLocked || pending"
        :aria-pressed="activeValue === option.value"
        data-testid="stufenschalter-option"
        @click="selectOption(option.value)"
      >
        <span
          v-if="option.icon"
          class="flex h-4 w-4 shrink-0 items-center justify-center text-base leading-none [&>img]:h-full [&>img]:w-full"
          data-testid="stufenschalter-option-icon"
        >
          <VisuIcon :icon="option.icon" />
        </span>
        <span class="min-w-0 truncate">{{ option.label || option.value }}</span>
      </button>
    </div>

    <div v-if="mode === 'select-save'" class="mt-2 flex shrink-0 items-center gap-2">
      <button
        type="button"
        class="min-h-[2rem] flex-1 rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-500 dark:disabled:bg-gray-700 dark:disabled:text-gray-400"
        :disabled="!canSave"
        data-testid="stufenschalter-save"
        @click="save"
      >
        {{ pending ? '...' : saveLabel }}
      </button>
    </div>

    <p
      v-if="error"
      class="mt-1 shrink-0 truncate text-center text-xs text-red-600 dark:text-red-400"
      data-testid="stufenschalter-error"
    >{{ error }}</p>
  </div>
</template>
