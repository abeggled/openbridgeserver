<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DataPointValue } from '@/types'

const SAFE_PROTOCOLS = new Set(['http:', 'https:'])
const SAFE_SANDBOX_TOKENS = new Set([
  'allow-popups',
  'allow-forms',
  'allow-popups-to-escape-sandbox',
  'allow-top-navigation-by-user-activation',
])
const DEFAULT_SANDBOX = 'allow-popups allow-forms'

function sanitizeSandbox(value: unknown): string {
  if (value == null) return DEFAULT_SANDBOX
  if (typeof value !== 'string') return ''
  const tokens = value
    .split(/\s+/)
    .filter(token => SAFE_SANDBOX_TOKENS.has(token))
  return Array.from(new Set(tokens)).join(' ')
}

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()
const { t } = useI18n()

const label           = computed(() => (props.config.label           as string)  ?? '')
const urlRaw          = computed(() => (props.config.url             as string)  ?? '')
const sandboxRaw      = computed(() => props.config.sandbox)
const allowFullscreen = computed(() => (props.config.allowFullscreen as boolean) ?? false)
const aspectRatio     = computed(() => (props.config.aspectRatio     as string)  ?? '16/9')

const safeUrl = computed(() => {
  if (!urlRaw.value) return ''
  try {
    const parsed = new URL(urlRaw.value)
    return SAFE_PROTOCOLS.has(parsed.protocol) ? parsed.toString() : ''
  } catch {
    return ''
  }
})

const safeSandbox = computed(() => {
  return sanitizeSandbox(sandboxRaw.value)
})

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
      v-if="editorMode && !safeUrl"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-2 bg-gray-800/40"
    >
      <span class="text-4xl">🖼️</span>
      <span class="text-xs">{{ t('widgets.iframe.configureUrl') }}</span>
    </div>

    <!-- Kein URL im Live-Modus -->
    <div
      v-else-if="!safeUrl"
      class="flex-1 flex items-center justify-center text-gray-600 text-xs bg-gray-900"
    >
      {{ t('widgets.iframe.noUrlConfigured') }}
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
        :src="safeUrl"
        :sandbox="safeSandbox"
        :allowfullscreen="allowFullscreen || undefined"
        referrerpolicy="no-referrer"
        class="w-full h-full border-0"
        title="iFrame"
        data-testid="iframe-element"
      />
    </div>

  </div>
</template>
