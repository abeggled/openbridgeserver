<template>
  <div class="section-header">{{ $t('adapters.bindingForm.knxSection') }}</div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.groupAddressLabel') }}</label>
    <GaCombobox v-model="cfg.group_address" :placeholder="$t('adapters.bindingForm.groupAddressPlaceholder')" @select="$emit('ga-select', $event)" />
  </div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.dptLabel') }}</label>
    <select v-model="cfg.dpt_id" class="input" required>
      <option value="">{{ $t('adapters.bindingForm.selectDpt') }}</option>
      <optgroup v-for="group in groupedDpts" :key="group.family" :label="group.label">
        <option v-for="dpt in group.dpts" :key="dpt.dpt_id" :value="dpt.dpt_id">
          {{ dpt.dpt_id }} — {{ dpt.name }}<template v-if="dpt.unit"> [{{ dpt.unit }}]</template>
        </option>
      </optgroup>
    </select>
  </div>
  <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="flex items-start gap-2">
    <input
      type="checkbox"
      id="respond_to_read"
      v-model="cfg.respond_to_read"
      :disabled="!dpPersistValue"
      class="w-4 h-4 rounded mt-0.5"
    />
    <div>
      <label
        for="respond_to_read"
        class="text-sm"
        :class="dpPersistValue ? 'text-slate-600 dark:text-slate-300' : 'text-slate-400 dark:text-slate-500 cursor-not-allowed'"
      >{{ $t('adapters.bindingForm.respondToReadLabel') }}</label>
      <p class="hint">
        {{ $t('adapters.bindingForm.respondToReadHint') }}
        <template v-if="!dpPersistValue"> {{ $t('adapters.bindingForm.respondToReadPersistHint') }}</template>
      </p>
    </div>
  </div>
  <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
    <label for="read_interval_s" class="label">{{ $t('adapters.bindingForm.readIntervalLabel') }}</label>
    <input
      id="read_interval_s"
      v-model.number="cfg.read_interval_s"
      type="number"
      min="0"
      step="any"
      class="input"
      :placeholder="$t('adapters.bindingForm.readIntervalPlaceholder')"
    />
    <p class="hint">{{ $t('adapters.bindingForm.readIntervalHint', { min: READ_INTERVAL_MIN_S }) }}</p>
  </div>
</template>

<script setup>
import GaCombobox from '@/components/ui/GaCombobox.vue'

// Mirrors READ_INTERVAL_MIN_S in obs/adapters/knx/adapter.py (issue #1402). Only
// the hint uses it: 0 must stay enterable (= off) and buildConfig rounds fractions,
// so the browser's min/step checks would block valid input; the API rejects 1–9.
const READ_INTERVAL_MIN_S = 10

defineProps({
  cfg: { type: Object, required: true },
  form: { type: Object, required: true },
  groupedDpts: { type: Array, required: true },
  dpPersistValue: { type: Boolean, required: true },
})

defineEmits(['ga-select'])
</script>
