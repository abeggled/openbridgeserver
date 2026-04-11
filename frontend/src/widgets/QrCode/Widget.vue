<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import QRCode from 'qrcode'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const content         = computed(() => (props.config.content         as string) ?? '')
const label           = computed(() => (props.config.label           as string) ?? '')
const errorCorrection = computed(() => (props.config.errorCorrection as string) ?? 'M')
const darkColor       = computed(() => (props.config.darkColor       as string) ?? '#000000')
const lightColor      = computed(() => (props.config.lightColor      as string) ?? '#ffffff')

const svgHtml  = ref('')
const genError = ref(false)

async function generateQr() {
  const text = content.value.trim()
  if (!text) {
    svgHtml.value = ''
    genError.value = false
    return
  }
  try {
    svgHtml.value = await QRCode.toString(text, {
      type: 'svg',
      errorCorrectionLevel: errorCorrection.value as 'L' | 'M' | 'Q' | 'H',
      color: { dark: darkColor.value, light: lightColor.value },
      margin: 1,
    })
    genError.value = false
  } catch {
    svgHtml.value = ''
    genError.value = true
  }
}

onMounted(generateQr)
watch([content, errorCorrection, darkColor, lightColor], generateQr)
</script>

<template>
  <div class="h-full w-full flex flex-col items-center justify-center p-2 gap-1 overflow-hidden">

    <!-- Kein Inhalt konfiguriert -->
    <div
      v-if="!content"
      class="flex flex-col items-center justify-center flex-1 gap-2 text-gray-400 dark:text-gray-600"
      data-testid="qrcode-placeholder"
    >
      <span class="text-4xl">▣</span>
      <span class="text-xs">QR-Code-Inhalt konfigurieren</span>
    </div>

    <!-- Fehler beim Generieren -->
    <div
      v-else-if="genError"
      class="flex-1 flex items-center justify-center text-red-400 text-xs"
      data-testid="qrcode-error"
    >
      Ungültiger Inhalt
    </div>

    <!-- QR-Code als SVG -->
    <div
      v-else
      class="flex-1 flex items-center justify-center overflow-hidden w-full [&_svg]:w-full [&_svg]:h-full [&_svg]:max-h-full"
      data-testid="qrcode-svg"
      v-html="svgHtml"
    />

    <!-- Label -->
    <span
      v-if="label"
      class="shrink-0 text-xs text-gray-600 dark:text-gray-400 truncate max-w-full text-center"
      data-testid="qrcode-label"
    >{{ label }}</span>

  </div>
</template>
