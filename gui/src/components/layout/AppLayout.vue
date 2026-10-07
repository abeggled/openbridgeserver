<template>
  <div class="flex h-screen overflow-hidden bg-surface-900">
    <!-- Sidebar -->
    <Sidebar :collapsed="sidebarCollapsed" @toggle="sidebarCollapsed = !sidebarCollapsed" />

    <!-- Main -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <TopBar @toggle-sidebar="sidebarCollapsed = !sidebarCollapsed" />
      <main class="flex-1 overflow-y-auto p-6">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import Sidebar from './Sidebar.vue'
import TopBar  from './TopBar.vue'
import { useKnxProjectStore } from '@/stores/knxProject'
import { useAuthStore } from '@/stores/auth'

const sidebarCollapsed = ref(false)

// Load the KNX project's group address style once per session, before a view shows addresses (#1296).
// Only when logged in: this layout also renders before the router knows a public route, and a 401
// there would send the login page into a reload loop.
const knxProject = useKnxProjectStore()
const auth = useAuthStore()
onMounted(() => {
  if (auth.isLoggedIn) knxProject.load()
})
</script>
