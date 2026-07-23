<template>
  <div class="w-full max-w-sm">
    <!-- Logo + heading -->
    <div class="text-center mb-8">
      <img src="/obs_logo_light.svg" :alt="$t('login.logoAlt')"
           class="mx-auto block dark:hidden rounded-lg" style="width:280px;height:auto" />
      <img src="/obs_logo_dark.svg" :alt="$t('login.logoAlt')"
           class="mx-auto hidden dark:block rounded-lg" style="width:280px;height:auto" />
    </div>

    <!-- Card -->
    <div class="card shadow-2xl">
      <div class="card-body">
        <form @submit.prevent="submit" class="flex flex-col gap-4">
          <div class="form-group">
            <label class="label">{{ $t('login.username') }}</label>
            <input v-model="form.username" type="text" class="input" placeholder="admin" autocomplete="username" required autofocus data-testid="input-username" />
          </div>

          <div class="form-group">
            <label class="label">{{ $t('login.password') }}</label>
            <input v-model="form.password" type="password" class="input" placeholder="••••••••" autocomplete="current-password" required data-testid="input-password" />
          </div>

          <div v-if="auth.error" class="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            {{ auth.error }}
          </div>

          <button type="submit" class="btn-primary w-full justify-center py-2.5" :disabled="auth.loading" data-testid="btn-login">
            <Spinner v-if="auth.loading" size="sm" color="white" />
            <span>{{ auth.loading ? $t('login.submitting') : $t('login.submit') }}</span>
          </button>
        </form>
      </div>
    </div>

    <p class="text-center text-xs text-slate-600 mt-6">open bridge server {{ appVersion }} · MIT License</p>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import { useWebSocketStore } from '@/stores/websocket'
import Spinner from '@/components/ui/Spinner.vue'

const appVersion = __APP_VERSION__

const auth   = useAuthStore()
const settings = useSettingsStore()
const ws     = useWebSocketStore()
const router = useRouter()

const form = reactive({ username: '', password: '' })

async function submit() {
  const ok = await auth.login(form.username, form.password)
  if (ok) {
    ws.connect()
    await settings.load()
    router.push('/')
  }
}
</script>
