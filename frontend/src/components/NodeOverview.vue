<!-- Auto-Übersicht für LOCATION-Knoten: zeigt alle direkten Kinder als Kachelraster.
     Private Knoten werden für Nicht-Admins ausgeblendet.
     Protected Knoten sind sichtbar, zeigen aber ein PIN-Badge. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { getJwt } from '@/api/client'
import type { VisuNode, AccessLevel } from '@/types'

const props = defineProps<{ nodeId: string }>()
const router = useRouter()
const store  = useVisuStore()

const isAdmin = computed(() => !!getJwt())

/**
 * Effektiven Zugang eines Knotens bestimmen (Vererbung berücksichtigen).
 * null → Elternknoten-Zugang, bis ein expliziter Wert gefunden wird.
 * Fallback wenn Baum-Wurzel erreicht: 'public'
 */
function effectiveAccess(node: VisuNode): AccessLevel {
  let cur: VisuNode | undefined = node
  while (cur) {
    if (cur.access !== null) return cur.access
    cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
  }
  return 'public'
}

const children = computed(() => {
  const all = store.getChildren(props.nodeId)
  return all.filter(n => {
    const access = effectiveAccess(n)
    // Private Seiten nur für Admins sichtbar
    if (access === 'private') return isAdmin.value
    return true
  })
})

function navigate(node: VisuNode) {
  router.push({ name: 'viewer', params: { id: node.id } })
}

function accessBadge(node: VisuNode): { icon: string; label: string; cls: string } | null {
  const access = effectiveAccess(node)
  if (access === 'readonly')  return { icon: '👁', label: 'Nur ansehen', cls: 'bg-blue-500/20 text-blue-600 dark:text-blue-400' }
  if (access === 'protected') return { icon: '🔐', label: 'PIN', cls: 'bg-amber-500/20 text-amber-600 dark:text-amber-400' }
  if (access === 'private')   return { icon: '🔒', label: 'Privat', cls: 'bg-red-500/20 text-red-500 dark:text-red-400' }
  return null
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
      class="relative flex flex-col items-center justify-center gap-3 p-6 rounded-xl bg-gray-800 border border-gray-700 hover:border-blue-500 hover:bg-gray-750 transition-all group"
      @click="navigate(child)"
    >
      <!-- Access-Badge (oben rechts) -->
      <span
        v-if="accessBadge(child)"
        class="absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded font-medium"
        :class="accessBadge(child)!.cls"
        :title="accessBadge(child)!.label"
      >{{ accessBadge(child)!.icon }}</span>

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
