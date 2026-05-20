<template>
  <Teleport to="body">
    <Transition
      enter-from-class="opacity-0" enter-active-class="transition-opacity duration-200"
      leave-to-class="opacity-0"   leave-active-class="transition-opacity duration-150"
    >
      <div
        v-if="modelValue"
        class="fixed inset-0 z-50 flex items-center justify-center p-4"
        @mousedown.self="onBackdropClick"
      >
        <!-- Backdrop -->
        <div
          :class="[
            'absolute inset-0',
            softBackdrop
              ? 'bg-slate-900/5 dark:bg-black/20 pointer-events-none'
              : 'bg-black/60 backdrop-blur-sm',
          ]"
        />

        <!-- Panel -->
        <Transition
          enter-from-class="opacity-0 scale-95" enter-active-class="transition-all duration-200"
          leave-to-class="opacity-0 scale-95"   leave-active-class="transition-all duration-150"
        >
          <div
            v-if="modelValue"
            :class="['relative card shadow-2xl w-full flex flex-col max-h-[90vh] pointer-events-auto', maxWidthClass, { 'modal-resizable': resizable }]"
          >
            <!-- Header -->
            <div v-if="title" class="card-header shrink-0">
              <h3 class="text-base font-semibold text-slate-800 dark:text-slate-100">{{ title }}</h3>
              <button @click="$emit('update:modelValue', false)" class="btn-icon">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
            </div>

            <!-- Body -->
            <div class="card-body flex-1 min-h-0 overflow-y-auto">
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
import { computed, onMounted, onBeforeUnmount, watch } from 'vue'
const props = defineProps({
  modelValue: Boolean,
  title:      String,
  maxWidth:   { type: String, default: 'lg' },
  resizable:  { type: Boolean, default: false },
  /**
   * Soft backdrop variant (issue #435): renders a very light, non-blocking
   * backdrop without blur. Click-outside does NOT close the modal — only
   * ESC and the X button do. Default `false` preserves prior behaviour
   * for all existing callers.
   */
  softBackdrop: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const maxWidths = { sm: 'max-w-sm', md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-xl', '2xl': 'max-w-2xl' }
const maxWidthClass = computed(() => maxWidths[props.maxWidth] ?? maxWidths.lg)

function onBackdropClick() {
  if (props.softBackdrop) return
  emit('update:modelValue', false)
}

function onKeyDown(event) {
  if (event.key !== 'Escape' || !props.modelValue) return
  // Don't close the modal while the user is actively editing a field —
  // the field's own ESC handler (e.g. a combobox closing its dropdown)
  // should run, but the modal itself stays open. The user dismisses the
  // modal with another ESC after the field is blurred.
  const target = event.target
  const tag = target?.tagName?.toUpperCase?.() || ''
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  if (target?.isContentEditable) return
  emit('update:modelValue', false)
}

onMounted(() => {
  document.addEventListener('keydown', onKeyDown)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeyDown)
})

// Keep listener active across remounts — re-attach if modelValue toggles
// after mount (defensive).
watch(() => props.modelValue, () => {
  // no-op; listener is global. kept here as a hook point for future logic.
})
</script>

<style scoped>
.modal-resizable {
  resize: both;
  overflow: hidden;
  min-width: min(42rem, calc(100vw - 2rem));
  min-height: min(28rem, calc(100vh - 2rem));
  max-width: calc(100vw - 2rem);
}
</style>
