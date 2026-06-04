<template>
  <div class="section-header">{{ $t('adapters.bindingForm.mqttSection') }}</div>

  <!-- Topic with browser -->
  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.topicLabel') }}</label>
    <div class="flex gap-2">
      <input v-model="cfg.topic" class="input flex-1" :placeholder="$t('adapters.bindingForm.topicPlaceholder')" required data-testid="input-mqtt-topic" />
      <button
        type="button"
        class="btn-secondary px-3 text-sm whitespace-nowrap"
        :disabled="!form.adapter_instance_id || mqttBrowseLoading"
        @click="$emit('mqtt-browse')"
      >
        <span v-if="mqttBrowseLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
        {{ mqttBrowseLoading ? $t('adapters.bindingForm.scanning') : $t('adapters.bindingForm.browse') }}
      </button>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.topicHint') }}</p>

    <!-- Browse results -->
    <div
      v-if="mqttBrowseTopics.length > 0"
      class="mt-1 max-h-44 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
    >
      <button
        v-for="t in mqttBrowseTopics"
        :key="t"
        type="button"
        class="w-full text-left px-3 py-1.5 text-sm font-mono hover:bg-slate-50 dark:hover:bg-slate-700/50 truncate"
        @click="$emit('select-mqtt-topic', t)"
      >{{ t }}</button>
    </div>
    <p v-if="mqttBrowseError" class="text-xs text-red-400 mt-1">{{ mqttBrowseError }}</p>
  </div>

  <div class="optional-divider">{{ $t('adapters.binding.optionalSettings') }}</div>
  <div class="grid grid-cols-2 gap-4">
    <!-- Publish-Topic: nur bei Lesen/Schreiben (BOTH) sichtbar -->
    <div v-if="form.direction === 'BOTH'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.publishTopicLabel') }} <span class="optional">{{ $t('logic.nodeConfig.common.optional') }}</span></label>
      <input v-model="cfg.publish_topic" class="input" :placeholder="$t('adapters.bindingForm.publishTopicPlaceholder')" />
      <p class="hint">{{ $t('adapters.bindingForm.publishTopicHint') }}</p>
    </div>
    <!-- Retain: nur bei Schreiben (DEST) oder Lesen/Schreiben (BOTH) -->
    <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="form-group flex flex-col justify-end">
      <div class="flex items-center gap-2 mt-6">
        <input type="checkbox" id="mqtt_retain" v-model="cfg.retain" class="w-4 h-4 rounded" />
        <label for="mqtt_retain" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.retainLabel') }}</label>
      </div>
      <p class="hint">{{ $t('adapters.bindingForm.retainHint') }}</p>
    </div>
  </div>

  <!-- Payload Template — only for DEST / BOTH -->
  <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.payloadTemplateLabel') }} <span class="optional">{{ $t('logic.nodeConfig.common.optional') }}</span></label>
    <input
      v-model="cfg.payload_template"
      class="input font-mono text-sm"
      :placeholder="$t('adapters.bindingForm.payloadTemplatePlaceholder')"
    />
    <p class="hint">{{ $t('adapters.bindingForm.payloadTemplateHint') }}</p>
  </div>

  <!-- Source Data Type — SOURCE / BOTH only -->
  <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.sourceDataTypeLabel') }} <span class="optional">{{ $t('logic.nodeConfig.common.optional') }}</span></label>
    <div class="flex gap-2 items-start">
      <select v-model="cfg.source_data_type" class="input flex-1" data-testid="select-source-data-type">
        <option v-for="t in mqttSourceTypes" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <span v-if="mqttTypeCompat" class="mt-1.5 shrink-0 text-xs px-2 py-1 rounded-full font-medium" :class="mqttTypeCompat.cls">
        {{ mqttTypeCompat.label }}
      </span>
    </div>
    <p class="hint">
      {{ $t('adapters.bindingForm.sourceDataTypeHint') }}
      {{ $t('adapters.bindingForm.objectTypeLabel') }}: <code class="text-blue-400">{{ dpDataType }}</code>
    </p>

    <!-- JSON key extraction panel -->
    <div v-if="cfg.source_data_type === 'json'" class="mt-3 flex flex-col gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50">
      <div class="form-group">
        <div class="flex justify-between items-center mb-1">
          <label class="text-xs font-medium text-slate-500">{{ $t('adapters.bindingForm.samplePayloadLabel') }}</label>
          <button
            type="button"
            class="text-xs text-blue-500 hover:text-blue-400 disabled:opacity-40"
            :disabled="!cfg.topic || mqttSampleLoading"
            @click="$emit('load-mqtt-sample')"
          >{{ mqttSampleLoading ? $t('adapters.bindingForm.loadingShort') : $t('adapters.bindingForm.loadFromTopic') }}</button>
        </div>
        <textarea
          :value="mqttJsonSample"
          class="input font-mono text-xs h-20 resize-y"
          :placeholder="$t('adapters.bindingForm.jsonSamplePlaceholder')"
          data-testid="mqtt-json-sample"
          @input="$emit('update:mqttJsonSample', $event.target.value); $emit('mqtt-json-sample-input')"
        />
        <p v-if="mqttJsonParseError" class="text-xs text-red-400 mt-0.5">{{ mqttJsonParseError }}</p>
      </div>
      <div class="form-group">
        <label class="text-xs font-medium text-slate-500 mb-1 block">{{ $t('adapters.bindingForm.jsonKeyLabel') }}</label>
        <div class="flex gap-2">
          <input
            v-model="cfg.json_key"
            class="input flex-1 font-mono text-sm"
            :placeholder="$t('adapters.bindingForm.jsonKeyPlaceholder')"
            data-testid="mqtt-json-key-input"
          />
          <select
            v-if="mqttJsonKeys.length"
            v-model="cfg.json_key"
            class="input w-52 shrink-0"
            data-testid="mqtt-json-key-select"
          >
            <option value="">{{ $t('adapters.bindingForm.fromSampleOption') }}</option>
            <option v-for="k in mqttJsonKeys" :key="k.key" :value="k.key">
              {{ k.key }}<template v-if="k.text"> = {{ k.text }}</template>
            </option>
          </select>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.jsonKeyHint') }}</p>
      </div>
    </div>

    <!-- XML element-path extraction panel -->
    <div v-if="cfg.source_data_type === 'xml'" class="mt-3 flex flex-col gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700/50">
      <div class="form-group">
        <div class="flex justify-between items-center mb-1">
          <label class="text-xs font-medium text-slate-500">{{ $t('adapters.bindingForm.samplePayloadLabel') }}</label>
          <button
            type="button"
            class="text-xs text-blue-500 hover:text-blue-400 disabled:opacity-40"
            :disabled="!cfg.topic || mqttSampleLoading"
            @click="$emit('load-mqtt-sample')"
          >{{ mqttSampleLoading ? $t('adapters.bindingForm.loadingShort') : $t('adapters.bindingForm.loadFromTopic') }}</button>
        </div>
        <textarea
          :value="mqttXmlSample"
          class="input font-mono text-xs h-20 resize-y"
          :placeholder="$t('adapters.bindingForm.xmlSamplePlaceholder')"
          @input="$emit('update:mqttXmlSample', $event.target.value); $emit('mqtt-xml-sample-input')"
        />
        <p v-if="mqttXmlParseError" class="text-xs text-red-400 mt-0.5">{{ mqttXmlParseError }}</p>
      </div>
      <div class="form-group">
        <label class="text-xs font-medium text-slate-500 mb-1 block">{{ $t('adapters.bindingForm.xmlPathLabel') }}</label>
        <div class="flex gap-2">
          <input
            v-model="cfg.xml_path"
            class="input flex-1 font-mono text-sm"
            :placeholder="$t('adapters.bindingForm.xmlPathPlaceholder')"
          />
          <select
            v-if="mqttXmlElements.length"
            v-model="cfg.xml_path"
            class="input w-52 shrink-0"
          >
            <option value="">{{ $t('adapters.bindingForm.fromSampleOption') }}</option>
            <option v-for="el in mqttXmlElements" :key="el.path" :value="el.path">
              {{ el.path }}<template v-if="el.text"> = {{ el.text }}</template>
            </option>
          </select>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.xmlPathHint') }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  form: { type: Object, required: true },
  mqttSourceTypes: { type: Array, required: true },
  mqttTypeCompat: { type: Object, default: null },
  dpDataType: { type: String, required: true },
  mqttBrowseTopics: { type: Array, required: true },
  mqttBrowseLoading: { type: Boolean, required: true },
  mqttBrowseError: { type: [String, null], default: null },
  mqttJsonSample: { type: String, required: true },
  mqttJsonKeys: { type: Array, required: true },
  mqttJsonParseError: { type: [String, null], default: null },
  mqttXmlSample: { type: String, required: true },
  mqttXmlElements: { type: Array, required: true },
  mqttXmlParseError: { type: [String, null], default: null },
  mqttSampleLoading: { type: Boolean, required: true },
})

defineEmits([
  'mqtt-browse',
  'select-mqtt-topic',
  'load-mqtt-sample',
  'mqtt-json-sample-input',
  'mqtt-xml-sample-input',
  'update:mqttJsonSample',
  'update:mqttXmlSample',
])
</script>
