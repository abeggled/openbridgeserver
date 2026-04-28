<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { datapoints } from '@/api/client'
import { useIcons } from '@/composables/useIcons'
import type { DataPointValue } from '@/types'

interface Step {
  label: string
  value: number
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

const label = computed(() => (props.config.label as string | undefined) ?? '')
const steps = computed<Step[]>(() => {
  const raw = props.config.steps as Partial<Step>[] | undefined
  return (raw ?? []).map((s) => ({
    label: s.label ?? '',
    value: s.value ?? 0,
    icon:  s.icon  ?? '',
    color: s.color ?? '#6b7280',
  }))
})

// Status-Datenpunkt hat Vorrang
const displayValue = computed(() => props.statusValue ?? props.value)

function resolveCurrentValue(v: DataPointValue | null): number | null {
  if (v === null) return null
  const raw = v.v
  if (typeof raw === 'number') return raw
  return null
}

const optimisticValue = ref<number | null>(null)

watch(displayValue, () => { optimisticValue.value = null })

const currentValue = computed(() => {
  if (optimisticValue.value !== null) return optimisticValue.value
  return resolveCurrentValue(displayValue.value)
})

function isActive(step: Step): boolean {
  if (currentValue.value === null) return false
  return Math.abs(currentValue.value - step.value) < 1e-9
}

const pending = ref(false)

async function selectStep(step: Step) {
  if (props.editorMode || props.readonly || !props.datapointId || pending.value) return
  if (isActive(step)) return
  optimisticValue.value = step.value
  pending.value = true
  try {
    await datapoints.write(props.datapointId, step.value)
  } catch {
    optimisticValue.value = null
  } finally {
    pending.value = false
  }
}

// SVG-Icons pro Stufe: Cache nach Icon-Name
const svgCache = ref<Map<string, string>>(new Map())

async function loadSvg(icon: string) {
  if (!icon || !isSvgIcon(icon) || svgCache.value.has(icon)) return
  const svg = await getSvg(svgIconName(icon))
  svgCache.value = new Map(svgCache.value).set(icon, svg)
}

watch(steps, (newSteps) => {
  newSteps.forEach((s) => { if (s.icon) loadSvg(s.icon) })
}, { immediate: true, deep: true })

function colorSvg(raw: string, color: string): string {
  if (!raw) return ''
  const nonNoneFill = /\bfill\s*:\s*(?!none\b)/g
  return raw
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
}

function getStepSvg(step: Step): string {
  if (!step.icon || !isSvgIcon(step.icon)) return ''
  const raw = svgCache.value.get(step.icon) ?? ''
  if (!raw) return ''
  return colorSvg(raw, isActive(step) ? step.color : '#9ca3af')
}
</script>

<template>
  <div class="flex flex-col h-full p-2 gap-1 select-none overflow-hidden">
    <!-- Widget-Beschriftung -->
    <span
      v-if="label"
      class="text-xs text-gray-500 dark:text-gray-400 truncate text-center shrink-0"
    >{{ label }}</span>

    <!-- Stufen-Buttons -->
    <div class="flex flex-wrap gap-1 flex-1 min-h-0 content-center">
      <button
        v-for="(step, i) in steps"
        :key="i"
        type="button"
        :disabled="editorMode || readonly || pending"
        class="flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 rounded border transition-all duration-150 flex-1 min-w-0 min-h-[2.5rem]"
        :class="[
          isActive(step)
            ? 'shadow-sm'
            : 'border-gray-600 dark:border-gray-700 text-gray-400 hover:border-gray-400 hover:text-gray-300',
          editorMode || readonly ? 'cursor-default opacity-70' : 'cursor-pointer',
        ]"
        :style="isActive(step)
          ? { borderColor: step.color, backgroundColor: step.color + '22', color: step.color }
          : {}"
        @click="selectStep(step)"
      >
        <!-- Icon -->
        <template v-if="step.icon">
          <span
            v-if="!isSvgIcon(step.icon)"
            class="text-lg leading-none"
            :style="{ filter: isActive(step) ? 'none' : 'grayscale(1) opacity(0.5)' }"
          >{{ step.icon }}</span>
          <span
            v-else-if="getStepSvg(step)"
            class="w-5 h-5 [&>svg]:w-full [&>svg]:h-full shrink-0"
            v-html="getStepSvg(step)"
          />
          <span v-else class="inline-block opacity-30 text-xs">▪</span>
        </template>

        <!-- Label -->
        <span class="text-xs font-medium truncate w-full text-center leading-none">
          {{ step.label || step.value }}
        </span>
      </button>
    </div>
  </div>
</template>
