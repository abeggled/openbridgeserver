<template>
  <component :is="layout">
    <router-view />
  </component>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'
import { useSettingsStore } from '@/stores/settings'
import AppLayout from '@/components/layout/AppLayout.vue'
import PlainLayout from '@/components/layout/PlainLayout.vue'

const route    = useRoute()
const auth     = useAuthStore()
const ws       = useWebSocketStore()
const settings = useSettingsStore()

const layout = computed(() => route.meta.public ? PlainLayout : AppLayout)

onMounted(async () => {
  if (auth.isLoggedIn) {
    await auth.loadMe()
    await settings.load()
    ws.connect()
  }
})
</script>
