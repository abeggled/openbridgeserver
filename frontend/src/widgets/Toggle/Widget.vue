<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { datapoints } from '@/api/client'
import { useIcons } from '@/composables/useIcons'
import type { DataPointValue } from '@/types'

type DisplayMode = 'switch' | 'icon_only' | 'icon_text'

interface StateRule {
  icon: string
  color: string
  text: string
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
const mode  = computed<DisplayMode>(() => (props.config.mode as DisplayMode | undefined) ?? 'switch')

const onRule = computed<StateRule>(() => {
  const r = props.config.on as Partial<StateRule> | undefined
  return { icon: r?.icon ?? '', color: r?.color ?? '#3b82f6', text: r?.text ?? 'EIN' }
})

const offRule = computed<StateRule>(() => {
  const r = props.config.off as Partial<StateRule> | undefined
  return { icon: r?.icon ?? '', color: r?.color ?? '#6b7280', text: r?.text ?? 'AUS' }
})

// Status-Datenpunkt hat Vorrang für die Anzeige; sonst Haupt-Datenpunkt
const displayValue = computed(() => props.statusValue ?? props.value)

function resolveIsOn(v: DataPointValue | null): boolean {
  if (v === null) return false
  const raw = v.v
  if (typeof raw === 'boolean') return raw
  if (typeof raw === 'number') return raw !== 0
  return false
}

// Optimistischer lokaler Status: wird nach dem Schreiben sofort aktualisiert,
// bis ein neuer Wert vom Server eintrifft.
const optimisticValue = ref<boolean | null>(null)

watch(displayValue, () => {
  optimisticValue.value = null
})

const isOn = computed(() => {
  if (optimisticValue.value !== null) return optimisticValue.value
  return resolveIsOn(displayValue.value)
})

const pending = ref(false)

async function toggle() {
  if (props.editorMode || props.readonly || !props.datapointId || pending.value) return
  const next = !isOn.value
  optimisticValue.value = next
  pending.value = true
  try {
    await datapoints.write(props.datapointId, next)
  } catch {
    // Optimistischen Wert bei Fehler zurücksetzen
    optimisticValue.value = null
  } finally {
    pending.value = false
  }
}

// Aktiven Zustand basierend auf isOn
const activeRule   = computed<StateRule>(() => isOn.value ? onRule.value : offRule.value)
const activeIcon   = computed(() => activeRule.value.icon)
const activeColor  = computed(() => activeRule.value.color)
const activeText   = computed(() => activeRule.value.text)

// SVG-Icon laden und auf currentColor umfärben (gleiche Logik wie ValueDisplay)
const svgContent = ref('')

watch(
  activeIcon,
  async (icon) => {
    if (!isSvgIcon(icon)) { svgContent.value = ''; return }
    svgContent.value = await getSvg(svgIconName(icon))
  },
  { immediate: true },
)

const coloredSvg = computed(() => {
  if (!svgContent.value) return ''
  const nonNoneFill = /\bfill\s*:\s*(?!none\b)/g
  return svgContent.value
    .replace(/<svg\b([^>]*)>/, (_, attrs: string) => {
      const updated = /\bfill=/.test(attrs)
        ? attrs.replace(/\bfill="(?!none\b)[^"]*"/, 'fill="currentColor"')
        : `${attrs} fill="currentColor"`
      return `<svg${updated}>`
    })
    .replace(/\bfill="(?!none\b)[^"]*"/g, 'fill="currentColor"')
    .replace(/\bstroke="(?!none\b)[^"]*"/g, 'stroke="currentColor"')
    .replace(/\bstyle="([^"]*)"/g, (_, s: string) =>
      `style="${s
        .replace(nonNoneFill, 'fill:currentColor ')
        .replace(/\bstroke\s*:\s*(?!none\b)[^;"]*/g, 'stroke:currentColor')}"`)
    .replace(/(<style[^>]*>)([\s\S]*?)(<\/style>)/g, (_, open, css: string, close) =>
      `${open}${css
        .replace(nonNoneFill, 'fill:currentColor ')
        .replace(/\bstroke\s*:\s*(?!none\b)[^;}\n]*/g, 'stroke:currentColor')}${close}`)
})
</script>

