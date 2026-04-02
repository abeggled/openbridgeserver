<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

const router   = useRouter()
const label    = computed(() => (props.config.label         as string | undefined) ?? 'Link')
const icon     = computed(() => (props.config.icon          as string | undefined) ?? '🔗')
const targetId = computed(() => (props.config.target_node_id as string | undefined) ?? '')

function navigate() {
  if (props.editorMode || !targetId.value) return
  router.push({ name: 'viewer', params: { id: targetId.value } })
}
</script>

<template>
  <div
    class="flex flex-col items-center justify-center h-full p-3 gap-2 select-none rounded-xl transition-colors"
    :class="editorMode
      ? 'cursor-default opacity-70'
      : 'cursor-pointer hover:bg-gray-200/60 dark:hover:bg-white/5 active:bg-gray-300/60 dark:active:bg-white/10'"
    @click="navigate"
  >
    <span class="text-4xl leading-none">{{ icon }}</span>
    <span class="text-sm font-medium text-gray-800 dark:text-gray-200 text-center leading-tight">{{ label }}</span>
    <span v-if="!editorMode && targetId" class="text-xs text-gray-400 dark:text-gray-500">→</span>
    <span v-else-if="!targetId" class="text-xs text-gray-400 dark:text-gray-600">Kein Ziel</span>
  </div>
</template>
