<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label: (props.modelValue.label as string) ?? '',
  hours: (props.modelValue.hours as number) ?? 24,
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input v-model="cfg.label" type="text" placeholder="z.B. Temperaturverlauf"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Zeitraum (Stunden)</label>
      <input v-model.number="cfg.hours" type="number" min="1" max="720"
        class="w-32 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
  </div>
</template>
