<template>
  <div class="min-h-screen">
    <div v-if="showRuntimeStrip" :class="runtimeStripClass">
      {{ runtimeStripText }}
    </div>
    <component :is="layout" :style="contentAreaStyle">
      <router-view />
    </component>
    <HelpDrawer />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { useSettingsStore } from '@/stores/settings'
import { useHelpStore } from '@/stores/help'
import AppLayout from '@/components/layout/AppLayout.vue'
import PlainLayout from '@/components/layout/PlainLayout.vue'
import HelpDrawer from '@/components/ui/HelpDrawer.vue'

const route    = useRoute()
const auth     = useAuthStore()
const ws       = useWebSocketStore()
const settings = useSettingsStore()
const help     = useHelpStore()

const layout = computed(() => route.meta.public ? PlainLayout : AppLayout)
const instanceName = (import.meta.env.VITE_INSTANCE_NAME || '').trim()
const instanceColor = (import.meta.env.VITE_INSTANCE_COLOR || 'amber').trim().toLowerCase()
const showRuntimeStrip = computed(() => !!instanceName)

const runtimeStripText = computed(() => showRuntimeStrip.value ? `Instanz: ${instanceName}` : '')

// Reserve space for the help drawer instead of letting it float on top of
// page content (issue feedback: fields near the right edge, e.g. in Settings,
// disappeared behind the drawer). AppLayout's/PlainLayout's single root
// element receives this via Vue's automatic non-prop-attribute fallthrough.
const contentAreaStyle = computed(() => ({
  // Mirrors HelpDrawer.vue's own `maxWidth: '90vw'` via CSS min() rather than
  // clamping help.drawerWidth in JS — a narrow viewport after the drawer was
  // last resized wide (drawerWidth is persisted) would otherwise reserve more
  // margin than the drawer actually renders at, and CSS min() stays correct
  // across later viewport resizes with no resize-event listener needed.
  marginRight: help.isOpen ? `min(${help.drawerWidth}px, 90vw)` : '0px',
  transition: 'margin-right 200ms ease',
}))

const runtimeStripClass = computed(() => {
  const base = 'w-full py-1 px-3 text-center text-xs font-semibold tracking-wide text-white'
  if (instanceColor === 'red') return `${base} bg-red-700`
  if (instanceColor === 'green' || instanceColor === 'emerald') return `${base} bg-emerald-700`
  if (instanceColor === 'blue') return `${base} bg-blue-700`
  if (instanceColor === 'orange' || instanceColor === 'amber') return `${base} bg-amber-700`
  return `${base} bg-slate-700`
})

onMounted(async () => {
  // Public, unauthenticated static content — prefetch regardless of login
  // state so the first "?" click doesn't wait on a round-trip.
  help.loadIndex()

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
