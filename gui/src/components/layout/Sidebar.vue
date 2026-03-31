<template>
  <aside
    :class="[
      'flex flex-col bg-surface-800 border-r border-slate-200 dark:border-slate-700/60 transition-all duration-300 shrink-0',
      collapsed ? 'w-16' : 'w-56'
    ]"
  >
    <!-- Logo -->
    <div class="flex items-center gap-3 px-4 py-5 border-b border-slate-200 dark:border-slate-700/60">
      <!-- openTWS icon (inline SVG) -->
      <svg class="shrink-0 w-8 h-8 rounded-lg" viewBox="0 0 400 400" xmlns="http://www.w3.org/2000/svg">
        <rect width="400" height="400" fill="#0F1320"/>
        <rect x="20" y="20" width="360" height="360" rx="48" fill="#1A1F2E"/>
        <line x1="20"  y1="110" x2="380" y2="110" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="20"  y1="200" x2="380" y2="200" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="20"  y1="290" x2="380" y2="290" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="110" y1="20"  x2="110" y2="380" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="200" y1="20"  x2="200" y2="380" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <line x1="290" y1="20"  x2="290" y2="380" stroke="#2E3650" stroke-width="0.5" opacity="0.4"/>
        <rect x="20" y="20" width="360" height="360" rx="48" fill="none" stroke="#2E3650" stroke-width="2.5"/>
        <path d="M46 20 L20 20 L20 46"    fill="none" stroke="#1DCFAB" stroke-width="4" stroke-linecap="round"/>
        <path d="M354 20 L380 20 L380 46"  fill="none" stroke="#1DCFAB" stroke-width="4" stroke-linecap="round"/>
        <path d="M46 380 L20 380 L20 354"  fill="none" stroke="#1DCFAB" stroke-width="4" stroke-linecap="round"/>
        <path d="M354 380 L380 380 L380 354" fill="none" stroke="#1DCFAB" stroke-width="4" stroke-linecap="round"/>
        <circle cx="200" cy="200" r="44" fill="#1A1F2E" stroke="#1DCFAB" stroke-width="2"/>
        <circle cx="200" cy="200" r="34" fill="#1DCFAB" opacity="0.08"/>
        <circle cx="200" cy="200" r="20" fill="none" stroke="#1DCFAB" stroke-width="2" opacity="0.5"/>
        <circle cx="200" cy="200" r="11" fill="#1DCFAB" opacity="0.95"/>
        <circle cx="100" cy="100" r="26" fill="#1A1F2E" stroke="#F5A623" stroke-width="2"/>
        <circle cx="100" cy="100" r="13" fill="#F5A623" opacity="0.95"/>
        <circle cx="300" cy="100" r="26" fill="#1A1F2E" stroke="#5B9CF6" stroke-width="2"/>
        <circle cx="300" cy="100" r="13" fill="#5B9CF6" opacity="0.95"/>
        <circle cx="200" cy="316" r="26" fill="#1A1F2E" stroke="#F5A623" stroke-width="2" stroke-dasharray="4 3"/>
        <circle cx="200" cy="316" r="13" fill="#F5A623" opacity="0.65"/>
        <line x1="119" y1="117" x2="170" y2="168" stroke="#F5A623" stroke-width="1.8" opacity="0.7"/>
        <line x1="281" y1="117" x2="230" y2="168" stroke="#5B9CF6" stroke-width="1.8" opacity="0.7"/>
        <line x1="200" y1="290" x2="200" y2="245" stroke="#F5A623" stroke-width="1.8" opacity="0.7"/>
      </svg>
      <span v-if="!collapsed" class="font-bold text-slate-800 dark:text-slate-100 tracking-tight">openTWS</span>
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
            ? 'bg-blue-600/20 text-blue-600 dark:text-blue-400'
            : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 hover:text-slate-800 dark:hover:text-slate-100'
        ]"
      >
        <span class="shrink-0 text-lg w-5 text-center" v-html="item.icon" />
        <span v-if="!collapsed" class="truncate">{{ item.label }}</span>
      </RouterLink>
    </nav>

    <!-- Bottom: WS status + collapse toggle -->
    <div class="px-2 py-3 border-t border-slate-200 dark:border-slate-700/60 flex flex-col gap-2">
      <!-- WebSocket indicator -->
      <div :class="['flex items-center gap-2 px-3 py-2 rounded-lg text-xs', collapsed ? 'justify-center' : '']" :title="ws.connected ? 'Live verbunden' : 'Getrennt'">
        <span :class="['w-2 h-2 rounded-full shrink-0', ws.connected ? 'bg-green-400 animate-pulse' : 'bg-red-500']" />
        <span v-if="!collapsed" :class="ws.connected ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'">
          {{ ws.connected ? 'Live' : 'Offline' }}
        </span>
      </div>
      <!-- Collapse button -->
      <button @click="$emit('toggle')" class="btn-ghost w-full justify-center text-slate-400 dark:text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 py-2">
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
  { to: '/logic',      label: 'Logic Engine', icon: '<svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18" style="display:inline-block;vertical-align:middle"><circle cx="4" cy="7" r="2"/><circle cx="4" cy="13" r="2"/><circle cx="16" cy="10" r="2.5"/><line x1="6" y1="7.5" x2="13.5" y2="9.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="6" y1="12.5" x2="13.5" y2="10.7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>' },
  { to: '/settings',   label: 'Einstellungen', icon: '&#9881;' },
]

function isActive(to) {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>