<template>
  <!-- ── SCHALTER-Modus (klassischer Toggle-Schalter) ────────────────────────── -->
  <div
    v-if="mode === 'switch'"
    class="flex flex-col items-center justify-center h-full p-3 gap-2 select-none"
    :class="[editorMode || readonly ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="toggle"
  >
    <span class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center">{{ label }}</span>

    <!-- Optionales Icon (wenn konfiguriert) -->
    <div
      v-if="activeIcon"
      class="flex items-center justify-center"
      data-testid="toggle-icon"
      :style="{ color: activeColor }"
    >
      <span v-if="!isSvgIcon(activeIcon)" class="text-2xl leading-none">{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="w-8 h-8 [&>svg]:w-full [&>svg]:h-full"
        v-html="coloredSvg"
      />
    </div>

    <!-- Toggle-Schalter -->
    <button
      class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none shrink-0"
      :class="isOn ? '' : 'bg-gray-300 dark:bg-gray-600'"
      :style="isOn ? { backgroundColor: onRule.color } : {}"
      :disabled="editorMode || readonly || pending"
      :aria-checked="isOn"
      role="switch"
    >
      <span
        class="absolute top-0.5 left-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform duration-200"
        :class="{ 'translate-x-7': isOn }"
      />
    </button>

    <span
      class="text-xs font-medium"
      :style="{ color: activeColor }"
      data-testid="toggle-text"
    >{{ activeText }}</span>
  </div>

  <!-- ── NUR-ICON-Modus ──────────────────────────────────────────────────────── -->
  <div
    v-else-if="mode === 'icon_only'"
    class="flex flex-col items-center h-full p-2 select-none"
    :class="[editorMode || readonly ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="toggle"
  >
    <span v-if="label" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ label }}</span>

    <!-- Abstandhalter oben: zentriert das Icon vertikal -->
    <div style="flex: 1" />

    <!-- Icon: 3 flex-Anteile -->
    <div
      class="min-h-0 flex items-center justify-center w-full"
      style="flex: 3; aspect-ratio: 1; max-width: 100%"
      data-testid="toggle-icon"
      :style="{ color: activeColor }"
    >
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
      >{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        v-html="coloredSvg"
      />
    </div>

    <!-- Abstandhalter unten -->
    <div style="flex: 1" />

    <span class="sr-only" data-testid="toggle-text">{{ activeText }}</span>
  </div>

  <!-- ── ICON + TEXT-Modus ───────────────────────────────────────────────────── -->
  <div
    v-else
    class="flex flex-col items-center h-full p-2 select-none"
    :class="[editorMode || readonly ? 'opacity-60 cursor-default' : 'cursor-pointer']"
    @click="toggle"
  >
    <span v-if="label" class="text-xs text-gray-500 dark:text-gray-400 truncate w-full text-center shrink-0 mb-1">{{ label }}</span>

    <!-- Icon: 3 flex-Anteile -->
    <div
      class="min-h-0 flex items-center justify-center w-full"
      style="flex: 3; aspect-ratio: 1; max-width: 100%"
      data-testid="toggle-icon"
      :style="{ color: activeColor }"
    >
      <span
        v-if="activeIcon && !isSvgIcon(activeIcon)"
        class="leading-none select-none h-full flex items-center"
        style="font-size: min(100%, 4rem)"
      >{{ activeIcon }}</span>
      <span
        v-else-if="coloredSvg"
        class="h-full max-w-full [&>svg]:w-full [&>svg]:h-full"
        style="aspect-ratio: 1"
        v-html="coloredSvg"
      />
    </div>

    <!-- Text: 2 flex-Anteile -->
    <div class="min-h-0 flex items-center justify-center text-center mt-1" style="flex: 2">
      <span
        class="text-2xl font-semibold leading-none"
        :style="{ color: activeColor }"
        data-testid="toggle-text"
      >{{ activeText }}</span>
    </div>
  </div>
</template>
