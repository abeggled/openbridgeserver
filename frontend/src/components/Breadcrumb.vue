<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useVisuStore } from '@/stores/visu'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import type { VisuNode } from '@/types'

const store = useVisuStore()
const { breadcrumb, isLoggedIn } = storeToRefs(store)
const router = useRouter()

// Letzter Knoten im Breadcrumb
const lastNode = computed(() => breadcrumb.value[breadcrumb.value.length - 1] ?? null)

// Kinder des letzten Knotens (gefiltert: user-geschützte nur für angemeldete Benutzer)
const children = computed<VisuNode[]>(() => {
  if (!lastNode.value) return []
  return store.getChildren(lastNode.value.id).filter(n => {
    if (n.access === 'user') return isLoggedIn.value
    return true
  })
})

// Dropdown-State
const open = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)

function toggle() { open.value = !open.value }

function navigate(node: VisuNode) {
  open.value = false
  router.push({ name: 'viewer', params: { id: node.id } })
}

// Klick außerhalb schließt Dropdown
function onClickOutside(e: MouseEvent) {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target as Node)) {
    open.value = false
  }
}
onMounted(() => document.addEventListener('mousedown', onClickOutside))
onUnmounted(() => document.removeEventListener('mousedown', onClickOutside))

// Badge-Icon für Zugangslevel
function accessIcon(node: VisuNode): string {
  if (node.access === 'readonly')  return '👁'
  if (node.access === 'protected') return '🔐'
  if (node.access === 'user')      return '👤'
  return ''
}
</script>

<template>
  <nav v-if="breadcrumb.length > 0" class="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400 flex-wrap">
    <!-- Haus-Icon -->
    <button
      class="hover:text-gray-900 dark:hover:text-gray-200 transition-colors flex items-center"
      title="Startseite"
      @click="router.push({ name: 'tree' })"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 20 20" fill="currentColor">
        <path d="M10 2L2 9h2v9h5v-5h2v5h5V9h2L10 2z"/>
      </svg>
    </button>

    <!-- Breadcrumb-Pfad -->
    <template v-for="(node, idx) in breadcrumb" :key="node.id">
      <span class="text-gray-300 dark:text-gray-600">/</span>

      <span class="flex items-center gap-0.5">
        <!-- Node-Name -->
        <button
          class="transition-colors truncate max-w-[200px]"
          :class="idx === breadcrumb.length - 1
            ? 'text-gray-800 dark:text-gray-200 font-medium pointer-events-none'
            : 'hover:text-gray-900 dark:hover:text-gray-200'"
          @click="router.push({ name: 'viewer', params: { id: node.id } })"
        >{{ node.name }}</button>

        <!-- Dropdown-Chevron beim letzten Node (nur wenn Kinder vorhanden) -->
        <span
          v-if="idx === breadcrumb.length - 1 && children.length > 0"
          ref="dropdownRef"
          class="relative"
        >
          <button
            class="px-1 text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 transition-colors rounded"
            :class="{ 'text-blue-500 dark:text-blue-400': open }"
            :title="`${children.length} Unterseite${children.length !== 1 ? 'n' : ''}`"
            @click="toggle"
          >
            <span class="text-xs" :class="open ? 'inline-block rotate-180' : 'inline-block'">▾</span>
          </button>

          <!-- Dropdown -->
          <div
            v-show="open"
            class="absolute left-0 top-full mt-1 z-50 min-w-[180px] bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1 text-sm"
          >
            <button
              v-for="child in children"
              :key="child.id"
              class="w-full text-left flex items-center gap-2 px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-gray-700 dark:text-gray-300"
              @click="navigate(child)"
            >
              <span>{{ child.icon ?? (child.type === 'PAGE' ? '📄' : '📁') }}</span>
              <span class="truncate flex-1">{{ child.name }}</span>
              <span v-if="accessIcon(child)" class="text-xs opacity-60">{{ accessIcon(child) }}</span>
            </button>
          </div>
        </span>
      </span>
    </template>
  </nav>
</template>
