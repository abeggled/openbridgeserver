<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { auth, setJwt } from '@/api/client'
import { useVisuStore } from '@/stores/visu'
import { useWebSocket } from '@/composables/useWebSocket'

const router = useRouter()
const route  = useRoute()
const store  = useVisuStore()
const ws     = useWebSocket()

const username = ref('')
const password = ref('')
const error    = ref('')
const loading  = ref(false)

async function login() {
  if (!username.value || !password.value || loading.value) return
  error.value = ''
  loading.value = true
  try {
    const res = await auth.login(username.value, password.value)
    setJwt(res.access_token)
    store.login(res.access_token)
    ws.connect()
    // Zurück zur ursprünglichen Seite oder zur Übersicht
    const redirect = route.query.redirect as string | undefined
    router.push(redirect ?? { name: 'tree' })
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Login fehlgeschlagen'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
    <div class="w-80 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-2xl p-8 shadow-2xl">
      <!-- Logo -->
      <div class="text-center mb-8">
        <div class="text-3xl font-bold text-blue-500 dark:text-blue-400 mb-1">openTWS</div>
        <div class="text-sm text-gray-400 dark:text-gray-500">Visualisierung · Anmeldung</div>
      </div>

      <form @submit.prevent="login" class="space-y-4">
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Benutzername</label>
          <input
            v-model="username"
            type="text"
            autocomplete="username"
            autofocus
            class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 transition-colors"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Passwort</label>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 transition-colors"
          />
        </div>

        <p v-if="error" class="text-red-500 dark:text-red-400 text-sm text-center">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading || !username || !password"
          class="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-lg py-2.5 transition-colors"
        >
          {{ loading ? 'Anmelden …' : 'Anmelden' }}
        </button>
      </form>

      <button
        class="mt-4 w-full text-sm text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
        @click="router.push({ name: 'tree' })"
      >
        ← Zur Übersicht (ohne Login)
      </button>
    </div>
  </div>
</template>
