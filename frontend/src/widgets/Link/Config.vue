<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label: (props.modelValue.label as string) ?? '',
  icon: (props.modelValue.icon as string) ?? '🔗',
  target_node_id: (props.modelValue.target_node_id as string) ?? '',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input v-model="cfg.label" type="text" placeholder="z.B. Zur Übersicht"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Icon (Emoji)</label>
      <input v-model="cfg.icon" type="text" maxlength="4"
        class="w-20 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Ziel-Node-ID</label>
      <input v-model="cfg.target_node_id" type="text" placeholder="UUID des Zielknotens"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500" />
    </div>
  </div>
</template>
