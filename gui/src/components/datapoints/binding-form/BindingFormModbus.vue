<template>
  <div class="section-header">{{ $t('adapters.bindingForm.modbusSection') }}</div>
  <div class="grid grid-cols-3 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.addressLabel') }}</label>
      <input v-model.number="cfg.address" type="number" min="0" max="65535" class="input" required />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.registerTypeLabel') }}</label>
      <select v-model="cfg.register_type" class="input">
        <option value="holding">{{ $t('adapters.bindingForm.modbusHoldingRegister') }}</option>
        <option value="input">{{ $t('adapters.bindingForm.modbusInputRegister') }}</option>
        <option value="coil">Coil</option>
        <option value="discrete_input">{{ $t('adapters.bindingForm.modbusDiscreteInput') }}</option>
      </select>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.dataFormatLabel') }}</label>
      <select v-model="cfg.data_format" class="input">
        <optgroup label="16-Bit">
          <option value="uint16">UINT16</option>
          <option value="int16">INT16</option>
        </optgroup>
        <optgroup label="32-Bit">
          <option value="uint32">UINT32</option>
          <option value="int32">INT32</option>
          <option value="float32">FLOAT32</option>
        </optgroup>
        <optgroup label="64-Bit">
          <option value="uint64">UINT64</option>
          <option value="int64">INT64</option>
        </optgroup>
      </select>
    </div>
  </div>
  <div class="optional-divider">{{ $t('adapters.binding.optionalSettings') }}</div>
  <div class="grid grid-cols-4 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.unitIdLabel') }}</label>
      <input v-model.number="cfg.unit_id" type="number" min="0" max="255" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '1' }) }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.registerCountLabel') }}</label>
      <input v-model.number="cfg.count" type="number" min="1" max="125" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '1' }) }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.scaleLabel') }}</label>
      <input v-model.number="cfg.scale_factor" type="number" step="any" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '1.0' }) }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.intervalSecondsLabel') }}</label>
      <input v-model.number="cfg.poll_interval" type="number" step="0.1" min="0.1" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '1.0' }) }}</p>
    </div>
  </div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.byteOrderLabel') }}</label>
      <select v-model="cfg.byte_order" class="input">
        <option value="big">{{ $t('adapters.bindingForm.bigEndian') }}</option>
        <option value="little">{{ $t('adapters.bindingForm.littleEndian') }}</option>
      </select>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.wordOrderLabel') }}</label>
      <select v-model="cfg.word_order" class="input">
        <option value="big">{{ $t('adapters.bindingForm.bigEndian') }}</option>
        <option value="little">{{ $t('adapters.bindingForm.littleEndian') }}</option>
      </select>
    </div>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
})
</script>
