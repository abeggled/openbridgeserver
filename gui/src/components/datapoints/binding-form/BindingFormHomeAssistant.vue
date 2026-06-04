<template>
  <div class="section-header">{{ $t('adapters.bindingForm.haSection') }}</div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haEntityIdLabel') }}</label>
    <input
      v-model="cfg.entity_id"
      class="input"
      :placeholder="$t('adapters.bindingForm.haEntityIdPlaceholder')"
      data-testid="config-field-entity_id"
      required
    />
    <p class="hint">{{ $t('adapters.bindingForm.haEntityIdHint') }}</p>
  </div>
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.haAttributeLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haAttributeOptional') }}</span></label>
    <input
      v-model="cfg.attribute"
      class="input"
      :placeholder="$t('adapters.bindingForm.haAttributePlaceholder')"
      data-testid="config-field-attribute"
    />
    <p class="hint">{{ $t('adapters.bindingForm.haAttributeHint') }}</p>
  </div>
  <div class="optional-divider">{{ $t('adapters.bindingForm.haWriteSection') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.haServiceDomainLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceDomainOptional') }}</span></label>
      <input
        v-model="cfg.service_domain"
        class="input"
        :placeholder="$t('adapters.bindingForm.haServiceDomainPlaceholder')"
        data-testid="config-field-service_domain"
      />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.haServiceNameLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceNameOptional') }}</span></label>
      <input
        v-model="cfg.service_name"
        class="input"
        :placeholder="$t('adapters.bindingForm.haServiceNamePlaceholder')"
        data-testid="config-field-service_name"
      />
    </div>
  </div>
  <div class="form-group" style="max-width:280px">
    <label class="label">{{ $t('adapters.bindingForm.haServiceDataKeyLabel') }} <span class="optional">{{ $t('adapters.bindingForm.haServiceDataKeyOptional') }}</span></label>
    <input
      v-model="cfg.service_data_key"
      class="input"
      :placeholder="$t('adapters.bindingForm.haServiceDataKeyPlaceholder')"
      data-testid="config-field-service_data_key"
    />
    <p class="hint">{{ $t('adapters.bindingForm.haServiceDataKeyHint') }}</p>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
})
</script>
