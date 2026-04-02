<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useWebSocket } from '@/composables/useWebSocket'

const router  = useRouter()
const store   = useVisuStore()
const ws      = useWebSocket()

const flash = ref<'idle' | 'out'>('idle')

async function logout() {
  store.logout()
  ws.disconnect()
  flash.value = 'out'
  // kurz warten damit der Nutzer die Bestätigung sieht, dann zur Startseite
  await new Promise(r => setTimeout(r, 1200))
  flash.value = 'idle'
  // Nur zur Startseite navigieren wenn man gerade auf einer geschützten Seite ist
  const current = router.currentRoute.value
  if (current.meta?.requiresAuth || current.name === 'editor' || current.name === 'manage') {
    router.push({ name: 'tree' })
  }
}

function login() {
  router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
}
</script>

<template>
  <!-- Bestätigung nach Logout -->
  <span
    v-if="flash === 'out'"
    class="text-xs text-green-600 dark:text-green-400 flex items-center gap-1 px-2 py-1"
  >✓ Abgemeldet</span>

  <!-- Eingeloggt -->
  <template v-else-if="store.isLoggedIn">
    <span class="hidden sm:flex items-center gap-1.5 text-xs text-green-600 dark:text-green-400">
      <span class="w-1.5 h-1.5 rounded-full bg-green-500 dark:bg-green-400 inline-block" />
      Angemeldet
    </span>
    <button
      class="text-xs text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400 transition-colors px-2 py-1 rounded"
      title="Abmelden"
      @click="logout"
    >Abmelden</button>
  </template>

  <!-- Nicht eingeloggt -->
  <button
    v-else
    class="text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 border border-blue-400/40 hover:border-blue-500 dark:hover:border-blue-400 px-3 py-1 rounded-lg transition-colors"
    title="Anmelden"
    @click="login"
  >🔑 Anmelden</button>
</template>
