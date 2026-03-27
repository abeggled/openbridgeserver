<template>
  <div class="w-full max-w-sm">
    <!-- Logo + heading -->
    <div class="text-center mb-8">
      <div class="inline-flex items-center justify-center w-14 h-14 bg-blue-600 rounded-2xl mb-4 shadow-lg shadow-blue-600/30">
        <svg class="w-8 h-8 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M12 5l7 7-7 7"/>
        </svg>
      </div>
      <h1 class="text-2xl font-bold text-slate-100">OpenTWS</h1>
      <p class="text-sm text-slate-500 mt-1">Multiprotocol Server</p>
    </div>

    <!-- Card -->
    <div class="card shadow-2xl">
      <div class="card-body">
        <form @submit.prevent="submit" class="flex flex-col gap-4">
          <div class="form-group">
            <label class="label">Benutzername</label>
            <input v-model="form.username" type="text" class="input" placeholder="admin" autocomplete="username" required autofocus />
          </div>

          <div class="form-group">
            <label class="label">Passwort</label>
            <input v-model="form.password" type="password" class="input" placeholder="••••••••" autocomplete="current-password" required />
          </div>

          <div v-if="auth.error" class="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            {{ auth.error }}
          </div>

          <button type="submit" class="btn-primary w-full justify-center py-2.5" :disabled="auth.loading">
            <Spinner v-if="auth.loading" size="sm" color="white" />
            <span>{{ auth.loading ? 'Anmelden …' : 'Anmelden' }}</span>
          </button>
        </form>
      </div>
    </div>

    <p class="text-center text-xs text-slate-600 mt-6">OpenTWS v0.1.0 · MIT License</p>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import Spinner from '@/components/ui/Spinner.vue'

const auth   = useAuthStore()
const ws     = useWebSocketStore()
const router = useRouter()

const form = reactive({ username: '', password: '' })

async function submit() {
  const ok = await auth.login(form.username, form.password)
  if (ok) {
    ws.connect()
    router.push('/')
  }
}
</script>
