<template>
  <div class="relative">
    <svg
      aria-hidden="true"
      class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z" />
    </svg>
    <input
      ref="inputRef"
      :value="modelValue"
      type="search"
      :data-testid="testid"
      :placeholder="placeholder"
      :aria-label="ariaLabel || placeholder"
      :class="inputClass"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <button
      v-if="modelValue"
      type="button"
      class="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1 text-slate-400 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500 dark:hover:text-slate-200"
      :aria-label="clearLabel"
      :title="clearLabel"
      :data-testid="testid ? `${testid}-clear` : undefined"
      @click="$emit('update:modelValue', '')"
    >
      ×
    </button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  ariaLabel: { type: String, default: '' },
  clearLabel: { type: String, default: 'Clear' },
  testid: { type: String, default: '' },
  inputClass: { type: String, default: 'input w-full pl-9 pr-9' },
})

defineEmits(['update:modelValue'])

const inputRef = ref(null)

defineExpose({
  focus: () => inputRef.value?.focus(),
})
</script>
