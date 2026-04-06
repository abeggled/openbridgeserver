<template>
  <div class="w-full max-w-sm">
    <!-- Logo + heading -->
    <div class="text-center mb-8">
      <svg class="w-48 h-48 mx-auto rounded-2xl" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <style>
            .pt { font-family: 'Space Mono', monospace; font-size: 9px; font-weight: 700; letter-spacing: 1px; }
            .lm { font-family: 'Space Mono', monospace; font-size: 26px; font-weight: 700; fill: #FFFFFF; letter-spacing: 2px; }
            .ls { font-family: 'Space Mono', monospace; font-size: 11px; font-weight: 400; fill: #5B9CF6; letter-spacing: 3px; }
          </style>
        </defs>
        <rect width="400" height="400" fill="#0F1320"/>
        <rect x="80" y="30" width="240" height="240" rx="32" fill="#1A1F2E"/>
        <line x1="80"  y1="90"  x2="320" y2="90"  stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="80"  y1="150" x2="320" y2="150" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="80"  y1="210" x2="320" y2="210" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="140" y1="30"  x2="140" y2="270" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="200" y1="30"  x2="200" y2="270" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="260" y1="30"  x2="260" y2="270" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <rect x="80" y="30" width="240" height="240" rx="32" fill="none" stroke="#2E3650" stroke-width="2"/>
        <path d="M97 30 L80 30 L80 47"    fill="none" stroke="#1DCFAB" stroke-width="3" stroke-linecap="round"/>
        <path d="M303 30 L320 30 L320 47"  fill="none" stroke="#1DCFAB" stroke-width="3" stroke-linecap="round"/>
        <path d="M97 270 L80 270 L80 253"  fill="none" stroke="#1DCFAB" stroke-width="3" stroke-linecap="round"/>
        <path d="M303 270 L320 270 L320 253" fill="none" stroke="#1DCFAB" stroke-width="3" stroke-linecap="round"/>
        <circle cx="200" cy="150" r="30" fill="#1A1F2E" stroke="#1DCFAB" stroke-width="1.5"/>
        <circle cx="200" cy="150" r="23" fill="#1DCFAB" opacity="0.08"/>
        <circle cx="200" cy="150" r="14" fill="none" stroke="#1DCFAB" stroke-width="1.5" opacity="0.5"/>
        <circle cx="200" cy="150" r="8"  fill="#1DCFAB" opacity="0.9"/>
        <circle cx="122" cy="82" r="17" fill="#1A1F2E" stroke="#F5A623" stroke-width="1.5"/>
        <circle cx="122" cy="82" r="9"  fill="#F5A623" opacity="0.9"/>
        <circle cx="278" cy="82" r="17" fill="#1A1F2E" stroke="#5B9CF6" stroke-width="1.5"/>
        <circle cx="278" cy="82" r="9"  fill="#5B9CF6" opacity="0.9"/>
        <circle cx="200" cy="232" r="17" fill="#1A1F2E" stroke="#F5A623" stroke-width="1.5" stroke-dasharray="3 2"/>
        <circle cx="200" cy="232" r="9"  fill="#F5A623" opacity="0.6"/>
        <line x1="136" y1="91"  x2="180" y2="133" stroke="#F5A623" stroke-width="1.5" opacity="0.7"/>
        <line x1="264" y1="91"  x2="220" y2="133" stroke="#5B9CF6" stroke-width="1.5" opacity="0.7"/>
        <line x1="200" y1="215" x2="200" y2="181" stroke="#F5A623" stroke-width="1.5" opacity="0.7"/>
        <text x="122" y="78"  text-anchor="middle" class="pt" fill="#F5A623">KNX</text>
        <text x="278" y="78"  text-anchor="middle" class="pt" fill="#5B9CF6">MOD</text>
        <text x="200" y="155" text-anchor="middle" class="pt" fill="#1DCFAB">MQTT</text>
        <text x="200" y="308" text-anchor="middle" class="lm">open bridge server</text>
        <text x="200" y="332" text-anchor="middle" class="ls">MULTIPROTOCOL SERVER</text>
        <rect x="108" y="350" width="50" height="18" rx="5" fill="rgba(245,166,35,0.12)" stroke="#F5A623" stroke-width="0.8"/>
        <text x="133" y="363" text-anchor="middle" class="pt" fill="#F5A623">KNX</text>
        <rect x="166" y="350" width="68" height="18" rx="5" fill="rgba(91,156,246,0.12)" stroke="#5B9CF6" stroke-width="0.8"/>
        <text x="200" y="363" text-anchor="middle" class="pt" fill="#5B9CF6">MODBUS</text>
        <rect x="242" y="350" width="50" height="18" rx="5" fill="rgba(245,166,35,0.12)" stroke="#F5A623" stroke-width="0.8"/>
        <text x="267" y="363" text-anchor="middle" class="pt" fill="#F5A623">1-WIRE</text>
        <rect x="168" y="376" width="64" height="18" rx="5" fill="rgba(29,207,171,0.12)" stroke="#1DCFAB" stroke-width="0.8"/>
        <text x="200" y="389" text-anchor="middle" class="pt" fill="#1DCFAB">MQTT</text>
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
