<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { datapoints, getWriteContext } from '@/api/client'
import VisuIcon from '@/components/VisuIcon.vue'
import type { DataPointValue } from '@/types'

interface ButtonConfig {
  id: string
  label: string
  icon: string
  color: string
  value: string
  resetEnabled: boolean
  resetValue: string
  resetDelayMs: number
  preserveIconColor: boolean
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
  readonly?: boolean
}>()

const { t } = useI18n()

const label = computed(() => (props.config.label as string | undefined) ?? '')
const showLabel = computed(() => props.config.showLabel !== false)
const columns = computed(() => {
  const raw = Number(props.config.columns)
  return Number.isFinite(raw) ? Math.min(4, Math.max(1, Math.round(raw))) : 2
})

const buttons = computed<ButtonConfig[]>(() => {
  const raw = props.config.buttons as Partial<ButtonConfig>[] | undefined
  if (!Array.isArray(raw) || raw.length === 0) return []
  return raw.map((button, index) => ({
    id: button.id || `button-${index + 1}`,
    label: normalizeButtonLabel(button.label, index),
    icon: button.icon ?? '',
    color: button.color ?? '#3b82f6',
    value: String(button.value ?? 'true'),
    resetEnabled: parseBoolean(button.resetEnabled, false),
    resetValue: String(button.resetValue ?? 'false'),
    resetDelayMs: parseDelay(button.resetDelayMs),
    preserveIconColor: parseBoolean(button.preserveIconColor, false),
  }))
})

const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(${columns.value}, minmax(0, 1fr))`,
}))

const pendingId = ref<string | null>(null)
const feedback = ref<Record<string, 'success' | 'error'>>({})
let feedbackTimer: number | undefined
let unmounted = false

function parseBoolean(raw: unknown, fallback: boolean): boolean {
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'string') {
    const value = raw.trim().toLowerCase()
    if (value === 'true') return true
    if (value === 'false') return false
  }
  return fallback
}

function parseDelay(raw: unknown): number {
  const delay = Number(raw)
  return Number.isFinite(delay) ? Math.max(0, Math.round(delay)) : 300
}

function defaultButtonLabel(index: number): string {
  return t('widgets.buttongroup.defaultButtonWithNumber', { number: index + 1 })
}

function normalizeButtonLabel(raw: unknown, index: number): string {
  if (typeof raw !== 'string') return defaultButtonLabel(index)
  const label = raw.trim()
  if (!label || label === 'widgets.buttongroup.defaultButton') return defaultButtonLabel(index)
  return raw
}

function parseValue(raw: string): unknown {
  const value = raw.trim()
  if (value === 'true') return true
  if (value === 'false') return false
  const numberValue = Number(value)
  if (value !== '' && Number.isFinite(numberValue)) return numberValue
  return raw
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function setFeedback(id: string, value: 'success' | 'error') {
  if (feedbackTimer !== undefined) window.clearTimeout(feedbackTimer)
  feedback.value = { [id]: value }
  feedbackTimer = window.setTimeout(() => {
    feedback.value = {}
    feedbackTimer = undefined
  }, 1500)
}

async function press(button: ButtonConfig) {
  if (props.editorMode || props.readonly || !props.datapointId || pendingId.value) return
  const datapointId = props.datapointId
  const writeContext = { ...getWriteContext() }
  pendingId.value = button.id
  feedback.value = {}
  try {
    await datapoints.write(datapointId, parseValue(button.value), writeContext)
    if (button.resetEnabled) {
      await wait(button.resetDelayMs)
      await datapoints.write(datapointId, parseValue(button.resetValue), writeContext)
    }
    if (!unmounted) setFeedback(button.id, 'success')
  } catch {
    if (!unmounted) setFeedback(button.id, 'error')
  } finally {
    if (!unmounted) pendingId.value = null
  }
}

onUnmounted(() => {
  unmounted = true
  if (feedbackTimer !== undefined) window.clearTimeout(feedbackTimer)
})
</script>

<template>
  <div class="h-full min-h-0 flex flex-col gap-2 p-3 select-none">
    <span v-if="showLabel && label" class="text-xs text-gray-500 dark:text-gray-400 truncate text-center shrink-0">
      {{ label }}
    </span>

    <div
      v-if="buttons.length"
      class="grid gap-2 min-h-0 flex-1"
      :style="gridStyle"
    >
      <button
        v-for="button in buttons"
        :key="button.id"
        type="button"
        class="min-w-0 min-h-0 rounded-lg border bg-white/50 dark:bg-gray-900/25 px-2 py-2 text-xs font-semibold transition active:scale-[0.98] disabled:active:scale-100 disabled:cursor-default overflow-hidden"
        :class="[
          feedback[button.id] === 'error'
            ? 'border-red-500/50 text-red-500'
            : 'border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-100',
          editorMode || readonly || !datapointId || pendingId ? 'opacity-60 cursor-default' : 'cursor-pointer',
        ]"
        :disabled="editorMode || readonly || !datapointId || !!pendingId"
        :title="!datapointId ? $t('widgets.buttongroup.noDatapoint') : undefined"
        :style="{ borderColor: feedback[button.id] ? undefined : `${button.color}66` }"
        @click="press(button)"
      >
        <span class="flex h-full min-w-0 flex-col items-center justify-center gap-1">
          <span
            v-if="button.icon"
            class="text-xl leading-none"
            :style="{ color: button.color }"
          >
            <VisuIcon :icon="button.icon" :preserve-color="button.preserveIconColor" />
          </span>
          <span
            class="max-w-full truncate leading-tight"
            :style="{ color: feedback[button.id] === 'error' ? undefined : button.color }"
          >
            {{ pendingId === button.id ? '...' : feedback[button.id] === 'success' ? $t('widgets.buttongroup.sent') : feedback[button.id] === 'error' ? $t('widgets.buttongroup.error') : button.label }}
          </span>
        </span>
      </button>
    </div>

    <div v-else class="flex-1 flex items-center justify-center text-xs text-gray-400 dark:text-gray-500 text-center">
      {{ $t('widgets.buttongroup.empty') }}
    </div>
  </div>
</template>
