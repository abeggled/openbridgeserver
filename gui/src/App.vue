<template>
  <div class="min-h-screen">
    <div v-if="showRuntimeStrip" :class="runtimeStripClass">
      {{ runtimeStripText }}
    </div>
    <component :is="layout">
      <router-view />
    </component>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { useSettingsStore } from '@/stores/settings'
import AppLayout from '@/components/layout/AppLayout.vue'
import PlainLayout from '@/components/layout/PlainLayout.vue'

const route    = useRoute()
const auth     = useAuthStore()
const ws       = useWebSocketStore()
const settings = useSettingsStore()

const layout = computed(() => route.meta.public ? PlainLayout : AppLayout)
const instanceName = (import.meta.env.VITE_INSTANCE_NAME || '').trim()
const instanceColor = (import.meta.env.VITE_INSTANCE_COLOR || 'amber').trim().toLowerCase()
const showRuntimeStrip = computed(() => !!instanceName)

const runtimeStripText = computed(() => showRuntimeStrip.value ? `Instanz: ${instanceName}` : '')

const runtimeStripClass = computed(() => {
  const base = 'w-full py-1 px-3 text-center text-xs font-semibold tracking-wide text-white'
  if (instanceColor === 'red') return `${base} bg-red-700`
  if (instanceColor === 'green' || instanceColor === 'emerald') return `${base} bg-emerald-700`
  if (instanceColor === 'blue') return `${base} bg-blue-700`
  if (instanceColor === 'orange' || instanceColor === 'amber') return `${base} bg-amber-700`
  return `${base} bg-slate-700`
})

onMounted(async () => {
  if (auth.isLoggedIn) {
    // Open the WebSocket first — it must not wait behind the loadMe/settings
    // round-trips, otherwise live pushes that arrive during the handshake gap
    // are lost (the server does not replay missed events).
    ws.connect()
    await auth.loadMe()
    await settings.load()
  }
})

// Keep system theme in sync when OS preference changes
const mql = window.matchMedia('(prefers-color-scheme: dark)')
function onSystemThemeChange() {
  if (settings.theme === 'system') settings.applyTheme()
}
mql.addEventListener('change', onSystemThemeChange)
onUnmounted(() => mql.removeEventListener('change', onSystemThemeChange))
</script>
