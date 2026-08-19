<template>
  <Modal
    :model-value="modelValue"
    :title="title"
    max-width="lg"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="space-y-4" data-testid="action-preflight">
      <p v-if="description" class="text-sm text-slate-500">{{ description }}</p>

      <div v-if="loading" class="flex justify-center py-6">
        <Spinner />
      </div>
      <p v-else-if="error" class="rounded bg-red-500/10 px-3 py-2 text-sm text-red-400" role="alert">
        {{ error }}
      </p>
      <p v-else-if="!items.length" class="text-sm text-slate-500">
        {{ $t('authzPreflight.noRequirements') }}
      </p>
      <ul v-else class="divide-y divide-slate-200 rounded border border-slate-200 dark:divide-slate-700/60 dark:border-slate-700/60">
        <li v-for="(item, index) in items" :key="item.id ?? index" class="flex gap-3 px-3 py-2.5">
          <span
            :class="item.allowed ? 'text-green-500' : 'text-red-400'"
            :aria-label="item.allowed ? $t('authzPreflight.allowed') : $t('authzPreflight.denied')"
          >
            {{ item.allowed ? '✓' : '✕' }}
          </span>
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium text-slate-800 dark:text-slate-100">{{ item.label }}</p>
            <p v-if="item.detail" class="mt-0.5 break-words text-xs text-slate-500">{{ item.detail }}</p>
            <p v-if="!item.allowed && item.reason" class="mt-1 text-xs text-red-400">{{ item.reason }}</p>
          </div>
        </li>
      </ul>

      <p
        v-if="!loading && !error"
        :class="allowed ? 'text-green-500' : 'text-red-400'"
        class="text-sm font-medium"
        data-testid="preflight-outcome"
      >
        {{ allowed ? $t('authzPreflight.ready') : $t('authzPreflight.blocked') }}
      </p>
    </div>

    <template #footer>
      <button type="button" class="btn-secondary" @click="$emit('update:modelValue', false)">
        {{ $t('common.cancel') }}
      </button>
      <button
        type="button"
        class="btn-primary"
        :disabled="loading || !!error || !allowed"
        data-testid="preflight-confirm"
        @click="$emit('confirm')"
      >
        {{ confirmLabel || $t('common.confirm') }}
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed } from 'vue'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, required: true },
  description: { type: String, default: '' },
  confirmLabel: { type: String, default: '' },
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

defineEmits(['update:modelValue', 'confirm'])

const allowed = computed(() => props.items.every(item => item.allowed !== false))
</script>
