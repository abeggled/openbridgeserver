<template>
  <div class="section-header">{{ $t('adapters.bindingForm.onewireSection') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.onewireSensorIdLabel') }}</label>
      <input v-model="cfg.sensor_id" class="input font-mono text-sm" :placeholder="$t('adapters.bindingForm.onewireSensorIdPlaceholder')" required />
    </div>
    <div class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.onewirePropertyLabel') }}</label>
      <input v-model="cfg.property" class="input font-mono text-sm" :placeholder="$t('adapters.bindingForm.onewirePropertyPlaceholder')" />
    </div>
  </div>

  <div class="form-group">
    <button
      type="button"
      class="btn-secondary px-3 text-sm whitespace-nowrap self-start"
      :disabled="!selectedInstanceId || onewireBrowseLoading"
      @click="$emit('onewire-browse')"
    >
      <span v-if="onewireBrowseLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
      {{ onewireBrowseLoading ? $t('adapters.bindingForm.loading') : $t('adapters.bindingForm.onewireScanButton') }}
    </button>

    <div
      v-if="onewireSensors.length > 0"
      class="mt-2 max-h-72 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
    >
      <div v-for="sensor in onewireSensors" :key="sensor.rom_id" class="px-3 py-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="font-mono text-sm text-slate-700 dark:text-slate-100 truncate">{{ sensor.rom_id }}</span>
          <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 shrink-0">{{ sensor.family }}</span>
        </div>
        <div class="mt-1 flex items-center gap-2">
          <input
            :value="sensor.alias ?? ''"
            @input="$emit('update-onewire-alias-draft', { romId: sensor.rom_id, label: $event.target.value })"
            class="input text-sm flex-1"
            :placeholder="$t('adapters.bindingForm.onewireAliasPlaceholder')"
          />
          <button
            type="button"
            class="btn-secondary px-2 text-xs whitespace-nowrap"
            @click="$emit('save-onewire-alias', sensor.rom_id)"
          >
            {{ $t('adapters.bindingForm.onewireAliasSave') }}
          </button>
        </div>
        <div class="mt-2 flex flex-wrap gap-1">
          <button
            v-for="property in sensor.properties"
            :key="property"
            type="button"
            class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-200 hover:bg-blue-100 dark:hover:bg-blue-900/30 font-mono"
            @click="$emit('select-onewire-sensor', { rom_id: sensor.rom_id, property })"
          >
            {{ property }}
          </button>
        </div>
      </div>
    </div>
    <p v-if="onewireBrowseError" class="text-xs text-red-400 mt-1">{{ onewireBrowseError }}</p>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  selectedInstanceId: { type: [String, Number, null], default: null },
  onewireSensors: { type: Array, required: true },
  onewireBrowseLoading: { type: Boolean, required: true },
  onewireBrowseError: { type: [String, null], default: null },
})

defineEmits([
  'onewire-browse',
  'select-onewire-sensor',
  'update-onewire-alias-draft',
  'save-onewire-alias',
])
</script>
