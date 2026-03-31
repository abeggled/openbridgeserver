<template>
  <Modal :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)" :title="title" max-width="sm">
    <p class="text-slate-600 dark:text-slate-300 text-sm">{{ message }}</p>
    <template #footer>
      <button @click="$emit('update:modelValue', false)" class="btn-secondary btn-sm">Abbrechen</button>
      <button @click="confirm" :class="['btn-sm', danger ? 'btn-danger' : 'btn-primary']">{{ confirmLabel }}</button>
    </template>
  </Modal>
</template>

<script setup>
import Modal from './Modal.vue'
const props = defineProps({
  modelValue:   Boolean,
  title:        { type: String, default: 'Bestätigung' },
  message:      { type: String, default: 'Bist du sicher?' },
  confirmLabel: { type: String, default: 'Bestätigen' },
  danger:       { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'confirm'])
function confirm() {
  emit('confirm')
  emit('update:modelValue', false)
}
</script>
