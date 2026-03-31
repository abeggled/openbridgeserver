<template>
  <Teleport to="body">
    <Transition
      enter-from-class="opacity-0" enter-active-class="transition-opacity duration-200"
      leave-to-class="opacity-0"   leave-active-class="transition-opacity duration-150"
    >
      <div v-if="modelValue" class="fixed inset-0 z-50 flex items-center justify-center p-4" @mousedown.self="$emit('update:modelValue', false)">
        <!-- Backdrop -->
        <div class="absolute inset-0 bg-black/60 backdrop-blur-sm" />

        <!-- Panel -->
        <Transition
          enter-from-class="opacity-0 scale-95" enter-active-class="transition-all duration-200"
          leave-to-class="opacity-0 scale-95"   leave-active-class="transition-all duration-150"
        >
          <div v-if="modelValue" :class="['relative card shadow-2xl w-full', maxWidthClass]">
            <!-- Header -->
            <div v-if="title" class="card-header">
              <h3 class="text-base font-semibold text-slate-800 dark:text-slate-100">{{ title }}</h3>
              <button @click="$emit('update:modelValue', false)" class="btn-icon">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>

            <!-- Body -->
            <div class="card-body">
              <slot />
            </div>

            <!-- Footer -->
            <div v-if="$slots.footer" class="px-5 py-4 border-t border-slate-200 dark:border-slate-700/60 flex justify-end gap-3">
              <slot name="footer" />
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  modelValue: Boolean,
  title:      String,
  maxWidth:   { type: String, default: 'lg' },
})
defineEmits(['update:modelValue'])
const maxWidths = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-xl', '2xl': 'max-w-2xl' }
const maxWidthClass = computed(() => maxWidths[props.maxWidth] ?? maxWidths.lg)
</script>
