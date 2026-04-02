<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { getJwt, clearJwt } from '@/api/client'

const router    = useRouter()
const loggedIn  = computed(() => !!getJwt())

function toggle() {
  if (loggedIn.value) {
    clearJwt()
    router.push({ name: 'tree' })
  } else {
    router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
}
</script>

<template>
  <button
    class="text-xs px-2 py-1 rounded transition-colors"
    :class="loggedIn
      ? 'text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400'
      : 'text-gray-400 dark:text-gray-500 hover:text-blue-500 dark:hover:text-blue-400'"
    :title="loggedIn ? 'Abmelden' : 'Anmelden'"
    @click="toggle"
  >{{ loggedIn ? '🔓 Abmelden' : '🔑 Anmelden' }}</button>
</template>
