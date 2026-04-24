<script setup lang="ts">
import { computed } from 'vue'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const label           = computed(() => (props.config.label           as string)  ?? '')
const url             = computed(() => (props.config.url             as string)  ?? '')
const sandbox         = computed(() => (props.config.sandbox         as string)  ?? '')
const allowFullscreen = computed(() => (props.config.allowFullscreen as boolean) ?? false)
const aspectRatio     = computed(() => (props.config.aspectRatio     as string)  ?? '16/9')

const containerStyle = computed((): Record<string, string> => {
  if (aspectRatio.value === 'free') return {}
  return { aspectRatio: aspectRatio.value }
})
</script>

<template>
  <div class="h-full w-full flex flex-col overflow-hidden rounded">

    <!-- Label -->
    <div
      v-if="label"
      class="shrink-0 px-2 py-1 text-xs text-gray-300 bg-gray-900/80 truncate"
    >
      {{ label }}
    </div>

    <!-- Editor-Platzhalter -->
    <div
      v-if="editorMode && !url"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-2 bg-gray-800/40"
    >
      <span class="text-4xl">🖼️</span>
      <span class="text-xs">URL konfigurieren</span>
    </div>

    <!-- Kein URL im Live-Modus -->
    <div
      v-else-if="!url"
      class="flex-1 flex items-center justify-center text-gray-600 text-xs bg-gray-900"
    >
      Keine URL konfiguriert
    </div>

    <!-- iFrame -->
    <div
      v-else
      class="flex-1 relative overflow-hidden"
      :style="containerStyle"
    >
      <!-- Overlay im Editor-Modus verhindert Interaktion mit dem iFrame -->
      <div
        v-if="editorMode"
        class="absolute inset-0 z-10 cursor-default"
      />
      <iframe
        :src="url"
        :sandbox="sandbox || undefined"
        :allowfullscreen="allowFullscreen || undefined"
        referrerpolicy="no-referrer"
        class="w-full h-full border-0"
        title="iFrame"
        data-testid="iframe-element"
      />
    </div>

  </div>
</template>
