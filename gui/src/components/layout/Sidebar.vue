<template>
  <aside
    :class="[
      'flex flex-col bg-surface-800 border-r border-slate-200 dark:border-slate-700/60 transition-all duration-300 shrink-0',
      collapsed ? 'w-16' : 'w-56'
    ]"
  >
    <!-- Logo -->
    <div class="flex items-center gap-3 px-4 py-5 border-b border-slate-200 dark:border-slate-700/60">
      <!-- open bridge server icon (inline SVG) -->
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" class="shrink-0 w-8 h-8 rounded-lg">
        <rect width="64" height="64" rx="14" fill="#085041"/>
        <rect x="6" y="42" width="52" height="4" rx="2" fill="#9FE1CB"/>
        <rect x="14" y="14" width="4" height="30" rx="2" fill="#9FE1CB"/>
        <rect x="46" y="14" width="4" height="30" rx="2" fill="#9FE1CB"/>
        <line x1="16" y1="14" x2="6"  y2="44" stroke="#9FE1CB" stroke-width="2.2" stroke-linecap="round"/>
        <line x1="16" y1="14" x2="32" y2="44" stroke="#9FE1CB" stroke-width="2.2" stroke-linecap="round"/>
        <line x1="48" y1="14" x2="32" y2="44" stroke="#9FE1CB" stroke-width="2.2" stroke-linecap="round"/>
        <line x1="48" y1="14" x2="58" y2="44" stroke="#9FE1CB" stroke-width="2.2" stroke-linecap="round"/>
        <line x1="22" y1="22" x2="22" y2="42" stroke="#9FE1CB" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
        <line x1="28" y1="16" x2="28" y2="42" stroke="#9FE1CB" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
        <line x1="36" y1="16" x2="36" y2="42" stroke="#9FE1CB" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
        <line x1="42" y1="22" x2="42" y2="42" stroke="#9FE1CB" stroke-width="1.5" stroke-linecap="round" opacity="0.6"/>
      </svg>
      <span v-if="!collapsed" class="font-bold text-slate-800 dark:text-slate-100 tracking-tight">open bridge server</span>
    </div>

    <!-- Nav -->
    <nav class="flex-1 py-3 px-2 flex flex-col gap-0.5">
      <RouterLink
        v-for="item in navItems" :key="item.to"
        :to="item.to"
        :title="collapsed ? item.label : ''"
        :data-testid="'nav-' + (item.to === '/' ? 'home' : item.to.replace('/', ''))"
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

      <!-- Visu link + Custom Links (abgesetzt) -->
      <div class="mt-3 pt-3 border-t border-slate-200 dark:border-slate-700/60 flex flex-col gap-0.5">
        <a
          href="/visu/"
          target="_blank"
          rel="noopener"
          :title="collapsed ? 'Visu' : ''"
          class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 hover:text-slate-800 dark:hover:text-slate-100"
        >
          <span class="shrink-0 text-lg w-5 text-center">&#9707;</span>
          <span v-if="!collapsed" class="truncate">Visu</span>
        </a>
        <a
          v-for="link in navStore.links" :key="link.id"
          :href="link.url"
          :target="link.open_new_tab ? '_blank' : '_self'"
          rel="noopener noreferrer"
          :title="collapsed ? link.label : ''"
          :data-testid="'nav-custom-link-' + link.id"
          class="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 hover:text-slate-800 dark:hover:text-slate-100"
        >
          <span class="shrink-0 text-lg w-5 text-center"><VisuIcon :icon="link.icon || '🔗'" /></span>
          <span v-if="!collapsed" class="truncate">{{ link.label }}</span>
        </a>
      </div>
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
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useWebSocketStore } from '@/stores/websocket'
import { useNavLinksStore } from '@/stores/navLinks'
import { useAuthStore } from '@/stores/auth'
import VisuIcon from '@/components/ui/VisuIcon.vue'

defineProps({ collapsed: Boolean })
defineEmits(['toggle'])

const route    = useRoute()
const ws       = useWebSocketStore()
const navStore = useNavLinksStore()
const auth     = useAuthStore()

const navItems = [
  { to: '/',           label: 'Übersicht',    icon: '&#9783;' },
  { to: '/datapoints', label: 'Objekte',      icon: '&#9636;' },
  { to: '/adapters',   label: 'Adapter',      icon: '&#9741;' },
  { to: '/history',    label: 'Historie',     icon: '&#9685;' },
  { to: '/ringbuffer', label: 'Monitor',      icon: '&#9706;' },
  { to: '/logic',      label: 'Logikmodul',   icon: '<svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18" style="display:inline-block;vertical-align:middle"><circle cx="4" cy="7" r="2"/><circle cx="4" cy="13" r="2"/><circle cx="16" cy="10" r="2.5"/><line x1="6" y1="7.5" x2="13.5" y2="9.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="6" y1="12.5" x2="13.5" y2="10.7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>' },
  { to: '/settings',   label: 'Einstellungen', icon: '&#9881;' },
]

// Nur laden wenn bereits eingeloggt — sonst triggert der 401 den Interceptor-Redirect
onMounted(() => { if (auth.isLoggedIn && !navStore.links.length) navStore.load() })

function isActive(to) {
  if (to === '/') return route.path === '/'
  return route.path.startsWith(to)
}
</script>
