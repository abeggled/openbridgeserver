<template>
  <header class="h-14 bg-surface-800 border-b border-slate-200 dark:border-slate-700/60 flex items-center px-4 gap-4 shrink-0">
    <!-- Sidebar toggle (mobile) -->
    <button @click="$emit('toggle-sidebar')" class="btn-icon lg:hidden">
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
      </svg>
    </button>

    <!-- Page title -->
    <h1 class="font-semibold text-slate-800 dark:text-slate-100 text-base flex-1">{{ pageTitle }}</h1>

    <!-- Version badge -->
    <span class="hidden sm:inline text-xs text-slate-400 dark:text-slate-500 font-mono">v{{ version }}</span>

    <!-- User menu -->
    <div class="relative" ref="menuRef">
      <button @click="menuOpen = !menuOpen" class="flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700/60 transition-colors">
        <div class="w-7 h-7 bg-blue-600/30 text-blue-600 dark:text-blue-400 rounded-full flex items-center justify-center text-xs font-bold">
          {{ auth.username.charAt(0).toUpperCase() }}
        </div>
        <span class="hidden sm:block text-sm text-slate-600 dark:text-slate-300">{{ auth.username }}</span>
        <svg class="w-4 h-4 text-slate-400 dark:text-slate-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
        </svg>
      </button>

      <!-- Dropdown -->
      <Transition enter-from-class="opacity-0 -translate-y-1" enter-active-class="transition-all duration-150" leave-to-class="opacity-0 -translate-y-1" leave-active-class="transition-all duration-150">
        <div v-if="menuOpen" class="absolute right-0 top-full mt-1 w-44 card shadow-xl z-50 py-1">
          <RouterLink to="/settings" @click="menuOpen = false" class="flex items-center gap-2 px-4 py-2 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700/60">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
            Einstellungen
          </RouterLink>
          <div class="border-t border-slate-200 dark:border-slate-700/60 my-1" />
          <button @click="logout" class="flex items-center gap-2 px-4 py-2 text-sm text-red-500 dark:text-red-400 hover:bg-slate-100 dark:hover:bg-slate-700/60 w-full">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
            Abmelden
          </button>
        </div>
      </Transition>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWebSocketStore } from '@/stores/websocket'

defineEmits(['toggle-sidebar'])

const route  = useRoute()
const router = useRouter()
const auth   = useAuthStore()
const ws     = useWebSocketStore()
const menuOpen  = ref(false)
const menuRef   = ref(null)
const version   = ref('0.1.0')

const titles = {
  'Dashboard':       'Dashboard',
  'DataPoints':      'DataPoints',
  'DataPointDetail': 'DataPoint Detail',
  'Adapters':        'Adapter',
  'History':         'History',
  'RingBuffer':      'RingBuffer',
  'Settings':        'Einstellungen',
}
const pageTitle = computed(() => titles[route.name] ?? 'openTWS')

function logout() {
  ws.disconnect()
  auth.logout()
  router.push('/login')
}

// Close menu on outside click
function onClickOutside(e) {
  if (menuRef.value && !menuRef.value.contains(e.target)) menuOpen.value = false
}
onMounted(()  => document.addEventListener('mousedown', onClickOutside))
onUnmounted(() => document.removeEventListener('mousedown', onClickOutside))
</script>
