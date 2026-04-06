<template>
  <div class="w-full max-w-sm">
    <!-- Logo + heading -->
    <div class="text-center mb-8">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 64" class="mx-auto rounded-lg" style="width:280px;height:auto;background:#111111">
        <!-- Hängebrücke Mark -->
        <rect x="0" y="38" width="62" height="3.5" rx="1.75" fill="#5DCAA5"/>
        <rect x="10" y="10" width="3.5" height="32" rx="1.75" fill="#5DCAA5"/>
        <rect x="48.5" y="10" width="3.5" height="32" rx="1.75" fill="#5DCAA5"/>
        <line x1="11.75" y1="10" x2="0"    y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
        <line x1="11.75" y1="10" x2="31"   y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
        <line x1="50.25" y1="10" x2="31"   y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
        <line x1="50.25" y1="10" x2="62"   y2="40" stroke="#5DCAA5" stroke-width="2" stroke-linecap="round"/>
        <line x1="18"  y1="19" x2="18"  y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
        <line x1="25"  y1="13" x2="25"  y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
        <line x1="37"  y1="13" x2="37"  y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
        <line x1="44"  y1="19" x2="44"  y2="38" stroke="#5DCAA5" stroke-width="1.2" stroke-linecap="round" opacity="0.65"/>
        <!-- Wordmark -->
        <text x="80" y="30"
          font-family="'DM Mono', monospace"
          font-size="28"
          font-weight="500"
          letter-spacing="-0.4"
          fill="#f0eeea">open bridge</text>
        <text x="81" y="48"
          font-family="'DM Mono', monospace"
          font-size="9.5"
          font-weight="300"
          letter-spacing="2.8"
          fill="#888780">MULTIPROTOCOL · AI SERVER</text>
      </svg>
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

    <p class="text-center text-xs text-slate-600 mt-6">open bridge server v0.1.0 · MIT License</p>
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
