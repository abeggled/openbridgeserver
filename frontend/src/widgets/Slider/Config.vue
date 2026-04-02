<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label: (props.modelValue.label as string) ?? '',
  unit: (props.modelValue.unit as string) ?? '',
  min: (props.modelValue.min as number) ?? 0,
  max: (props.modelValue.max as number) ?? 100,
  step: (props.modelValue.step as number) ?? 1,
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Beschriftung</label>
      <input v-model="cfg.label" type="text" placeholder="z.B. Solltemperatur"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Einheit</label>
      <input v-model="cfg.unit" type="text" placeholder="z.B. °C"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
    <div class="grid grid-cols-3 gap-2">
      <div>
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min</label>
        <input v-model.number="cfg.min" type="number"
          class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
      </div>
      <div>
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Max</label>
        <input v-model.number="cfg.max" type="number"
          class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
      </div>
      <div>
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Schritt</label>
        <input v-model.number="cfg.step" type="number" min="0.01"
          class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500" />
      </div>
    </div>
  </div>
</template>
