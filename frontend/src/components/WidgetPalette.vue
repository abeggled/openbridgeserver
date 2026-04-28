<!-- Widget-Palette für den Editor: zeigt alle registrierten Widgets, gruppiert nach Kategorie -->
<script setup lang="ts">
import { computed } from 'vue'
import { WidgetRegistry } from '@/widgets/registry'

const emit = defineEmits<{
  (e: 'insert', type: string): void
}>()

const GROUP_ORDER = ['Steuerung', 'Anzeige', 'Medien & Sonstiges']
const DEFAULT_GROUP = 'Sonstiges'

const groups = computed(() => {
  const map = new Map<string, ReturnType<typeof WidgetRegistry.all>>()
  for (const w of WidgetRegistry.all()) {
    const g = w.group ?? DEFAULT_GROUP
    if (!map.has(g)) map.set(g, [])
    map.get(g)!.push(w)
  }
  return [...map.entries()].sort(([a], [b]) => {
    const ia = GROUP_ORDER.indexOf(a)
    const ib = GROUP_ORDER.indexOf(b)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return a.localeCompare(b)
  })
})
</script>

<template>
  <aside class="w-52 flex-shrink-0 bg-gray-900 border-r border-gray-700 overflow-y-auto p-3">
    <p class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Widgets</p>
    <div
      v-for="([group, widgets]) in groups"
      :key="group"
      class="mb-4"
    >
      <p class="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-1 px-1">{{ group }}</p>
      <div class="space-y-1">
        <button
          v-for="w in widgets"
          :key="w.type"
          class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-gray-300 hover:bg-gray-800 hover:text-white transition-colors text-left"
          @click="emit('insert', w.type)"
        >
          <span class="text-xl leading-none w-6 text-center">{{ w.icon }}</span>
          <span>{{ w.label }}</span>
        </button>
      </div>
    </div>
  </aside>
</template>
