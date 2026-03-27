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
import { ref } from 'vue'
import Sidebar from './Sidebar.vue'
import TopBar  from './TopBar.vue'

const sidebarCollapsed = ref(false)
</script>
