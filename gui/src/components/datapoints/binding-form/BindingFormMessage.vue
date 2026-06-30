<template>
  <div class="section-header">{{ $t('adapters.bindingForm.messageSection') }}</div>

  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.operatorLabel') }}</label>
      <select v-model="cfg.operator" class="input">
        <option v-for="op in OPERATORS" :key="op" :value="op">{{ op }}</option>
      </select>
    </div>
    <div v-if="cfg.operator !== 'any'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.compareValueLabel') }}</label>
      <input v-model="cfg.compare_value" class="input" />
    </div>
  </div>

  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.titleLabel') }}</label>
      <input v-model="cfg.title" class="input" />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.cooldownSecondsLabel') }}</label>
      <input v-model.number="cfg.cooldown_seconds" type="number" min="0" step="1" class="input" />
    </div>
  </div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.messageTemplateLabel') }}</label>
    <textarea v-model="cfg.message" class="input min-h-24 font-mono text-sm resize-y" />
    <p class="hint">###DP### · ###DPU### · ###DPN### · ###DPI### · ###TS###</p>
  </div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.targetsLabel') }}</label>
    <div class="flex flex-col gap-2">
      <div v-for="(_, idx) in cfg.providers" :key="idx" class="grid grid-cols-[1fr_1fr_auto] gap-2 items-center">
        <select v-model="cfg.providers[idx].provider" class="input" @change="cfg.providers[idx].target = firstTarget(cfg.providers[idx].provider)">
          <option v-for="provider in providerOptions" :key="provider.provider" :value="provider.provider">{{ provider.provider }}</option>
        </select>
        <select v-model="cfg.providers[idx].target" class="input">
          <option v-for="target in targetsFor(cfg.providers[idx].provider)" :key="target" :value="target">{{ target }}</option>
        </select>
        <button type="button" class="btn-danger btn-sm" @click="cfg.providers.splice(idx, 1)">{{ $t('common.delete') }}</button>
      </div>
      <button type="button" class="btn-secondary btn-sm self-start" :disabled="unusedProviderTargets.length === 0" @click="addProviderTarget">
        {{ $t('adapters.bindingForm.addTarget') }}
      </button>
    </div>
  </div>

  <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
    <input type="checkbox" v-model="cfg.send_on_change" class="w-4 h-4 rounded" />
    {{ $t('adapters.bindingForm.messageSendOnChange') }}
  </label>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  cfg: { type: Object, required: true },
  selectedInstance: { type: Object, default: null },
})

const OPERATORS = ['any', '=', '==', '<', '<=', '>', '>=', '!=', 'contains', 'contains not', 'starts with', 'ends with']

function providerEnabled(config) {
  if (config?.enabled === true || config?.enabled === 1) return true
  if (typeof config?.enabled === 'string') {
    return ['true', '1', 'yes', 'on'].includes(config.enabled.trim().toLowerCase())
  }
  return false
}

const providerOptions = computed(() => {
  const providers = props.selectedInstance?.config?.providers ?? {}
  return Object.entries(providers)
    .filter(([, config]) => providerEnabled(config))
    .map(([provider, config]) => ({ provider, targets: Object.keys(config.targets ?? {}) }))
    .filter((entry) => entry.targets.length > 0)
})

const selectedPairs = computed(() => new Set((props.cfg.providers ?? []).map((ref) => `${ref.provider}\u0000${ref.target}`)))

const unusedProviderTargets = computed(() => {
  const unused = []
  for (const option of providerOptions.value) {
    for (const target of option.targets) {
      if (!selectedPairs.value.has(`${option.provider}\u0000${target}`)) {
        unused.push({ provider: option.provider, target })
      }
    }
  }
  return unused
})

function targetsFor(provider) {
  return providerOptions.value.find((entry) => entry.provider === provider)?.targets ?? []
}

function firstTarget(provider) {
  return targetsFor(provider)[0] ?? ''
}

function addProviderTarget() {
  const first = unusedProviderTargets.value[0]
  if (!first) return
  props.cfg.providers.push({ provider: first.provider, target: first.target })
}
</script>
