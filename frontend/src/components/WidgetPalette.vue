<!-- Widget-Palette für den Editor: zeigt alle registrierten Widgets -->
<script setup lang="ts">
import { WidgetRegistry } from '@/widgets/registry'

const emit = defineEmits<{
  (e: 'insert', type: string): void
}>()

const widgets = WidgetRegistry.all()
</script>

<template>
  <aside class="w-52 flex-shrink-0 bg-gray-900 border-r border-gray-700 overflow-y-auto p-3">
    <p class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Widgets</p>
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
  </aside>
</template>
