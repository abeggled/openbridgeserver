<template>
  <div class="section-header">{{ $t('adapters.bindingForm.snmpSection') }}</div>

  <!-- Host + Port -->
  <div class="grid grid-cols-3 gap-4">
    <div class="form-group col-span-2">
      <label class="label">{{ $t('adapters.bindingForm.snmpHostLabel') }}</label>
      <input v-model="cfg.host" class="input" :placeholder="$t('adapters.bindingForm.snmpHostPlaceholder')" required />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.snmpPortLabel') }}</label>
      <input v-model.number="cfg.port" type="number" min="1" max="65535" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '161' }) }}</p>
    </div>
  </div>

  <!-- OID with Walk -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.snmpOidLabel') }}</label>
    <div class="flex gap-2">
      <input
        v-model="cfg.oid"
        class="input flex-1 font-mono text-sm"
        :placeholder="$t('adapters.bindingForm.snmpOidPlaceholder')"
        required
      />
    </div>
    <!-- Walk root (independent from binding OID) -->
    <div class="flex gap-2 mt-2">
      <input
        :value="snmpWalkRoot"
        class="input flex-1 font-mono text-xs"
        :placeholder="$t('adapters.bindingForm.snmpWalkRootPlaceholder')"
        @input="$emit('update:snmpWalkRoot', $event.target.value)"
      />
      <button
        type="button"
        class="btn-secondary px-3 text-sm whitespace-nowrap"
        :disabled="!cfg.host || !selectedInstanceId || snmpWalkLoading"
        @click="$emit('snmp-walk')"
      >
        <span v-if="snmpWalkLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
        {{ snmpWalkLoading ? $t('adapters.bindingForm.snmpWalkLoading') : $t('adapters.bindingForm.snmpWalkButton') }}
      </button>
    </div>
    <!-- Walk results -->
    <div
      v-if="snmpWalkResults.length > 0"
      class="mt-1 max-h-52 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
    >
      <button
        v-for="entry in snmpWalkResults"
        :key="entry.oid"
        type="button"
        class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-50 dark:hover:bg-slate-700/50 flex gap-2 items-baseline"
        @click="cfg.oid = entry.oid"
      >
        <code class="text-blue-400 shrink-0">{{ entry.oid }}</code>
        <span class="text-slate-400 shrink-0">[{{ entry.type }}]</span>
        <span class="text-slate-600 dark:text-slate-300 truncate">{{ entry.value }}</span>
      </button>
    </div>
    <button
      v-if="snmpWalkHasMore && !snmpWalkLoading"
      type="button"
      class="mt-1 w-full text-xs text-center py-1 rounded border border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-500 dark:text-slate-400"
      @click="$emit('snmp-walk', true)"
    >
      {{ $t('adapters.bindingForm.snmpLoadMore', { n: snmpWalkResults.length }) }}
    </button>
    <p v-if="snmpWalkError" class="text-xs text-red-400 mt-1">{{ snmpWalkError }}</p>
    <p class="hint">
      {{ $t('adapters.bindingForm.snmpExamples') }}:
      <code class="text-blue-400 cursor-pointer hover:underline" @click="cfg.oid='1.3.6.1.2.1.1.1.0'">1.3.6.1.2.1.1.1.0</code> (sysDescr) ·
      <code class="text-blue-400 cursor-pointer hover:underline" @click="cfg.oid='1.3.6.1.2.1.1.3.0'">1.3.6.1.2.1.1.3.0</code> (sysUpTime) ·
      <code class="text-blue-400 cursor-pointer hover:underline" @click="cfg.oid='1.3.6.1.4.1'">1.3.6.1.4.1</code> (enterprises)
    </p>
  </div>

  <!-- Datentyp + Poll-Intervall -->
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.snmpDataTypeLabel') }}</label>
      <select v-model="cfg.data_type" class="input">
        <option value="auto">{{ $t('adapters.bindingForm.snmpTypeAuto') }}</option>
        <option value="int">{{ $t('adapters.bindingForm.snmpTypeInt') }}</option>
        <option value="float">{{ $t('adapters.bindingForm.snmpTypeFloat') }}</option>
        <option value="string">{{ $t('adapters.bindingForm.snmpTypeString') }}</option>
        <option value="hex">{{ $t('adapters.bindingForm.snmpTypeHex') }}</option>
        <option value="counter">{{ $t('adapters.bindingForm.snmpTypeCounter') }}</option>
        <option value="gauge">{{ $t('adapters.bindingForm.snmpTypeGauge') }}</option>
        <option value="timeticks">{{ $t('adapters.bindingForm.snmpTypeTimeticks') }}</option>
      </select>
      <p class="hint">{{ $t('adapters.bindingForm.snmpDataTypeHint') }}</p>
    </div>
    <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.snmpPollIntervalLabel') }}</label>
      <input v-model.number="cfg.poll_interval" type="number" min="1" step="1" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '30 s' }) }}</p>
    </div>
  </div>

  <div class="optional-divider">{{ $t('adapters.binding.advancedSettings') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.snmpTimeoutLabel') }}</label>
      <input v-model.number="cfg.timeout" type="number" min="0.5" max="30" step="0.5" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '5 s' }) }}</p>
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.snmpRetriesLabel') }}</label>
      <input v-model.number="cfg.retries" type="number" min="0" max="5" class="input" />
      <p class="hint">{{ $t('adapters.bindingForm.defaultN', { n: '1' }) }}</p>
    </div>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  form: { type: Object, required: true },
  selectedInstanceId: { type: [String, Number, null], default: null },
  snmpWalkRoot: { type: String, required: true },
  snmpWalkResults: { type: Array, required: true },
  snmpWalkLoading: { type: Boolean, required: true },
  snmpWalkError: { type: [String, null], default: null },
  snmpWalkHasMore: { type: Boolean, required: true },
})

defineEmits(['update:snmpWalkRoot', 'snmp-walk'])
</script>
