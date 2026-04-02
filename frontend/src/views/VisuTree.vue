<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useThemeStore } from '@/stores/theme'
import type { VisuNode } from '@/types'
import { storeToRefs } from 'pinia'
import AuthButton from '@/components/AuthButton.vue'

const store = useVisuStore()
const { rootNodes, isLoggedIn } = storeToRefs(store)
const router = useRouter()
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    await store.loadTree()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : 'Fehler beim Laden'
  } finally {
    loading.value = false
  }
})

function navigate(node: VisuNode) {
  router.push({ name: 'viewer', params: { id: node.id } })
}
</script>

<template>
  <div class="min-h-screen bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100">
    <!-- Header -->
    <header class="border-b border-gray-200 dark:border-gray-800 px-6 py-4 flex items-center justify-between bg-gray-50 dark:bg-gray-900">
      <div class="flex items-center gap-3">
        <span class="text-xl font-bold text-blue-500 dark:text-blue-400">openTWS</span>
        <span class="text-gray-300 dark:text-gray-600">|</span>
        <span class="text-gray-600 dark:text-gray-300">Visualisierung</span>
      </div>
      <div class="flex items-center gap-3">
        <!-- Hell/Dunkel-Umschalter -->
        <button
          class="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors px-2 py-1 rounded"
          @click="useThemeStore().toggle()"
          :title="useThemeStore().isDark ? 'Heller Modus' : 'Dunkler Modus'"
        >{{ useThemeStore().isDark ? '☀️' : '🌙' }}</button>
        <AuthButton />
      </div>
    </header>

    <!-- Content -->
    <main class="max-w-5xl mx-auto px-6 py-8">
      <h2 class="text-xl font-semibold mb-6">Übersicht</h2>

      <div v-if="loading" class="text-center text-gray-400 dark:text-gray-500 py-16">Lade …</div>

      <div v-else-if="error" class="text-red-500 dark:text-red-400 text-center py-16">{{ error }}</div>

      <div v-else-if="rootNodes.length === 0" class="text-gray-400 dark:text-gray-500 text-center py-16">
        Noch keine Seiten vorhanden.<br />
        <span v-if="isLoggedIn" class="text-sm">
          <router-link class="text-blue-500 dark:text-blue-400 hover:underline" to="/editor/new">
            Erste Seite erstellen
          </router-link>
        </span>
      </div>

      <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
        <button
          v-for="node in rootNodes"
          :key="node.id"
          class="flex flex-col items-center justify-center gap-3 p-6 rounded-xl bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:border-blue-500 transition-all group"
          @click="navigate(node)"
        >
          <span class="text-4xl">{{ node.icon ?? (node.type === 'PAGE' ? '📄' : '🏠') }}</span>
          <span class="text-sm font-medium text-gray-700 dark:text-gray-200 text-center leading-tight group-hover:text-gray-900 dark:group-hover:text-white">
            {{ node.name }}
          </span>
          <span
            v-if="node.access === 'readonly'"
            class="text-xs text-blue-500 dark:text-blue-400 flex items-center gap-1"
          >👁 Nur ansehen</span>
          <span
            v-else-if="node.access === 'protected'"
            class="text-xs text-yellow-600 dark:text-yellow-500 flex items-center gap-1"
          >🔐 PIN</span>
          <span
            v-else-if="node.access === 'private'"
            class="text-xs text-red-500 dark:text-red-400 flex items-center gap-1"
          >🔑 Login</span>
        </button>
      </div>
    </main>
  </div>
</template>
