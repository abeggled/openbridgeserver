<template>
  <div class="section-header">{{ $t('adapters.bindingForm.anwSection') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.anwOffsetOverrideLabel') }}</label>
      <div class="flex gap-2">
        <select :value="anwOffsetSelect" class="input" @change="$emit('update:anwOffsetSelect', $event.target.value); $emit('anw-offset-select-change')">
          <option value="">{{ $t('adapters.bindingForm.anwDefaultAdapter') }}</option>
          <option value="1">{{ $t('adapters.bindingForm.anwOneDay') }}</option>
          <option value="7">{{ $t('adapters.bindingForm.anwSevenDays') }}</option>
          <option value="14">{{ $t('adapters.bindingForm.anwFourteenDays') }}</option>
          <option value="custom">{{ $t('adapters.bindingForm.anwCustomDays') }}</option>
        </select>
        <input
          v-if="anwOffsetSelect === 'custom'"
          v-model.number="cfg.offset_override"
          type="number" min="1" max="30"
          class="input w-24"
          :placeholder="$t('adapters.bindingForm.ztDaysLabel')"
          @input="$emit('anw-offset-custom-input')"
        />
      </div>
      <p class="hint">{{ $t('adapters.bindingForm.anwOffsetOverrideHint') }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.anwOnPresenceLabel') }}</label>
      <select v-model="cfg.on_presence_override" class="input">
        <option :value="null">{{ $t('adapters.bindingForm.anwDefaultAdapter') }}</option>
        <option value="behalten">{{ $t('adapters.bindingForm.anwKeepValue') }}</option>
        <option value="zuruecksetzen">{{ $t('adapters.bindingForm.anwResetValue') }}</option>
        <option value="setzen">{{ $t('adapters.bindingForm.anwSetValue') }}</option>
      </select>
      <input
        v-if="cfg.on_presence_override === 'setzen'"
        v-model="cfg.on_presence_value"
        type="text"
        class="input mt-2"
        :placeholder="$t('adapters.bindingForm.anwSetValuePlaceholder')"
      />
      <p class="hint">{{ $t('adapters.bindingForm.anwOnPresenceHint') }}</p>
    </div>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  anwOffsetSelect: { type: String, required: true },
})

defineEmits([
  'update:anwOffsetSelect',
  'anw-offset-select-change',
  'anw-offset-custom-input',
])
</script>
