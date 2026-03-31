<script setup lang="ts">
import { computed, ref } from 'vue'
import { datapoints } from '@/api/client'
import type { DataPointValue } from '@/types'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
}>()

const label = computed(() => (props.config.label as string | undefined) ?? '—')
const isOn = computed(() => {
  if (props.value === null) return false
  const v = props.value.v
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  return false
})

const pending = ref(false)

async function toggle() {
  if (props.editorMode || !props.datapointId || pending.value) return
  pending.value = true
  try {
    await datapoints.write(props.datapointId, !isOn.value)
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <div
    class="flex flex-col items-center justify-center h-full p-3 gap-2 select-none cursor-pointer"
    :class="{ 'opacity-60': editorMode }"
    @click="toggle"
  >
    <span class="text-xs text-gray-400 truncate w-full text-center">{{ label }}</span>
    <!-- Toggle-Schalter -->
    <button
      class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none"
      :class="isOn ? 'bg-blue-500' : 'bg-gray-600'"
      :disabled="editorMode || pending"
      :aria-checked="isOn"
      role="switch"
    >
      <span
        class="absolute top-0.5 left-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform duration-200"
        :class="{ 'translate-x-7': isOn }"
      />
    </button>
    <span class="text-xs font-medium" :class="isOn ? 'text-blue-400' : 'text-gray-500'">
      {{ isOn ? 'EIN' : 'AUS' }}
    </span>
  </div>
</template>
