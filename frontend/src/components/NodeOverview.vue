<!-- Auto-Übersicht für LOCATION-Knoten: zeigt alle direkten Kinder als Kachelraster -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import type { VisuNode } from '@/types'

const props = defineProps<{ nodeId: string }>()
const router = useRouter()
const store = useVisuStore()

const children = computed(() => store.getChildren(props.nodeId))

function navigate(node: VisuNode) {
  router.push({ name: 'viewer', params: { id: node.id } })
}
</script>

<template>
  <div v-if="children.length === 0" class="text-gray-500 text-sm text-center py-12">
    Dieser Knoten hat keine untergeordneten Seiten.
  </div>
  <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
    <button
      v-for="child in children"
      :key="child.id"
      class="flex flex-col items-center justify-center gap-3 p-6 rounded-xl bg-gray-800 border border-gray-700 hover:border-blue-500 hover:bg-gray-750 transition-all group"
      @click="navigate(child)"
    >
      <span class="text-4xl">{{ child.icon ?? (child.type === 'PAGE' ? '📄' : '📁') }}</span>
      <span class="text-sm font-medium text-gray-200 text-center leading-tight group-hover:text-white">
        {{ child.name }}
      </span>
      <span class="text-xs text-gray-500">
        {{ child.type === 'PAGE' ? 'Seite' : 'Bereich' }}
      </span>
    </button>
  </div>
</template>
