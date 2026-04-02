<script setup lang="ts">
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'
import { getJwt } from '@/api/client'
import { useThemeStore } from '@/stores/theme'

const ws = useWebSocket()
// Theme-Store initialisieren (setzt dark-Klasse auf <html>)
useThemeStore()

onMounted(() => {
  // WebSocket nur starten wenn JWT vorhanden (Live-Werte für eingeloggte User)
  if (getJwt()) {
    ws.connect()
  }
})
</script>

<template>
  <RouterView />
</template>
