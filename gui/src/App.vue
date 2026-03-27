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
import AppLayout from '@/components/layout/AppLayout.vue'
import PlainLayout from '@/components/layout/PlainLayout.vue'

const route = useRoute()
const auth  = useAuthStore()
const ws    = useWebSocketStore()

const layout = computed(() => route.meta.public ? PlainLayout : AppLayout)

onMounted(async () => {
  if (auth.isLoggedIn) {
    await auth.loadMe()
    ws.connect()
  }
})
</script>
