<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'

const props = defineProps<{ id: string }>()
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
    error.value = 'Falscher PIN. Bitte erneut versuchen.'
    pin.value = ''
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gray-950">
    <div class="w-80 bg-gray-900 border border-gray-700 rounded-2xl p-8 shadow-2xl">
      <div class="text-center mb-6">
        <span class="text-4xl">🔒</span>
        <h1 class="text-lg font-semibold text-gray-100 mt-2">Zugang gesichert</h1>
        <p class="text-sm text-gray-400 mt-1">Bitte PIN eingeben</p>
      </div>

      <form @submit.prevent="submit" class="space-y-4">
        <input
          v-model="pin"
          type="password"
          inputmode="numeric"
          placeholder="PIN"
          maxlength="16"
          autofocus
          class="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-3 text-center text-xl tracking-widest text-gray-100 focus:outline-none focus:border-blue-500"
        />

        <p v-if="error" class="text-red-400 text-sm text-center">{{ error }}</p>

        <button
          type="submit"
          :disabled="loading || !pin"
          class="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-lg py-3 transition-colors"
        >
          {{ loading ? 'Prüfe …' : 'Bestätigen' }}
        </button>
      </form>

      <button
        class="mt-4 w-full text-sm text-gray-500 hover:text-gray-300 transition-colors"
        @click="router.push({ name: 'tree' })"
      >
        ← Zurück
      </button>
    </div>
  </div>
</template>
