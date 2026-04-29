<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { datapoints } from '@/api/client'
import { useIcons } from '@/composables/useIcons'
import type { DataPointValue } from '@/types'

interface Step {
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

const label = computed(() => (props.config.label as string | undefined) ?? '')
const steps = computed<Step[]>(() => {
  const raw = props.config.steps as Partial<Step>[] | undefined
  return (raw ?? []).map((s) => ({
    label: s.label ?? '',
    value: String(s.value ?? ''),
    icon:  s.icon  ?? '',
    color: s.color ?? '#6b7280',
  }))
})

// Wert aus String parsen: 'true'/'false' → bool, Zahl → number, sonst string
function parseValue(s: string): unknown {
  if (s === 'true')  return true
  if (s === 'false') return false
  const n = Number(s)
  if (s.trim() !== '' && !isNaN(n)) return n
  return s
}

function valuesMatch(dpVal: unknown, stepVal: string): boolean {
  const parsed = parseValue(stepVal)
  if (typeof dpVal === 'boolean') return dpVal === parsed
  if (typeof dpVal === 'number')  return dpVal === parsed
  if (typeof dpVal === 'string')  return dpVal === stepVal
  return false
}

// Status-Datenpunkt hat Vorrang
const displayValue = computed(() => props.statusValue ?? props.value)

function findStepIndex(v: DataPointValue | null): number {
  if (v === null) return -1
  return steps.value.findIndex((s) => valuesMatch(v.v, s.value))
}

// Optimistischer Schritt-Index: -1 = unbekannt
const optimisticIndex = ref<number | null>(null)

watch(displayValue, () => { optimisticIndex.value = null })

const currentIndex = computed<number>(() => {
  if (optimisticIndex.value !== null) return optimisticIndex.value
  return findStepIndex(displayValue.value)
})

const currentStep = computed<Step | null>(() =>
  currentIndex.value >= 0 ? steps.value[currentIndex.value] : null,
)

const pending = ref(false)

async function advance() {
  if (props.editorMode || props.readonly || !props.datapointId || pending.value) return
  if (steps.value.length === 0) return
  const nextIndex = currentIndex.value < 0
    ? 0
    : (currentIndex.value + 1) % steps.value.length
  const nextStep = steps.value[nextIndex]
  optimisticIndex.value = nextIndex
  pending.value = true
  try {
    await datapoints.write(props.datapointId, parseValue(nextStep.value))
  } catch {
    optimisticIndex.value = null
  } finally {
    pending.value = false
  }
}

// SVG-Icon laden und einfärben
const svgContent = ref('')

watch(
  () => currentStep.value?.icon,
  async (icon) => {
    if (!icon || !isSvgIcon(icon)) { svgContent.value = ''; return }
    svgContent.value = await getSvg(svgIconName(icon))
  },
  { immediate: true },
)

const coloredSvg = computed(() => {
  if (!svgContent.value || !currentStep.value) return ''
  const color = currentStep.value.color
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

const activeColor  = computed(() => currentStep.value?.color ?? '#6b7280')
const activeIcon   = computed(() => currentStep.value?.icon  ?? '')
const activeLabel  = computed(() => currentStep.value?.label ?? '—')
</script>

<template>
  <div
    class="flex flex-col items-center h-full p-2 select-none"
    :class="[editorMode || readonly ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="advance"
  >
    <!-- Widget-Beschriftung -->
    <span
      v-if="label"
      class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1"
    >{{ label }}</span>

    <!-- Abstandhalter oben -->
    <div style="flex: 1" />

    <!-- Icon-Bereich -->
    <div
      class="min-h-0 flex items-center justify-center w-full"
      style="flex: 3; aspect-ratio: 1; max-width: 100%"
      :style="{ color: activeColor }"
    >
      <!-- Emoji-Icon -->
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
      >{{ activeIcon }}</span>

      <!-- SVG-Icon -->
      <span
        v-else-if="activeIcon && coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        v-html="coloredSvg"
      />

      <!-- Kein Icon: Fallback-Punkt in Aktivfarbe -->
      <span
        v-else
        class="text-4xl leading-none opacity-60"
      >●</span>
    </div>

    <!-- Abstandhalter Mitte -->
    <div style="flex: 0.5" />

    <!-- Stufen-Bezeichnung -->
    <div class="min-h-0 flex items-center justify-center text-center" style="flex: 1.5">
      <span
        class="text-sm font-semibold leading-tight"
        :style="{ color: activeColor }"
      >{{ activeLabel }}</span>
    </div>

    <!-- Abstandhalter unten -->
    <div style="flex: 0.5" />
  </div>
</template>
