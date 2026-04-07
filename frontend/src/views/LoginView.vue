<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { auth, setJwt } from '@/api/client'
import { useVisuStore } from '@/stores/visu'
import { useWebSocket } from '@/composables/useWebSocket'
import { useThemeStore } from '@/stores/theme'

const router = useRouter()
const route  = useRoute()
const store  = useVisuStore()
const ws     = useWebSocket()
const theme  = useThemeStore()

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
    await store.login(res.access_token)
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
        <!-- Dark logo -->
        <svg v-if="theme.isDark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 64" class="w-full mb-1 rounded-lg" style="background:#111111">
          <rect x="0" y="38" width="62" height="3.5" rx="1.75" fill="#5DCAA5"/>
          <rect x="10" y="10" width="3.5" height="32" rx="1.75" fill="#5DCAA5"/>
          <rect x="48.5" y="10" width="3.5" height="32" rx="1.75" fill="#5DCAA5"/>
          <line x1="11.75" y1="10" x2="0"  y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
          <line x1="11.75" y1="10" x2="31" y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
          <line x1="50.25" y1="10" x2="31" y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
          <line x1="50.25" y1="10" x2="62" y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
          <line x1="18" y1="19" x2="18" y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="25" y1="13" x2="25" y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="37" y1="13" x2="37" y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="44" y1="19" x2="44" y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <text x="80" y="30" font-family="'DM Mono', monospace" font-size="28" font-weight="500" letter-spacing="-0.4" fill="#f0eeea">open bridge</text>
          <text x="81" y="48" font-family="'DM Mono', monospace" font-size="9.5" font-weight="300" letter-spacing="2.8" fill="#888780">MULTIPROTOCOL · AI SERVER</text>
        </svg>
        <!-- Light logo -->
        <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 64" class="w-full mb-1">
          <rect x="0" y="38" width="62" height="3.5" rx="1.75" fill="#0F6E56"/>
          <rect x="10" y="10" width="3.5" height="32" rx="1.75" fill="#0F6E56"/>
          <rect x="48.5" y="10" width="3.5" height="32" rx="1.75" fill="#0F6E56"/>
          <line x1="11.75" y1="10" x2="0"  y2="40" stroke="#0F6E56" stroke-width="2" stroke-linecap="round"/>
          <line x1="11.75" y1="10" x2="31" y2="40" stroke="#0F6E56" stroke-width="2" stroke-linecap="round"/>
          <line x1="50.25" y1="10" x2="31" y2="40" stroke="#0F6E56" stroke-width="2" stroke-linecap="round"/>
          <line x1="50.25" y1="10" x2="62" y2="40" stroke="#0F6E56" stroke-width="2" stroke-linecap="round"/>
          <line x1="18" y1="19" x2="18" y2="38" stroke="#0F6E56" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="25" y1="13" x2="25" y2="38" stroke="#0F6E56" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="37" y1="13" x2="37" y2="38" stroke="#0F6E56" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <line x1="44" y1="19" x2="44" y2="38" stroke="#0F6E56" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
          <text x="80" y="30" font-family="'DM Mono', monospace" font-size="28" font-weight="500" letter-spacing="-0.4" fill="#1a1a18">open bridge</text>
          <text x="81" y="48" font-family="'DM Mono', monospace" font-size="9.5" font-weight="300" letter-spacing="2.8" fill="#5F5E5A">MULTIPROTOCOL · AI SERVER</text>
        </svg>
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
