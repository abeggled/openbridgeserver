<template>
  <div class="w-full max-w-md">
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
        <h1 class="text-lg font-semibold mb-1">{{ $t('setup.title') }}</h1>
        <p class="text-sm text-slate-400 mb-4">{{ $t('setup.intro') }}</p>

        <form @submit.prevent="submit" class="flex flex-col gap-4">
          <div class="form-group">
            <label class="label">{{ $t('setup.username') }}</label>
            <input v-model="form.username" type="text" class="input" autocomplete="username"
                   required autofocus data-testid="setup-username" />
          </div>

          <div class="form-group">
            <label class="label">{{ $t('setup.password') }}</label>
            <input v-model="form.password" type="password" class="input" placeholder="••••••••"
                   autocomplete="new-password" required data-testid="setup-password" />
            <p class="text-xs text-slate-500 mt-1">{{ $t('setup.passwordHint', { n: MIN_PASSWORD_LENGTH }) }}</p>
          </div>

          <div class="form-group">
            <label class="label">{{ $t('setup.passwordRepeat') }}</label>
            <input v-model="form.passwordRepeat" type="password" class="input" placeholder="••••••••"
                   autocomplete="new-password" required data-testid="setup-password-repeat" />
          </div>

          <div v-if="error" class="flex items-center gap-2 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400" data-testid="setup-error">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            {{ error }}
          </div>

          <button type="submit" class="btn-primary w-full justify-center py-2.5" :disabled="loading" data-testid="setup-submit">
            <Spinner v-if="loading" size="sm" color="white" />
            <span>{{ loading ? $t('setup.submitting') : $t('setup.submit') }}</span>
          </button>
        </form>

        <p class="text-xs text-slate-500 mt-4">{{ $t('setup.securityNote') }}</p>
      </div>
    </div>

    <p class="text-center text-xs text-slate-600 mt-6">open bridge server {{ appVersion }} · MIT License</p>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { setupApi } from '@/api/client'
import { markSetupComplete } from '@/utils/setupStatus'
import Spinner from '@/components/ui/Spinner.vue'

// Mirrors MIN_PASSWORD_LENGTH in obs/api/setup.py — the backend rejects shorter
// passwords with a 422 that would only be readable as a validation dump.
const MIN_PASSWORD_LENGTH = 8

const appVersion = __APP_VERSION__

const { t } = useI18n()
const router = useRouter()

const form = reactive({ username: 'admin', password: '', passwordRepeat: '' })
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  const username = form.username.trim()
  if (!username) {
    error.value = t('setup.errorUsernameRequired')
    return
  }
  if (form.password.length < MIN_PASSWORD_LENGTH) {
    error.value = t('setup.errorPasswordTooShort', { n: MIN_PASSWORD_LENGTH })
    return
  }
  if (form.password !== form.passwordRepeat) {
    error.value = t('setup.errorPasswordMismatch')
    return
  }

  loading.value = true
  try {
    await setupApi.createOwner(username, form.password)
    markSetupComplete()
    router.push({ name: 'Login' })
  } catch (e) {
    error.value = e?.response?.status === 409 ? t('setup.errorAlreadyDone') : t('setup.errorFailed')
  } finally {
    loading.value = false
  }
}
</script>
