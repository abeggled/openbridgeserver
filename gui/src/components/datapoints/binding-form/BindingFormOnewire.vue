<template>
  <div class="section-header">{{ $t('adapters.bindingForm.onewireSection') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.onewireSensorIdLabel') }}</label>
      <input v-model="cfg.sensor_id" class="input" :placeholder="$t('adapters.bindingForm.onewireSensorIdPlaceholder')" required />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.onewireSensorTypeLabel') }}</label>
      <input v-model="cfg.sensor_type" class="input" :placeholder="$t('adapters.bindingForm.onewireSensorTypePlaceholder')" />
      <p class="hint">{{ $t('adapters.bindingForm.onewireSensorTypeHint') }}</p>
    </div>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
})
</script>
