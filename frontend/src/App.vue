<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWebSocket } from '@/composables/useWebSocket'
import { getJwt } from '@/api/client'
import { useThemeStore } from '@/stores/theme'
import { useFormatStore } from '@/stores/format'
import { useDeviceStore } from '@/stores/device'
import { useIdleReturn } from '@/composables/useIdleReturn'

const ws = useWebSocket()
// Theme-Store initialisieren (setzt dark-Klasse auf <html>)
useThemeStore()
// Regionalformat für Zahlen/Währung/Datum laden (Issue #1073) — öffentliche Route,
// damit auch anonyme und PIN-Nutzer die konfigurierte Formatierung sehen.
const format = useFormatStore()
// Weekday/month names follow the UI language, the formats follow the server
// settings — keep the store's copy of the language current (issue #1073).
const { locale } = useI18n()
watch(locale, (code) => format.setUiLanguage(String(code)), { immediate: true })

// Geräteprofil (Startseite + Rücksprung) — zentral im Admin-GUI gepflegt.
const device = useDeviceStore()
useIdleReturn()

let stopDeviceRefresh: (() => void) | null = null

onMounted(() => {
  format.load()
  device.load()
  stopDeviceRefresh = device.keepFresh()
  // WebSocket nur starten wenn JWT vorhanden (Live-Werte für eingeloggte User)
  if (getJwt()) {
    ws.connect()
  }
})

onUnmounted(() => stopDeviceRefresh?.())
</script>

<template>
  <RouterView />
</template>
