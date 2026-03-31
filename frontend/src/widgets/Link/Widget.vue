<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const router = useRouter()
const label = computed(() => (props.config.label as string | undefined) ?? 'Link')
const icon = computed(() => (props.config.icon as string | undefined) ?? '🔗')
const targetId = computed(() => props.config.target_node_id as string | undefined)

function navigate() {
  if (props.editorMode || !targetId.value) return
  router.push({ name: 'viewer', params: { id: targetId.value } })
}
</script>

<template>
  <div
    class="flex flex-col items-center justify-center h-full p-3 gap-2 select-none"
    :class="editorMode ? 'cursor-default opacity-60' : 'cursor-pointer hover:bg-white/5 transition-colors'"
    @click="navigate"
  >
    <span class="text-3xl">{{ icon }}</span>
    <span class="text-sm font-medium text-gray-200 text-center leading-tight">{{ label }}</span>
    <span v-if="!editorMode" class="text-xs text-gray-500">→</span>
  </div>
</template>
