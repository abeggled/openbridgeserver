<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useWebSocket } from '@/composables/useWebSocket'
import { getJwt } from '@/api/client'
import { useThemeStore } from '@/stores/theme'
import { useFormatStore } from '@/stores/format'

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

onMounted(() => {
  format.load()
  // WebSocket nur starten wenn JWT vorhanden (Live-Werte für eingeloggte User)
  if (getJwt()) {
    ws.connect()
  }
})
</script>

<template>
  <RouterView />
</template>
