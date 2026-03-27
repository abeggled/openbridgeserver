<template>
  <aside
    :class="[
      'flex flex-col bg-surface-800 border-r border-slate-700/60 transition-all duration-300 shrink-0',
      collapsed ? 'w-16' : 'w-56'
    ]"
  >
    <!-- Logo -->
    <div class="flex items-center gap-3 px-4 py-5 border-b border-slate-700/60">
      <div class="shrink-0 w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
        <svg class="w-5 h-5 text-white" viewBox="0 0 20 20" fill="currentColor">
          <path d="M10 2L2 7l8 5 8-5-8-5zM2 13l8 5 8-5M2 10l8 5 8-5"/>
        </svg>
      </div>
      <span v-if="!collapsed" class="font-bold text-slate-100 tracking-tight">OpenTWS</span>
    </div>

    <!-- Nav -->
    <nav class="flex-1 py-3 px-2 flex flex-col gap-0.5">
      <RouterLink
        v-for="item in navItems" :key="item.to"
        :to="item.to"
        :title="collapsed ? item.label : ''"
        :class="[
          'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
          isActive(item.to)
            ? 'bg-blue-600/20 text-blue-400'
            : 'text-slate-400 hover:bg-slate-700/60 hover:text-slate-100'
        ]"
      >
        <span class="shrink-0 text-lg w-5 text-center" v-html="item.icon" />
        <span v-if="!collapsed" class="truncate">{{ item.label }}</span>
      </RouterLink>
    </nav>

    <!-- Bottom: WS status + collapse toggle -->
    <div class="px-2 py-3 border-t border-slate-700/60 flex flex-col gap-2">
      <!-- WebSocket indicator -->
      <div :class="['flex items-center gap-2 px-3 py-2 rounded-lg text-xs', collapsed ? 'justify-center' : '']" :title="ws.connected ? 'Live verbunden' : 'Getrennt'">
        <span :class="['w-2 h-2 rounded-full shrink-0', ws.connected ? 'bg-green-400 animate-pulse' : 'bg-red-500']" />
        <span v-if="!collapsed" :class="ws.connected ? 'text-green-400' : 'text-red-400'">
          {{ ws.connected ? 'Live' : 'Offline' }}
        </span>
      </div>
      <!-- Collapse button -->
      <button @click="$emit('toggle')" class="btn-ghost w-full justify-center text-slate-500 hover:text-slate-300 py-2">
        <svg class="w-4 h-4 transition-transform" :class="collapsed ? 'rotate-180' : ''" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M12.707 5.293a1 1 0 010 1.414L9.414 10l3.293 3.293a1 1 0 01-1.414 1.414l-4-4a1 1 0 010-1.414l4-4a1 1 0 011.414 0z"/>
        </svg>
      </button>
    </div>
  </aside>
</template>

<script setup>
import { useRoute } from 'vue-router'
import { useWebSocketStore } from '@/stores/websocket'

defineProps({ collapsed: Boolean })
defineEmits(['toggle'])

const route = useRoute()
const ws    = useWebSocketStore()

const navItems = [
  { to: '/',           label: 'Dashboard',   icon: '&#9783;' },
  { to: '/datapoints', label: 'DataPoints',  icon: '&#9636;' },
  { to: '/adapters',   label: 'Adapter',     icon: '&#9741;' },
  { to: '/history',    label: 'History',     icon: '&#9685;' },
  { to: '/ringbuffer', label: 'RingBuffer',  icon: '&#9706;' },
  { to: '/settings',   label: 'Einstellungen', icon: '&#9881;' },
]

function isActive(to) {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>
