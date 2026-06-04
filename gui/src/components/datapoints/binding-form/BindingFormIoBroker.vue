<template>
  <div class="section-header">{{ $t('adapters.bindingForm.iobSection') }}</div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.iobStateIdLabel') }}</label>
    <div class="flex gap-2">
      <input
        v-model="cfg.state_id"
        class="input font-mono text-sm flex-1"
        :placeholder="$t('adapters.bindingForm.iobStateIdPlaceholder')"
        data-testid="config-field-state_id"
        required
        @input="$emit('iobroker-state-input')"
      />
      <button
        type="button"
        class="btn-secondary px-3 text-sm whitespace-nowrap"
        :disabled="!selectedInstanceId || iobrokerBrowseLoading"
        @click="$emit('browse-iobroker-states')"
      >
        <span v-if="iobrokerBrowseLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
        {{ iobrokerBrowseLoading ? $t('adapters.bindingForm.loading') : $t('adapters.bindingForm.browse') }}
      </button>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.iobStateHint') }}</p>

    <div
      v-if="iobrokerStates.length > 0"
      class="mt-2 max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
    >
      <button
        v-for="state in iobrokerStates"
        :key="state.id"
        type="button"
        class="w-full text-left px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-700/50"
        @click="$emit('select-iobroker-state', state)"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span class="font-mono text-sm text-slate-700 dark:text-slate-100 truncate">{{ state.id }}</span>
          <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 shrink-0">{{ state.type || $t('adapters.bindingForm.iobAutoType') }}</span>
          <span v-if="state.write" class="text-[11px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 shrink-0">{{ $t('adapters.bindingForm.iobWriteTag') }}</span>
        </div>
        <div class="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
          <span class="truncate">{{ state.name || '—' }}</span>
          <span v-if="state.role" class="shrink-0">{{ state.role }}</span>
          <span v-if="state.value !== null && state.value !== undefined" class="font-mono shrink-0">= {{ state.value }}</span>
        </div>
      </button>
    </div>
    <p v-if="iobrokerBrowseError" class="text-xs text-red-400 mt-1">{{ iobrokerBrowseError }}</p>
  </div>

  <div class="grid grid-cols-2 gap-4">
    <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.iobDataTypeLabel') }}</label>
      <select v-model="cfg.source_data_type" class="input">
        <option value="">{{ $t('adapters.bindingForm.iobAutoTypeLabel') }}</option>
        <option value="bool">{{ $t('adapters.bindingForm.iobTypeBool') }}</option>
        <option value="float">{{ $t('adapters.bindingForm.iobTypeFloat') }}</option>
        <option value="int">{{ $t('adapters.bindingForm.iobTypeInt') }}</option>
        <option value="string">{{ $t('adapters.bindingForm.iobTypeString') }}</option>
        <option value="json">{{ $t('adapters.bindingForm.iobTypeJson') }}</option>
      </select>
      <p class="hint">{{ $t('adapters.bindingForm.iobDataTypeHint') }}</p>
    </div>
    <div v-if="cfg.source_data_type === 'json' && (form.direction === 'SOURCE' || form.direction === 'BOTH')" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.iobJsonKeyLabel') }}</label>
      <input v-model="cfg.json_key" class="input" :placeholder="$t('adapters.bindingForm.iobJsonKeyPlaceholder')" />
      <p class="hint">{{ $t('adapters.bindingForm.iobJsonKeyHint') }}</p>
    </div>
  </div>

  <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="optional-divider">{{ $t('adapters.bindingForm.iobWriteSection') }}</div>
  <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.iobCommandStateLabel') }} <span class="optional">{{ $t('adapters.bindingForm.iobOptional') }}</span></label>
      <input
        v-model="cfg.command_state_id"
        class="input font-mono text-sm"
        :placeholder="$t('adapters.bindingForm.iobCommandStatePlaceholder')"
      />
      <p class="hint">{{ $t('adapters.bindingForm.iobCommandStateHint') }}</p>
    </div>
    <div class="form-group flex flex-col justify-end">
      <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-6">
        <input type="checkbox" v-model="cfg.ack" class="w-4 h-4 rounded" />
        {{ $t('adapters.bindingForm.iobWriteWithAckLabel') }}
      </label>
      <p class="hint">{{ $t('adapters.bindingForm.iobWriteWithAckHint') }}</p>
    </div>
  </div>

  <button
    type="button"
    class="text-sm text-blue-500 hover:text-blue-400 self-start"
    @click="$emit('toggle-advanced-tabs')"
  >
    {{ showAdvancedTabs ? $t('adapters.bindingForm.hideAdvancedOptions') : $t('adapters.bindingForm.showAdvancedOptions') }}
  </button>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  form: { type: Object, required: true },
  selectedInstanceId: { type: [String, Number, null], default: null },
  iobrokerStates: { type: Array, required: true },
  iobrokerBrowseLoading: { type: Boolean, required: true },
  iobrokerBrowseError: { type: [String, null], default: null },
  showAdvancedTabs: { type: Boolean, required: true },
})

defineEmits([
  'iobroker-state-input',
  'browse-iobroker-states',
  'select-iobroker-state',
  'toggle-advanced-tabs',
])
</script>
