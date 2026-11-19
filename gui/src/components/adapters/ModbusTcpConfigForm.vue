<template>
  <div class="flex flex-col gap-4">
    <div class="flex justify-end -mb-2">
      <HelpButton help-id="adapters-modbus-tls" />
    </div>
    <SchemaForm
      :schema="schema"
      :model-value="modelValue"
      adapter-type="MODBUS_TCP"
      :exclude="excluded"
      @update:model-value="onUpdate"
    />
    <div
      v-if="modelValue.tls && modelValue.tls_verify === false"
      class="flex items-start gap-2 p-3 rounded-lg text-sm bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400"
      data-testid="modbus-tls-verify-disabled-hint"
    >
      <svg class="w-4 h-4 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
      </svg>
      <span>{{ $t('adapters.modbusTcp.tlsVerifyDisabledHint') }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import HelpButton from '@/components/ui/HelpButton.vue'
import SchemaForm from '@/components/adapters/SchemaForm.vue'

const props = defineProps({
  schema:     { type: Object, required: true },
  modelValue: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

// Default ports: plain Modbus TCP and Modbus/TCP Security (TLS).
const PLAIN_PORT = 502
const TLS_PORT = 802

const TLS_DETAIL_FIELDS = [
  'tls_verify',
  'tls_ca_file',
  'tls_client_cert_file',
  'tls_client_key_file',
  'tls_client_key_password',
]

// The TLS details stay in the config (SchemaForm keeps excluded keys) but are only
// shown while TLS is on; the CA file is irrelevant once verification is off.
const excluded = computed(() => {
  if (!props.modelValue.tls) return TLS_DETAIL_FIELDS
  return props.modelValue.tls_verify === false ? ['tls_ca_file'] : []
})

// Toggling TLS suggests the matching default port, but never overwrites a custom one.
function onUpdate(cfg) {
  const tlsOn = Boolean(cfg.tls)
  if (tlsOn === Boolean(props.modelValue.tls)) {
    emit('update:modelValue', cfg)
    return
  }
  const [from, to] = tlsOn ? [PLAIN_PORT, TLS_PORT] : [TLS_PORT, PLAIN_PORT]
  const port = cfg.port == null || cfg.port === from ? to : cfg.port
  emit('update:modelValue', { ...cfg, port })
}
</script>
