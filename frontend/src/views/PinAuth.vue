<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'

const props = defineProps<{ id: string }>()
const { t } = useI18n()
const router = useRouter()
const store = useVisuStore()

const pin = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  if (!pin.value || loading.value) return
  error.value = ''
  loading.value = true
  try {
    await store.authenticatePin(props.id, pin.value)
    // Erfolg → zurück zur Seite
    router.push({ name: 'viewer', params: { id: props.id } })
  } catch {
    error.value = t('pin.wrongPin')
    pin.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-100 dark:bg-gray-950">
    <div class="w-80 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-2xl p-8 shadow-2xl">
      <div class="text-center mb-6">
        <span class="text-4xl">🔒</span>
        <h1 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mt-2">{{ $t('pin.title') }}</h1>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ $t('pin.subtitle') }}</p>
      </div>

      <form @submit.prevent="submit" class="space-y-4">
        <input
          v-model="pin"
          type="password"
          inputmode="numeric"
          :placeholder="$t('login.pin')"
          maxlength="16"
          autofocus
          class="w-full bg-gray-100 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-4 py-3 text-center text-xl tracking-widest text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
        />

        <p v-if="error" class="text-red-500 dark:text-red-400 text-sm text-center">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading || !pin"
          class="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-lg py-3 transition-colors"
        >
          {{ loading ? $t('pin.checking') : $t('common.confirm') }}
        </button>
      </form>

      <button
        class="mt-4 w-full text-sm text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
        @click="router.push({ name: 'tree' })"
      >
        {{ $t('common.back') }}
      </button>
    </div>
  </div>
</template>
