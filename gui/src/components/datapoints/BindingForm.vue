<template>
  <form @submit.prevent="submit" class="flex flex-col gap-4">

    <!-- Tab-Leiste -->
    <div class="flex gap-0 border-b border-slate-200 dark:border-slate-700 -mt-1">
      <button
        v-for="tab in visibleTabs" :key="tab.id"
        type="button"
        @click="activeTab = tab.id"
        class="tab-btn"
        :class="{ 'tab-active': activeTab === tab.id }"
      >
        {{ tab.label }}
        <span v-if="tab.badge" class="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-blue-400"></span>
      </button>
    </div>

    <!-- ── TAB: Verbindung ── -->
    <div v-show="activeTab === 'conn'" class="flex flex-col gap-4">

      <div class="grid gap-4" :class="selectedAdapterType === 'ANWESENHEITSSIMULATION' ? 'grid-cols-1' : 'grid-cols-2'">
        <div class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.adapterInstanceLabel') }}</label>
          <div v-if="props.initial" class="input bg-slate-100 dark:bg-slate-800/50 text-slate-400 cursor-not-allowed">
            {{ currentInstanceName }}
          </div>
          <select v-else v-model="form.adapter_instance_id" class="input" required data-testid="select-adapter-instance">
            <option value="">{{ $t('adapters.bindingForm.selectInstance') }}</option>
            <optgroup v-for="group in groupedInstances" :key="group.type" :label="group.type">
              <option v-for="inst in group.items" :key="inst.id" :value="inst.id">{{ inst.name }}</option>
            </optgroup>
          </select>
        </div>
        <div v-if="selectedAdapterType !== 'ANWESENHEITSSIMULATION'" class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.directionLabel') }}</label>
          <select
            v-model="form.direction"
            class="input"
            :disabled="selectedAdapterType === 'ZEITSCHALTUHR'"
            data-testid="select-direction"
          >
            <option value="SOURCE">{{ $t('adapters.bindingForm.directionRead') }}</option>
            <option v-if="selectedAdapterType !== 'ZEITSCHALTUHR'" value="DEST">{{ $t('adapters.bindingForm.directionWrite') }}</option>
            <option v-if="selectedAdapterType !== 'ZEITSCHALTUHR'" value="BOTH">{{ $t('adapters.bindingForm.directionReadWrite') }}</option>
          </select>
          <p v-if="selectedAdapterType === 'ZEITSCHALTUHR'" class="hint">
            {{ $t('adapters.bindingForm.timerReadOnlyHint') }}
          </p>
        </div>
      </div>

      <!-- KNX -->
      <template v-if="selectedAdapterType === 'KNX'">
        <div class="section-header">{{ $t('adapters.bindingForm.knxSection') }}</div>
        <div class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.groupAddressLabel') }}</label>
          <GaCombobox v-model="cfg.group_address" :placeholder="$t('adapters.bindingForm.groupAddressPlaceholder')" @select="onGaSelect" />
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
            :disabled="!props.dpPersistValue"
            class="w-4 h-4 rounded mt-0.5"
          />
          <div>
            <label
              for="respond_to_read"
              class="text-sm"
              :class="props.dpPersistValue ? 'text-slate-600 dark:text-slate-300' : 'text-slate-400 dark:text-slate-500 cursor-not-allowed'"
            >{{ $t('adapters.bindingForm.respondToReadLabel') }}</label>
            <p class="hint">
              {{ $t('adapters.bindingForm.respondToReadHint') }}
              <template v-if="!props.dpPersistValue"> {{ $t('adapters.bindingForm.respondToReadPersistHint') }}</template>
            </p>
          </div>
        </div>
      </template>

      <!-- Modbus -->
      <template v-if="selectedAdapterType === 'MODBUS_TCP' || selectedAdapterType === 'MODBUS_RTU'">
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

      <!-- MQTT -->
      <template v-if="selectedAdapterType === 'MQTT'">
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
              @click="mqttBrowse"
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
              @click="selectMqttTopic(t)"
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
              <option v-for="t in MQTT_SOURCE_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
            </select>
            <span v-if="mqttTypeCompat" class="mt-1.5 shrink-0 text-xs px-2 py-1 rounded-full font-medium" :class="mqttTypeCompat.cls">
              {{ mqttTypeCompat.label }}
            </span>
          </div>
          <p class="hint">
            {{ $t('adapters.bindingForm.sourceDataTypeHint') }}
            {{ $t('adapters.bindingForm.objectTypeLabel') }}: <code class="text-blue-400">{{ props.dpDataType }}</code>
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
                  @click="loadMqttSample"
                >{{ mqttSampleLoading ? $t('adapters.bindingForm.loadingShort') : $t('adapters.bindingForm.loadFromTopic') }}</button>
              </div>
              <textarea
                v-model="mqttJsonSample"
                class="input font-mono text-xs h-20 resize-y"
                :placeholder="$t('adapters.bindingForm.jsonSamplePlaceholder')"
                data-testid="mqtt-json-sample"
                @input="onMqttJsonSampleInput"
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
                  @click="loadMqttSample"
                >{{ mqttSampleLoading ? $t('adapters.bindingForm.loadingShort') : $t('adapters.bindingForm.loadFromTopic') }}</button>
              </div>
              <textarea
                v-model="mqttXmlSample"
                class="input font-mono text-xs h-20 resize-y"
                :placeholder="$t('adapters.bindingForm.xmlSamplePlaceholder')"
                @input="onMqttXmlSampleInput"
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

      <!-- 1-Wire -->
      <template v-if="selectedAdapterType === 'ONEWIRE'">
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

      <!-- Home Assistant -->
      <template v-if="selectedAdapterType === 'HOME_ASSISTANT'">
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

      <!-- ioBroker -->
      <template v-if="selectedAdapterType === 'IOBROKER'">
        <div class="section-header">{{ $t('adapters.bindingForm.iobSection') }}</div>
        <div class="form-group">
          <label class="label">{{ $t('adapters.bindingForm.iobStateIdLabel') }}</label>
          <div class="flex gap-2">
            <input
              v-model="cfg.state_id"
              class="input font-mono text-sm flex-1"
              :placeholder="$t('adapters.bindingForm.iobStateIdPlaceholder')"
              data-testid="config-field-state_id"
              required
              @input="onIoBrokerStateInput"
            />
            <button
              type="button"
              class="btn-secondary px-3 text-sm whitespace-nowrap"
              :disabled="!selectedInstanceId || iobrokerBrowseLoading"
              @click="browseIoBrokerStates"
            >
              <span v-if="iobrokerBrowseLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
              {{ iobrokerBrowseLoading ? $t('adapters.bindingForm.loading') : $t('adapters.bindingForm.browse') }}
            </button>
          </div>
          <p class="hint">{{ $t('adapters.bindingForm.iobStateHint') }}</p>

          <div
            v-if="iobrokerStates.length > 0"
            class="mt-2 max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
          >
            <button
              v-for="state in iobrokerStates"
              :key="state.id"
              type="button"
              class="w-full text-left px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-700/50"
              @click="selectIoBrokerState(state)"
            >
              <div class="flex items-center gap-2 min-w-0">
                <span class="font-mono text-sm text-slate-700 dark:text-slate-100 truncate">{{ state.id }}</span>
                <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 shrink-0">{{ state.type || $t('adapters.bindingForm.iobAutoType') }}</span>
                <span v-if="state.write" class="text-[11px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 shrink-0">{{ $t('adapters.bindingForm.iobWriteTag') }}</span>
              </div>
              <div class="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                <span class="truncate">{{ state.name || '—' }}</span>
                <span v-if="state.role" class="shrink-0">{{ state.role }}</span>
                <span v-if="state.value !== null && state.value !== undefined" class="font-mono shrink-0">= {{ state.value }}</span>
              </div>
            </button>
          </div>
          <p v-if="iobrokerBrowseError" class="text-xs text-red-400 mt-1">{{ iobrokerBrowseError }}</p>
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div v-if="form.direction === 'SOURCE' || form.direction === 'BOTH'" class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.iobDataTypeLabel') }}</label>
            <select v-model="cfg.source_data_type" class="input">
              <option value="">{{ $t('adapters.bindingForm.iobAutoTypeLabel') }}</option>
              <option value="bool">{{ $t('adapters.bindingForm.iobTypeBool') }}</option>
              <option value="float">{{ $t('adapters.bindingForm.iobTypeFloat') }}</option>
              <option value="int">{{ $t('adapters.bindingForm.iobTypeInt') }}</option>
              <option value="string">{{ $t('adapters.bindingForm.iobTypeString') }}</option>
              <option value="json">{{ $t('adapters.bindingForm.iobTypeJson') }}</option>
            </select>
            <p class="hint">{{ $t('adapters.bindingForm.iobDataTypeHint') }}</p>
          </div>
          <div v-if="cfg.source_data_type === 'json' && (form.direction === 'SOURCE' || form.direction === 'BOTH')" class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.iobJsonKeyLabel') }}</label>
            <input v-model="cfg.json_key" class="input" :placeholder="$t('adapters.bindingForm.iobJsonKeyPlaceholder')" />
            <p class="hint">{{ $t('adapters.bindingForm.iobJsonKeyHint') }}</p>
          </div>
        </div>

        <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="optional-divider">{{ $t('adapters.bindingForm.iobWriteSection') }}</div>
        <div v-if="form.direction === 'DEST' || form.direction === 'BOTH'" class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.iobCommandStateLabel') }} <span class="optional">{{ $t('adapters.bindingForm.iobOptional') }}</span></label>
            <input
              v-model="cfg.command_state_id"
              class="input font-mono text-sm"
              :placeholder="$t('adapters.bindingForm.iobCommandStatePlaceholder')"
            />
            <p class="hint">{{ $t('adapters.bindingForm.iobCommandStateHint') }}</p>
          </div>
          <div class="form-group flex flex-col justify-end">
            <label class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300 mt-6">
              <input type="checkbox" v-model="cfg.ack" class="w-4 h-4 rounded" />
              {{ $t('adapters.bindingForm.iobWriteWithAckLabel') }}
            </label>
            <p class="hint">{{ $t('adapters.bindingForm.iobWriteWithAckHint') }}</p>
          </div>
        </div>

        <button
          type="button"
          class="text-sm text-blue-500 hover:text-blue-400 self-start"
          @click="showAdvancedTabs = !showAdvancedTabs"
        >
          {{ showAdvancedTabs ? $t('adapters.bindingForm.hideAdvancedOptions') : $t('adapters.bindingForm.showAdvancedOptions') }}
        </button>
      </template>

      <!-- Zeitschaltuhr -->
      <template v-if="selectedAdapterType === 'ZEITSCHALTUHR'">
        <div class="section-header">{{ $t('adapters.bindingForm.ztSection') }}</div>

        <!-- Typ -->
        <div class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.ztTypeLabel') }}</label>
            <select v-model="cfg.timer_type" class="input">
              <option value="daily">{{ $t('adapters.bindingForm.ztTypeDaily') }}</option>
              <option value="annual">{{ $t('adapters.bindingForm.ztTypeAnnual') }}</option>
              <option value="holiday">{{ $t('adapters.bindingForm.ztTypeHoliday') }}</option>
              <option value="meta">{{ $t('adapters.bindingForm.ztTypeMeta') }}</option>
            </select>
          </div>
          <div v-if="cfg.timer_type === 'meta'" class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.ztMetaTypeLabel') }}</label>
            <select v-model="cfg.meta_type" class="input">
              <optgroup :label="$t('adapters.bindingForm.ztMetaHolidayGroup')">
                <option value="holiday_today">{{ $t('adapters.bindingForm.ztMetaHolidayToday') }}</option>
                <option value="holiday_tomorrow">{{ $t('adapters.bindingForm.ztMetaHolidayTomorrow') }}</option>
                <option value="holiday_name_today">{{ $t('adapters.bindingForm.ztMetaHolidayNameToday') }}</option>
                <option value="holiday_name_tomorrow">{{ $t('adapters.bindingForm.ztMetaHolidayNameTomorrow') }}</option>
              </optgroup>
              <optgroup :label="$t('adapters.bindingForm.ztMetaVacationGroup')">
                <option value="vacation_1">{{ $t('adapters.bindingForm.ztMetaVacation1') }}</option>
                <option value="vacation_2">{{ $t('adapters.bindingForm.ztMetaVacation2') }}</option>
                <option value="vacation_3">{{ $t('adapters.bindingForm.ztMetaVacation3') }}</option>
                <option value="vacation_4">{{ $t('adapters.bindingForm.ztMetaVacation4') }}</option>
                <option value="vacation_5">{{ $t('adapters.bindingForm.ztMetaVacation5') }}</option>
                <option value="vacation_6">{{ $t('adapters.bindingForm.ztMetaVacation6') }}</option>
              </optgroup>
            </select>
            <p class="hint">{{ $t('adapters.bindingForm.ztMetaHint') }}</p>
          </div>
        </div>

        <template v-if="cfg.timer_type !== 'meta'">

          <!-- Feiertagsschaltuhr: Feiertagsauswahl -->
          <template v-if="cfg.timer_type === 'holiday'">
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztHolidaysLabel') }} <span class="optional">{{ $t('adapters.bindingForm.ztHolidaysOptional') }}</span></label>
              <p class="hint mb-2">{{ $t('adapters.bindingForm.ztHolidaysHint') }}</p>
              <div v-if="ztHolidaysLoading" class="text-xs text-slate-400 py-2">{{ $t('adapters.bindingForm.ztHolidaysLoading') }}</div>
              <div v-else-if="ztHolidaysError" class="text-xs text-red-400 py-2">{{ ztHolidaysError }}</div>
              <div v-else-if="ztHolidays.length === 0" class="text-xs text-slate-400 italic py-2">{{ $t('adapters.bindingForm.ztHolidaysEmpty') }}</div>
              <div v-else class="space-y-0.5 max-h-56 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded p-2 bg-white dark:bg-slate-800/50">
                <label
                  v-for="h in ztHolidays"
                  :key="h.name"
                  class="flex items-center gap-2 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-700/40 px-1.5 py-1 rounded text-xs"
                >
                  <input
                    type="checkbox"
                    :checked="cfg.selected_holidays.length === 0 || cfg.selected_holidays.includes(h.name)"
                    class="w-3.5 h-3.5 rounded flex-shrink-0"
                    @change="ztToggleHoliday(h.name)"
                  />
                  <span class="font-mono text-slate-400 dark:text-slate-500 flex-shrink-0">{{ h.date }}</span>
                  <span class="text-slate-700 dark:text-slate-200 truncate">{{ h.name }}</span>
                </label>
              </div>
              <div class="flex gap-3 mt-1.5 items-center">
                <button type="button" class="text-xs text-slate-400 hover:text-blue-400" @click="cfg.selected_holidays = []">{{ $t('adapters.bindingForm.ztHolidaysAllNoFilter') }}</button>
                <span class="text-xs text-slate-300 dark:text-slate-600">·</span>
                <span class="text-xs text-slate-400">
                  {{ cfg.selected_holidays.length === 0 ? $t('adapters.bindingForm.ztHolidaysAllSelected') : $t('adapters.bindingForm.ztHolidaysCount', { n: cfg.selected_holidays.length }) }}
                </span>
                <button type="button" class="text-xs text-slate-400 hover:text-blue-400 ml-auto" @click="loadZsuHolidays()">{{ $t('adapters.bindingForm.ztReload') }}</button>
              </div>
            </div>
          </template>

          <!-- Wochentage (nicht für Feiertagsschaltuhr) -->
          <div v-if="cfg.timer_type !== 'holiday'" class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.ztWeekdaysLabel') }}</label>
            <div class="flex gap-1.5 flex-wrap">
              <button
                v-for="(label, idx) in WEEKDAY_SHORTS"
                :key="idx"
                type="button"
                @click="ztToggleWeekday(idx)"
                class="px-3 py-1.5 text-xs font-medium rounded-md border transition-colors"
                :class="cfg.weekdays.includes(idx)
                  ? 'bg-blue-500 border-blue-500 text-white'
                  : 'bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:border-blue-400'"
              >{{ label }}</button>
              <button type="button" class="ml-2 text-xs text-slate-400 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4,5,6]">{{ $t('adapters.bindingForm.ztAll') }}</button>
              <button type="button" class="text-xs text-slate-400 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4]">{{ $t('adapters.bindingForm.ztWeekdaysWorkweek') }}</button>
              <button type="button" class="text-xs text-slate-400 hover:text-blue-400" @click="cfg.weekdays = [5,6]">{{ $t('adapters.bindingForm.ztWeekdaysWeekend') }}</button>
            </div>
          </div>

          <!-- Monate + Tag (nur Jahresschaltuhr, nicht bei Feiertagsschaltuhr) -->
          <template v-if="cfg.timer_type === 'annual'">
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztMonthsLabel') }} <span class="optional">{{ $t('adapters.bindingForm.ztMonthsOptional') }}</span></label>
              <div class="flex gap-1.5 flex-wrap">
                <button
                  v-for="(label, idx) in MONTH_SHORTS"
                  :key="idx+1"
                  type="button"
                  @click="ztToggleMonth(idx+1)"
                  class="px-2.5 py-1.5 text-xs font-medium rounded-md border transition-colors"
                  :class="cfg.months.includes(idx+1)
                    ? 'bg-blue-500 border-blue-500 text-white'
                    : 'bg-white dark:bg-slate-800 border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:border-blue-400'"
                >{{ label }}</button>
                <button type="button" class="ml-2 text-xs text-slate-400 hover:text-blue-400" @click="cfg.months = []">{{ $t('adapters.bindingForm.ztAll') }}</button>
              </div>
            </div>
            <div class="form-group" style="max-width:160px">
              <label class="label">{{ $t('adapters.bindingForm.ztDayOfMonthLabel') }} <span class="optional">{{ $t('adapters.bindingForm.ztDayOfMonthOptional') }}</span></label>
              <input v-model.number="cfg.day_of_month" type="number" min="0" max="31" class="input" />
            </div>
          </template>

          <!-- Zeitreferenz -->
          <div class="optional-divider">{{ $t('adapters.bindingForm.ztTimepointDivider') }}</div>
          <div class="grid grid-cols-2 gap-4">
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztTimeRefLabel') }}</label>
              <select v-model="cfg.time_ref" class="input">
                <option value="absolute">{{ $t('adapters.bindingForm.ztTimeRefAbsolute') }}</option>
                <option value="sunrise">{{ $t('adapters.bindingForm.ztTimeRefSunrise') }}</option>
                <option value="sunset">{{ $t('adapters.bindingForm.ztTimeRefSunset') }}</option>
                <option value="solar_noon">{{ $t('adapters.bindingForm.ztTimeRefSolarNoon') }}</option>
                <option value="solar_altitude">{{ $t('adapters.bindingForm.ztTimeRefSolarAltitude') }}</option>
              </select>
            </div>
          </div>

          <!-- Absolute Zeit -->
          <div v-if="cfg.time_ref === 'absolute'" class="grid grid-cols-2 gap-4">
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztHourLabel') }}</label>
              <input v-model.number="cfg.hour" type="number" min="0" max="23" class="input" />
            </div>
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztMinuteLabel') }}</label>
              <input v-model.number="cfg.minute" type="number" min="0" max="59" class="input" />
            </div>
          </div>

          <!-- Offset (bei allen nicht-absoluten Zeitreferenzen) -->
          <div v-if="cfg.time_ref !== 'absolute'" class="form-group" style="max-width:200px">
            <label class="label">{{ $t('adapters.bindingForm.ztOffsetMinutesLabel') }}</label>
            <input v-model.number="cfg.offset_minutes" type="number" class="input" placeholder="0" />
            <p class="hint">{{ $t('adapters.bindingForm.ztOffsetMinutesHint') }}</p>
          </div>

          <!-- Sonnenhöhenwinkel -->
          <div v-if="cfg.time_ref === 'solar_altitude'" class="grid grid-cols-2 gap-4">
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztSolarAltitudeLabel') }}</label>
              <input v-model.number="cfg.solar_altitude_deg" type="number" min="-18" max="90" step="0.5" class="input" />
              <p class="hint">{{ $t('adapters.bindingForm.ztSolarAltitudeHint') }}</p>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztSunDirectionLabel') }}</label>
              <select v-model="cfg.sun_direction" class="input">
                <option value="rising">{{ $t('adapters.bindingForm.ztSunDirectionRising') }}</option>
                <option value="setting">{{ $t('adapters.bindingForm.ztSunDirectionSetting') }}</option>
              </select>
            </div>
          </div>

          <!-- Takt -->
          <div class="optional-divider">{{ $t('adapters.bindingForm.ztTickDivider') }} <span class="font-normal text-slate-400">{{ $t('adapters.bindingForm.ztTickDividerHint') }}</span></div>
          <div class="grid grid-cols-2 gap-4">
            <div class="flex items-start gap-2">
              <input type="checkbox" id="zt_every_minute" v-model="cfg.every_minute" class="w-4 h-4 rounded mt-0.5" />
              <div>
                <label for="zt_every_minute" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.ztEveryMinuteLabel') }}</label>
                <p class="hint">{{ $t('adapters.bindingForm.ztEveryMinuteHint') }}</p>
              </div>
            </div>
            <div class="flex items-start gap-2">
              <input type="checkbox" id="zt_every_hour" v-model="cfg.every_hour" class="w-4 h-4 rounded mt-0.5" />
              <div>
                <label for="zt_every_hour" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.ztEveryHourLabel') }}</label>
                <p class="hint">{{ $t('adapters.bindingForm.ztEveryHourHint') }}</p>
              </div>
            </div>
          </div>
          <div v-if="cfg.every_hour && !cfg.every_minute" class="form-group" style="max-width:160px">
            <label class="label">{{ $t('adapters.bindingForm.ztAtMinuteLabel') }}</label>
            <input v-model.number="cfg.minute" type="number" min="0" max="59" class="input" />
          </div>

          <!-- Feiertag / Ferien -->
          <div class="optional-divider">{{ $t('adapters.bindingForm.ztHolidayVacationDivider') }}</div>
          <div class="grid grid-cols-2 gap-4">
            <div v-if="cfg.timer_type !== 'holiday'" class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztHolidayModeLabel') }}</label>
              <select v-model="cfg.holiday_mode" class="input">
                <option value="ignore">{{ $t('adapters.bindingForm.ztModeIgnore') }}</option>
                <option value="skip">{{ $t('adapters.bindingForm.ztHolidayModeSkip') }}</option>
                <option value="only">{{ $t('adapters.bindingForm.ztHolidayModeOnly') }}</option>
                <option value="as_sunday">{{ $t('adapters.bindingForm.ztHolidayModeAsSunday') }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="label">{{ $t('adapters.bindingForm.ztVacationModeLabel') }}</label>
              <select v-model="cfg.vacation_mode" class="input">
                <option value="ignore">{{ $t('adapters.bindingForm.ztModeIgnore') }}</option>
                <option value="skip">{{ $t('adapters.bindingForm.ztVacationModeSkip') }}</option>
                <option value="only">{{ $t('adapters.bindingForm.ztVacationModeOnly') }}</option>
                <option value="as_sunday">{{ $t('adapters.bindingForm.ztVacationModeAsSunday') }}</option>
              </select>
            </div>
          </div>

          <!-- Datum-Fenster -->
          <div class="optional-divider">{{ $t('adapters.bindingForm.ztDateWindowDivider') }}</div>
          <div class="flex items-start gap-2">
            <input type="checkbox" id="zt_date_window" v-model="cfg.date_window_enabled" class="w-4 h-4 rounded mt-0.5" />
            <div>
              <label for="zt_date_window" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.ztDateWindowEnableLabel') }}</label>
              <p class="hint">{{ $t('adapters.bindingForm.ztDateWindowEnableHint') }}</p>
            </div>
          </div>
          <template v-if="cfg.date_window_enabled">
            <template v-for="(ep, epLabel) in [{ ep: winFrom, label: $t('adapters.bindingForm.ztDateWindowFrom') }, { ep: winTo, label: $t('adapters.bindingForm.ztDateWindowTo') }]" :key="epLabel">
              <div class="form-group">
                <label class="label">{{ ep.label }}</label>
                <div class="flex gap-2 flex-wrap items-center">
                  <select v-model="ep.ep.type" class="input text-xs" style="width:160px" @change="onWinTypeChange(ep.ep)">
                    <option value="fixed">{{ $t('adapters.bindingForm.ztDateTypeFixed') }}</option>
                    <option value="easter">{{ $t('adapters.bindingForm.ztDateTypeEaster') }}</option>
                    <option value="advent">{{ $t('adapters.bindingForm.ztDateTypeAdvent') }}</option>
                    <option value="holiday_name">{{ $t('adapters.bindingForm.ztDateTypeHolidayName') }}</option>
                  </select>
                  <template v-if="ep.ep.type === 'fixed'">
                    <select v-model.number="ep.ep.month" class="input text-xs" style="width:110px">
                      <option v-for="m in WIN_MONTHS" :key="m.v" :value="m.v">{{ m.l }}</option>
                    </select>
                    <input v-model.number="ep.ep.day" type="number" min="1" max="31" class="input text-xs" style="width:56px" />
                  </template>
                  <template v-else-if="ep.ep.type === 'easter' || ep.ep.type === 'advent'">
                    <select v-model="ep.ep.sign" class="input text-xs" style="width:48px">
                      <option value="+">+</option>
                      <option value="-">−</option>
                    </select>
                    <input v-model.number="ep.ep.offset" type="number" min="0" max="400" class="input text-xs" style="width:64px" />
                    <span class="text-xs text-slate-400">{{ $t('adapters.bindingForm.ztDaysLabel') }}</span>
                  </template>
                  <template v-else-if="ep.ep.type === 'holiday_name'">
                    <div v-if="ztHolidaysLoading" class="text-xs text-slate-400">{{ $t('adapters.bindingForm.loading') }}</div>
                    <select v-else v-model="ep.ep.name" class="input text-xs flex-1" style="min-width:0">
                      <option value="">{{ $t('adapters.bindingForm.ztSelectHoliday') }}</option>
                      <option v-for="h in ztHolidays" :key="h.name" :value="h.name">{{ h.date }} · {{ h.name }}</option>
                    </select>
                    <select v-model="ep.ep.sign" class="input text-xs" style="width:48px">
                      <option value="+">+</option>
                      <option value="-">−</option>
                    </select>
                    <input v-model.number="ep.ep.offset" type="number" min="0" max="400" placeholder="0" class="input text-xs" style="width:64px" />
                    <span class="text-xs text-slate-400">{{ $t('adapters.bindingForm.ztDaysLabel') }}</span>
                  </template>
                </div>
                <p class="hint">{{ describeWinEp(ep.ep) }}</p>
              </div>
            </template>
            <div v-if="buildWinExpr(winFrom) && buildWinExpr(winTo)" class="text-xs font-mono text-blue-500 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1">
              {{ buildWinExpr(winFrom) }} → {{ buildWinExpr(winTo) }}
            </div>
          </template>

          <!-- Ausgabewert -->
          <div class="optional-divider">{{ $t('adapters.bindingForm.ztOutputDivider') }}</div>
          <div class="form-group" style="max-width:200px">
            <label class="label">{{ $t('adapters.bindingForm.ztOutputValueLabel') }}</label>
            <input v-model="cfg.value" class="input" placeholder="1" />
            <p class="hint">{{ $t('adapters.bindingForm.ztOutputValueHint') }}</p>
          </div>

        </template><!-- /timer_type !== meta -->
      </template>

      <!-- Anwesenheitssimulation — per-Binding Overrides -->
      <template v-if="selectedAdapterType === 'ANWESENHEITSSIMULATION'">
        <div class="section-header">{{ $t('adapters.bindingForm.anwSection') }}</div>
        <div class="grid grid-cols-2 gap-4">
          <div class="form-group">
            <label class="label">{{ $t('adapters.bindingForm.anwOffsetOverrideLabel') }}</label>
            <div class="flex gap-2">
              <select v-model="anwOffsetSelect" class="input" @change="onAnwOffsetSelectChange">
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
                @input="onAnwOffsetCustomInput"
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

      <!-- SNMP -->
      <template v-if="selectedAdapterType === 'SNMP'">
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
              v-model="snmpWalkRoot"
              class="input flex-1 font-mono text-xs"
              :placeholder="$t('adapters.bindingForm.snmpWalkRootPlaceholder')"
            />
            <button
              type="button"
              class="btn-secondary px-3 text-sm whitespace-nowrap"
              :disabled="!cfg.host || !selectedInstanceId || snmpWalkLoading"
              @click="snmpWalk"
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
            @click="snmpWalk(true)"
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

      <div v-if="!selectedAdapterType && !props.initial" class="p-3 bg-slate-100/80 dark:bg-slate-800/40 rounded-lg text-sm text-slate-500 text-center">
        {{ $t('adapters.bindingForm.selectAdapterInstanceFirst') }}
      </div>

    </div><!-- /TAB Verbindung -->

    <!-- ── TAB: Transformation ── -->
    <div v-show="activeTab === 'transform'" class="flex flex-col gap-4">
      <div class="section-header">{{ $t('adapters.bindingForm.transformSection') }}</div>
      <div class="form-group">
        <label class="label">
          {{ $t('adapters.bindingForm.formulaLabel') }}
          <span class="text-slate-500 font-normal ml-1">— {{ $t('adapters.bindingForm.formulaVariable') }}: <code class="text-blue-400">x</code></span>
        </label>
        <div class="flex gap-2">
          <select class="input w-52 shrink-0" v-model="form.formula_preset" @change="onPresetSelect">
            <option value="">{{ $t('adapters.bindingForm.formulaPresetSelect') }}</option>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupMultiply')">
              <option value="x * 86400">{{ $t('adapters.bindingForm.formulaMul86400') }}</option>
              <option value="x * 3600">{{ $t('adapters.bindingForm.formulaMul3600') }}</option>
              <option value="x * 1440">{{ $t('adapters.bindingForm.formulaMul1440') }}</option>
              <option value="x * 1000">× 1.000</option>
              <option value="x * 100">× 100</option>
              <option value="x * 60">{{ $t('adapters.bindingForm.formulaMul60') }}</option>
              <option value="x * 10">× 10</option>
            </optgroup>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupDivide')">
              <option value="x / 10">{{ $t('adapters.bindingForm.formulaDiv10') }}</option>
              <option value="x / 60">{{ $t('adapters.bindingForm.formulaDiv60') }}</option>
              <option value="x / 100">{{ $t('adapters.bindingForm.formulaDiv100') }}</option>
              <option value="x / 1000">{{ $t('adapters.bindingForm.formulaDiv1000') }}</option>
              <option value="x / 1440">{{ $t('adapters.bindingForm.formulaDiv1440') }}</option>
              <option value="x / 3600">{{ $t('adapters.bindingForm.formulaDiv3600') }}</option>
              <option value="x / 86400">{{ $t('adapters.bindingForm.formulaDiv86400') }}</option>
            </optgroup>
            <optgroup :label="$t('adapters.bindingForm.formulaGroupCustom')">
              <option value="__custom__">{{ $t('adapters.bindingForm.formulaCustom') }}</option>
            </optgroup>
          </select>
          <input
            v-model="form.value_formula"
            type="text"
            :placeholder="$t('adapters.bindingForm.formulaPlaceholder')"
            class="input flex-1 font-mono text-sm"
            @input="form.formula_preset = '__custom__'"
          />
        </div>
        <p class="hint mt-1">
          {{ $t('adapters.bindingForm.formulaHintPrefix') }} <code class="text-blue-400">abs round min max sqrt floor ceil</code>
          {{ $t('adapters.bindingForm.formulaHintSuffix') }} <code class="text-blue-400">math.*</code>{{ $t('adapters.bindingForm.formulaHintEnd') }}
        </p>
      </div>

      <div class="optional-divider">{{ $t('adapters.bindingForm.valueMapDivider') }}</div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.valueMapLabel') }} <span class="optional">{{ $t('adapters.bindingForm.iobOptional') }}</span></label>
        <select v-model="form.value_map_preset" class="input" @change="onValueMapPresetChange">
          <option v-for="p in VALUE_MAP_PRESETS" :key="p.key" :value="p.key">{{ p.label }}</option>
        </select>
        <div v-if="form.value_map_preset === 'custom'" class="mt-2">
          <textarea
            v-model="form.value_map_custom"
            @input="onValueMapCustomInput"
            class="input font-mono text-sm h-28 resize-y"
            :placeholder="$t('adapters.bindingForm.valueMapCustomPlaceholder')"
          />
          <p v-if="form.value_map_custom_error" class="text-xs text-red-400 mt-0.5">{{ form.value_map_custom_error }}</p>
          <p class="hint">{{ $t('adapters.bindingForm.valueMapCustomHint') }}</p>
        </div>
        <p class="hint mt-1">{{ $t('adapters.bindingForm.valueMapHint') }}</p>
      </div>
    </div><!-- /TAB Transformation -->

    <!-- ── TAB: Filter ── -->
    <div v-show="activeTab === 'filter'" class="flex flex-col gap-4">
      <div class="section-header">{{ $t('adapters.bindingForm.timeFilterSection') }}</div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.throttleLabel') }}</label>
        <div class="flex gap-2">
          <input v-model.number="form.throttle_value" type="number" min="0" step="1" :placeholder="$t('adapters.bindingForm.throttlePlaceholder')" class="input flex-1" />
          <select v-model="form.throttle_unit" class="input w-24">
            <option value="ms">ms</option>
            <option value="s">s</option>
            <option value="min">min</option>
            <option value="h">h</option>
          </select>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.throttleHint') }}</p>
      </div>

      <div class="section-header">{{ $t('adapters.bindingForm.valueFilterSection') }}</div>
      <div class="flex items-center gap-2">
        <input type="checkbox" id="send_on_change" v-model="form.send_on_change" class="w-4 h-4 rounded" />
        <label for="send_on_change" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('adapters.bindingForm.sendOnChangeLabel') }}</label>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.bindingForm.minDeltaLabel') }}</label>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-slate-400 mb-1 block">{{ $t('adapters.bindingForm.minDeltaAbsolute') }}</label>
            <input v-model.number="form.send_min_delta" type="number" min="0" step="any" :placeholder="$t('adapters.bindingForm.minDeltaAbsolutePlaceholder')" class="input" />
          </div>
          <div>
            <label class="text-xs text-slate-400 mb-1 block">{{ $t('adapters.bindingForm.minDeltaRelative') }}</label>
            <input v-model.number="form.send_min_delta_pct" type="number" min="0" step="any" :placeholder="$t('adapters.bindingForm.minDeltaRelativePlaceholder')" class="input" />
          </div>
        </div>
        <p class="hint">{{ $t('adapters.bindingForm.minDeltaHint') }}</p>
      </div>
    </div><!-- /TAB Filter -->

    <!-- Aktiviert -->
    <div class="flex items-center gap-2 border-t border-slate-200 dark:border-slate-700/60 pt-3">
      <input type="checkbox" id="enabled" v-model="form.enabled" class="w-4 h-4 rounded" />
      <label for="enabled" class="text-sm text-slate-600 dark:text-slate-300">{{ $t('common.enabled') }}</label>
    </div>

    <div v-if="error" class="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">{{ error }}</div>

    <div class="flex justify-end gap-3">
      <button type="button" @click="$emit('cancel')" class="btn-secondary">{{ $t('common.cancel') }}</button>
      <button type="submit" class="btn-primary" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        {{ $t('common.save') }}
      </button>
    </div>

  </form>
</template>

<script setup>
import { ref, reactive, watch, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { dpApi, adapterApi } from '@/api/client'
import Spinner    from '@/components/ui/Spinner.vue'
import GaCombobox from '@/components/ui/GaCombobox.vue'

const props = defineProps({
  dpId:           { type: String,  required: true },
  initial:        { type: Object,  default: null },
  dpPersistValue: { type: Boolean, default: false },
  dpDataType:     { type: String,  default: 'UNKNOWN' },  // DataPoint.data_type for compat check
})
const emit = defineEmits(['save', 'cancel'])
const { t } = useI18n()

const saving       = ref(false)
const error        = ref(null)
const allInstances = ref([])
const allDpts      = ref([])
const activeTab    = ref('conn')
const showAdvancedTabs = ref(false)
const anwOffsetSelect  = ref('')  // '' | '1' | '7' | '14' | 'custom'

// ---------------------------------------------------------------------------
// Form-State
// ---------------------------------------------------------------------------

const THROTTLE_FACTORS = { ms: 1, s: 1000, min: 60_000, h: 3_600_000 }

const form = reactive({
  adapter_instance_id:  '',
  direction:            'SOURCE',
  enabled:              true,
  value_formula:        '',
  formula_preset:       '',
  value_map_preset:     '',
  value_map_custom:     '',
  value_map_custom_error: '',
  throttle_value:       0,
  throttle_unit:        's',
  send_on_change:       false,
  send_min_delta:       null,
  send_min_delta_pct:   null,
})

const VALUE_MAP_PRESETS = [
  { key: '',            label: t('adapters.bindingForm.noValueMapping'),            map: null },
  { key: 'num_invert',  label: t('adapters.bindingForm.valueMapNumInvert'),         map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: t('adapters.bindingForm.valueMapBoolOnOff'),         map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: t('adapters.bindingForm.valueMapOnOffBool'),         map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: t('adapters.bindingForm.valueMapNumOnOff'),          map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: t('adapters.bindingForm.valueMapOnOffNum'),          map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: t('adapters.bindingForm.customValueMapping'),         map: null },
]

const cfg = reactive({
  group_address: '', dpt_id: 'DPT9.001', state_group_address: '', respond_to_read: false,
  address: 0, register_type: 'holding', data_format: 'uint16',
  unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0,
  byte_order: 'big', word_order: 'big',
  topic: '', publish_topic: '', retain: false, payload_template: '',
  source_data_type: '', json_key: '', xml_path: '',
  sensor_id: '', sensor_type: 'DS18B20',
  // HOME_ASSISTANT
  entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '',
  // IOBROKER
  state_id: '', command_state_id: '', ack: false,
  // ANWESENHEITSSIMULATION
  offset_override: null,
  on_presence_override: null,
  on_presence_value: '',
  // SNMP
  host: '192.168.1.1',
  port: 161,
  oid: '',
  data_type: 'auto',
  timeout: 5.0,
  retries: 1,
  // ZEITSCHALTUHR
  timer_type: 'daily', meta_type: 'none',
  weekdays: [0,1,2,3,4,5,6], months: [], day_of_month: 0,
  time_ref: 'absolute', hour: 0, minute: 0, offset_minutes: 0,
  solar_altitude_deg: 0.0, sun_direction: 'rising',
  every_hour: false, every_minute: false,
  holiday_mode: 'ignore', vacation_mode: 'ignore',
  selected_holidays: [],
  date_window_enabled: false,
  date_window_from: '',
  date_window_to: '',
  value: '1',
})

// MQTT source data type constants + compatibility map
const MQTT_SOURCE_TYPES = [
  { value: '',       label: t('adapters.bindingForm.noForcedType') },
  { value: 'string', label: 'string' },
  { value: 'int',    label: 'int' },
  { value: 'float',  label: 'float' },
  { value: 'bool',   label: 'bool' },
  { value: 'json',   label: t('adapters.bindingForm.jsonExtractKey') },
  { value: 'xml',    label: t('adapters.bindingForm.xmlExtractPath') },
]

// DataPoint type → which MQTT source types are ok / warn / bad
const MQTT_TYPE_COMPAT = {
  BOOLEAN:  { ok: ['bool', 'auto'], warn: ['int', 'string'], bad: ['float', 'json', 'xml'] },
  INTEGER:  { ok: ['int', 'auto'],  warn: ['float'],          bad: ['bool', 'string', 'json', 'xml'] },
  FLOAT:    { ok: ['float', 'int', 'auto'], warn: [],          bad: ['bool', 'string', 'json', 'xml'] },
  STRING:   { ok: ['string', 'auto'], warn: ['int', 'float', 'bool'], bad: ['json', 'xml'] },
  DATE:     { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
  TIME:     { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
  DATETIME: { ok: ['string', 'auto'], warn: [],  bad: ['int', 'float', 'bool', 'json', 'xml'] },
}

// JSON sample state (UI-only — not persisted)
const mqttJsonSample     = ref('')
const mqttJsonKeys       = ref([])   // [{ key: 'temperature', type: 'number' }, …]
const mqttJsonParseError = ref(null)

// XML sample state (UI-only — not persisted)
const mqttXmlSample      = ref('')
const mqttXmlElements    = ref([])   // [{ path: 'data/temperature', text: '22.5' }, …]
const mqttXmlParseError  = ref(null)

// Shared loading state for sample fetch
const mqttSampleLoading  = ref(false)

// MQTT topic browser state
const mqttBrowseTopics = ref([])
const mqttBrowseLoading = ref(false)
const mqttBrowseError  = ref(null)

// ioBroker state browser state
const iobrokerStates = ref([])
const iobrokerBrowseLoading = ref(false)
const iobrokerBrowseError = ref(null)
let iobrokerBrowseTimer = null

// SNMP Walk state
const snmpWalkResults = ref([])
const snmpWalkLoading = ref(false)
const snmpWalkError   = ref(null)
const snmpWalkHasMore = ref(false)
const snmpWalkRoot    = ref('1.3.6.1.2.1')

// Zeitschaltuhr holiday list state (for Feiertagsschaltuhr)
const ztHolidays = ref([])   // [{date, name}, …] sorted by date
const ztHolidaysLoading = ref(false)
const ztHolidaysError = ref(null)

// Date window UI state
const WIN_MONTHS = [
  { v: 1, l: t('common.months.january') }, { v: 2, l: t('common.months.february') }, { v: 3, l: t('common.months.march') },
  { v: 4, l: t('common.months.april') }, { v: 5, l: t('common.months.may') }, { v: 6, l: t('common.months.june') },
  { v: 7, l: t('common.months.july') }, { v: 8, l: t('common.months.august') }, { v: 9, l: t('common.months.september') },
  { v: 10, l: t('common.months.october') }, { v: 11, l: t('common.months.november') }, { v: 12, l: t('common.months.december') },
]
const WEEKDAY_SHORTS = [
  t('adapters.bindingForm.ztWeekdayMo'),
  t('adapters.bindingForm.ztWeekdayTu'),
  t('adapters.bindingForm.ztWeekdayWe'),
  t('adapters.bindingForm.ztWeekdayTh'),
  t('adapters.bindingForm.ztWeekdayFr'),
  t('adapters.bindingForm.ztWeekdaySa'),
  t('adapters.bindingForm.ztWeekdaySu'),
]
const MONTH_SHORTS = [
  t('adapters.bindingForm.ztMonthJan'),
  t('adapters.bindingForm.ztMonthFeb'),
  t('adapters.bindingForm.ztMonthMar'),
  t('adapters.bindingForm.ztMonthApr'),
  t('adapters.bindingForm.ztMonthMay'),
  t('adapters.bindingForm.ztMonthJun'),
  t('adapters.bindingForm.ztMonthJul'),
  t('adapters.bindingForm.ztMonthAug'),
  t('adapters.bindingForm.ztMonthSep'),
  t('adapters.bindingForm.ztMonthOct'),
  t('adapters.bindingForm.ztMonthNov'),
  t('adapters.bindingForm.ztMonthDec'),
]
const winFrom = reactive({ type: 'fixed', month: 1,  day: 1,  sign: '+', offset: 0, name: '' })
const winTo   = reactive({ type: 'fixed', month: 12, day: 31, sign: '+', offset: 0, name: '' })

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const selectedAdapterType = computed(() => {
  const explicitType = props.initial?.adapter_type
  if (explicitType) return explicitType
  const selectedId = props.initial?.adapter_instance_id ?? form.adapter_instance_id
  const inst = allInstances.value.find(i => String(i.id) === String(selectedId))
  return inst?.adapter_type ?? null
})

const currentInstanceName = computed(() => {
  if (!props.initial) return ''
  const type = selectedAdapterType.value
  if (props.initial.instance_name && type) return `${props.initial.instance_name} (${type})`
  if (props.initial.instance_name) return props.initial.instance_name
  if (type) return type
  return ''
})

const selectedInstanceId = computed(() => props.initial?.adapter_instance_id || form.adapter_instance_id)

const visibleTabs = computed(() => {
  const tabs = [{ id: 'conn', label: t('logic.nodeConfig.tabs.connection'), badge: false }]
  if (selectedAdapterType.value && selectedAdapterType.value !== 'ZEITSCHALTUHR' && selectedAdapterType.value !== 'ANWESENHEITSSIMULATION') {
    if (selectedAdapterType.value === 'IOBROKER' && !showAdvancedTabs.value) return tabs
    const hasFormula = !!form.value_formula?.trim() || !!form.value_map_preset
    tabs.push({ id: 'transform', label: t('logic.nodeConfig.tabs.transform'), badge: hasFormula })
    const canUseFilter = selectedAdapterType.value === 'IOBROKER'
      || form.direction === 'DEST' || form.direction === 'BOTH'
    if (canUseFilter) {
      const hasFilter = form.throttle_value > 0 || form.send_on_change
        || (form.send_min_delta ?? 0) > 0 || (form.send_min_delta_pct ?? 0) > 0
      tabs.push({ id: 'filter', label: t('logic.nodeConfig.tabs.filter'), badge: hasFilter })
    }
  }
  return tabs
})

watch(visibleTabs, tabs => {
  if (!tabs.find(t => t.id === activeTab.value)) activeTab.value = 'conn'
})

const groupedDpts = computed(() => {
  const familyLabels = {
    DPT1: 'DPT 1.x — 1-Bit (Boolean)', DPT5: 'DPT 5.x — 8-Bit unsigned',
    DPT6: 'DPT 6.x — 8-Bit signed',    DPT7: 'DPT 7.x — 16-Bit unsigned',
    DPT8: 'DPT 8.x — 16-Bit signed',   DPT9: 'DPT 9.x — 16-Bit Float',
    DPT10: 'DPT 10.x — Time of Day',   DPT11: 'DPT 11.x — Date',
    DPT12: 'DPT 12.x — 32-Bit unsigned', DPT13: 'DPT 13.x — 32-Bit signed',
    DPT14: 'DPT 14.x — 32-Bit IEEE Float', DPT16: 'DPT 16.x — 14-Byte String',
    DPT18: 'DPT 18.x — Scene Control', DPT19: 'DPT 19.x — Date and Time',
    DPT219: 'DPT 219.x — AlarmInfo',
  }
  const families = {}
  for (const dpt of allDpts.value) {
    const family = dpt.dpt_id.replace(/\.\d+$/, '')
    if (!families[family]) families[family] = []
    families[family].push(dpt)
  }
  return Object.entries(families).map(([family, dpts]) => ({
    family, label: familyLabels[family] ?? family, dpts,
  }))
})

const groupedInstances = computed(() => {
  const groups = {}
  for (const inst of allInstances.value) {
    if (!groups[inst.adapter_type]) groups[inst.adapter_type] = []
    groups[inst.adapter_type].push(inst)
  }
  return Object.entries(groups).map(([type, items]) => ({ type, items }))
})

// Compatibility badge for MQTT source_data_type vs DataPoint data_type
const mqttTypeCompat = computed(() => {
  const sdt = cfg.source_data_type ?? 'auto'
  if (!sdt || sdt === 'json' || sdt === 'xml') return null  // no badge — depends on extracted value
  const dpType = (props.dpDataType ?? 'UNKNOWN').toUpperCase()
  const compat = MQTT_TYPE_COMPAT[dpType]
  if (!compat) return null                             // UNKNOWN → no badge
  if (compat.ok.includes(sdt))
    return { cls: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400', label: t('adapters.bindingForm.compatible') }
  if (compat.warn.includes(sdt))
    return { cls: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400', label: t('adapters.bindingForm.conversionRequired') }
  return { cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400', label: t('adapters.bindingForm.incompatible') }
})

// ---------------------------------------------------------------------------
// Init beim Bearbeiten
// ---------------------------------------------------------------------------

watch(() => props.initial, val => {
  if (!val) return
  form.adapter_instance_id = val.adapter_instance_id ?? ''
  form.direction           = val.direction
  form.enabled             = val.enabled
  Object.assign(cfg, val.config ?? {})
  if (cfg.state_group_address == null) cfg.state_group_address = ''
  if (cfg.publish_topic       == null) cfg.publish_topic = ''
  if (cfg.respond_to_read     == null) cfg.respond_to_read = false
  if (cfg.payload_template    == null) cfg.payload_template = ''
  if (cfg.source_data_type   == null) cfg.source_data_type = ''
  if (cfg.json_key           == null) cfg.json_key = ''
  if (cfg.xml_path           == null) cfg.xml_path = ''
  // HOME_ASSISTANT defaults when loading
  if (cfg.entity_id        == null) cfg.entity_id        = ''
  if (cfg.attribute        == null) cfg.attribute        = ''
  if (cfg.service_domain   == null) cfg.service_domain   = ''
  if (cfg.service_name     == null) cfg.service_name     = ''
  if (cfg.service_data_key == null) cfg.service_data_key = ''
  // IOBROKER defaults when loading
  if (cfg.state_id         == null) cfg.state_id = ''
  if (cfg.command_state_id == null) cfg.command_state_id = ''
  if (cfg.ack              == null) cfg.ack = false
  // ZEITSCHALTUHR defaults when loading
  if (cfg.timer_type    == null) cfg.timer_type    = 'daily'
  if (cfg.meta_type     == null) cfg.meta_type     = 'none'
  if (cfg.weekdays      == null) cfg.weekdays      = [0,1,2,3,4,5,6]
  if (cfg.months        == null) cfg.months        = []
  if (cfg.day_of_month  == null) cfg.day_of_month  = 0
  if (cfg.time_ref      == null) cfg.time_ref      = 'absolute'
  if (cfg.hour          == null) cfg.hour          = 0
  if (cfg.minute        == null) cfg.minute        = 0
  if (cfg.offset_minutes == null) cfg.offset_minutes = 0
  if (cfg.solar_altitude_deg == null) cfg.solar_altitude_deg = 0.0
  if (cfg.sun_direction == null) cfg.sun_direction = 'rising'
  if (cfg.every_hour    == null) cfg.every_hour    = false
  if (cfg.every_minute  == null) cfg.every_minute  = false
  if (cfg.holiday_mode  == null) cfg.holiday_mode  = 'ignore'
  if (cfg.vacation_mode     == null) cfg.vacation_mode     = 'ignore'
  if (cfg.selected_holidays    == null) cfg.selected_holidays    = []
  if (cfg.date_window_enabled == null) cfg.date_window_enabled = false
  if (cfg.date_window_from    == null) cfg.date_window_from    = ''
  if (cfg.date_window_to      == null) cfg.date_window_to      = ''
  if (cfg.date_window_from) parseWinExprInto(cfg.date_window_from, winFrom)
  if (cfg.date_window_to)   parseWinExprInto(cfg.date_window_to,   winTo)
  if (cfg.value             == null) cfg.value             = '1'
  // ANWESENHEITSSIMULATION defaults + select sync
  if (cfg.offset_override      === undefined) cfg.offset_override      = null
  if (cfg.on_presence_override === undefined) cfg.on_presence_override = null
  if (cfg.on_presence_value    === undefined) cfg.on_presence_value    = ''
  // SNMP defaults when loading
  if (cfg.host     == null) cfg.host     = '192.168.1.1'
  if (cfg.port     == null) cfg.port     = 161
  if (cfg.oid      == null) cfg.oid      = ''
  if (cfg.data_type == null) cfg.data_type = 'auto'
  if (cfg.timeout  == null) cfg.timeout  = 5.0
  if (cfg.retries  == null) cfg.retries  = 1
  {
    const ANW_PRESETS = ['1', '7', '14']
    if (cfg.offset_override != null) {
      anwOffsetSelect.value = ANW_PRESETS.includes(String(cfg.offset_override)) ? String(cfg.offset_override) : 'custom'
    } else {
      anwOffsetSelect.value = ''
    }
  }
  // Restore value_map UI state from top-level binding field
  if (val.value_map && typeof val.value_map === 'object') {
    const mapStr = JSON.stringify(val.value_map)
    const preset = VALUE_MAP_PRESETS.find(p => p.map && JSON.stringify(p.map) === mapStr)
    form.value_map_preset = preset?.key ?? 'custom'
    form.value_map_custom = preset ? '' : JSON.stringify(val.value_map, null, 2)
  } else {
    form.value_map_preset = ''
    form.value_map_custom = ''
  }
  const ms = val.send_throttle_ms ?? 0
  if      (ms === 0)               { form.throttle_value = 0;            form.throttle_unit = 's'   }
  else if (ms % 3_600_000 === 0)   { form.throttle_value = ms/3_600_000; form.throttle_unit = 'h'   }
  else if (ms % 60_000 === 0)      { form.throttle_value = ms/60_000;    form.throttle_unit = 'min' }
  else if (ms % 1000 === 0)        { form.throttle_value = ms/1000;      form.throttle_unit = 's'   }
  else                             { form.throttle_value = ms;            form.throttle_unit = 'ms'  }
  form.send_on_change     = val.send_on_change     ?? false
  form.send_min_delta     = val.send_min_delta     ?? null
  form.send_min_delta_pct = val.send_min_delta_pct ?? null
  const f = val.value_formula ?? ''
  form.value_formula  = f
  form.formula_preset = f ? '__custom__' : ''
}, { immediate: true })

onMounted(async () => {
  try {
    const [instRes, dptRes] = await Promise.all([adapterApi.listInstances(), adapterApi.knxDpts()])
    allInstances.value = instRes.data
    allDpts.value      = dptRes.data
  } catch {}
  // If editing an existing holiday-type binding, load holidays immediately
  if (cfg.timer_type === 'holiday' && selectedInstanceId.value) {
    await loadZsuHolidays()
  }
})

async function loadZsuHolidays() {
  const instanceId = selectedInstanceId.value
  if (!instanceId) return
  ztHolidaysLoading.value = true
  ztHolidaysError.value = null
  try {
    const { data } = await adapterApi.getZsuHolidays(instanceId)
    ztHolidays.value = data
  } catch (e) {
    ztHolidaysError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.holidaysLoadFailed')
  } finally {
    ztHolidaysLoading.value = false
  }
}

watch(() => [selectedAdapterType.value, cfg.timer_type, selectedInstanceId.value], ([type, timerType]) => {
  if (type === 'ZEITSCHALTUHR' && timerType === 'holiday' && selectedInstanceId.value) {
    loadZsuHolidays()
  }
})

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

async function mqttBrowse() {
  mqttBrowseLoading.value = true
  mqttBrowseError.value   = null
  mqttBrowseTopics.value  = []
  try {
    const res = await adapterApi.mqttBrowseTopics(form.adapter_instance_id)
    mqttBrowseTopics.value = res.data
    if (res.data.length === 0) mqttBrowseError.value = t('adapters.bindingForm.errors.noTopicsReceived')
  } catch (e) {
    mqttBrowseError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.topicsFetchFailed')
  } finally {
    mqttBrowseLoading.value = false
  }
}

function selectMqttTopic(topic) {
  cfg.topic = topic
  mqttBrowseTopics.value = []
  mqttBrowseError.value  = null
}

function onIoBrokerStateInput() {
  iobrokerBrowseError.value = null
  clearTimeout(iobrokerBrowseTimer)
  const q = cfg.state_id?.trim() ?? ''
  if (q.length < 2) {
    iobrokerStates.value = []
    return
  }
  iobrokerBrowseTimer = setTimeout(() => browseIoBrokerStates(), 350)
}

async function browseIoBrokerStates() {
  const instanceId = selectedInstanceId.value
  if (!instanceId) {
    iobrokerBrowseError.value = t('adapters.bindingForm.errors.selectIoBrokerInstanceFirst')
    return
  }
  iobrokerBrowseLoading.value = true
  iobrokerBrowseError.value = null
  try {
    const { data } = await adapterApi.iobrokerBrowseStates(instanceId, cfg.state_id?.trim() ?? '', 50)
    iobrokerStates.value = data
    if (data.length === 0) iobrokerBrowseError.value = t('adapters.bindingForm.errors.noStatesFound')
  } catch (e) {
    iobrokerBrowseError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.ioBrokerStatesLoadFailed')
  } finally {
    iobrokerBrowseLoading.value = false
  }
}

function selectIoBrokerState(state) {
  cfg.state_id = state.id
  if (state.type && !cfg.source_data_type) {
    const t = String(state.type).toLowerCase()
    if (t === 'boolean') cfg.source_data_type = 'bool'
    else if (t === 'number') cfg.source_data_type = 'float'
    else if (t === 'string') cfg.source_data_type = 'string'
  }
  if (!state.write && form.direction !== 'SOURCE') form.direction = 'SOURCE'
  iobrokerStates.value = []
  iobrokerBrowseError.value = null
}

async function snmpWalk(append = false) {
  const instanceId = selectedInstanceId.value
  if (!instanceId || !cfg.host) return
  snmpWalkLoading.value = true
  if (!append) {
    snmpWalkError.value = null
    snmpWalkResults.value = []
  }
  try {
    const rootOid  = snmpWalkRoot.value?.trim() || '1.3.6.1.2.1'
    const startOid = append && snmpWalkResults.value.length
      ? snmpWalkResults.value[snmpWalkResults.value.length - 1].oid
      : null
    const { data } = await adapterApi.snmpWalk(instanceId, cfg.host, rootOid, cfg.port || 161, 50, 10, startOid)
    if (append) {
      snmpWalkResults.value = [...snmpWalkResults.value, ...data]
    } else {
      snmpWalkResults.value = data
    }
    if (snmpWalkResults.value.length === 0) snmpWalkError.value = t('adapters.bindingForm.errors.noOidsFound')
    snmpWalkHasMore.value = data.length === 50
  } catch (e) {
    snmpWalkError.value = e.response?.data?.detail ?? t('adapters.bindingForm.errors.snmpWalkFailed')
  } finally {
    snmpWalkLoading.value = false
  }
}

function onValueMapPresetChange() {
  if (form.value_map_preset !== 'custom') {
    form.value_map_custom = ''
    form.value_map_custom_error = ''
  }
}

function onValueMapCustomInput() {
  form.value_map_custom_error = ''
  if (!form.value_map_custom.trim()) return
  try {
    JSON.parse(form.value_map_custom)
  } catch (e) {
    form.value_map_custom_error = t('adapters.bindingForm.errors.invalidJson', { msg: e.message })
  }
}

async function loadMqttSample() {
  const instanceId = form.adapter_instance_id || props.initial?.adapter_instance_id
  const topic = cfg.topic?.trim()
  if (!instanceId || !topic) return
  mqttSampleLoading.value = true
  // Clear previous errors so the user sees the loading state
  mqttJsonParseError.value = null
  mqttXmlParseError.value  = null
  try {
    const { data } = await adapterApi.mqttSamplePayload(instanceId, topic)
    if (cfg.source_data_type === 'json') {
      mqttJsonSample.value = data.payload
      onMqttJsonSampleInput()
    } else if (cfg.source_data_type === 'xml') {
      mqttXmlSample.value = data.payload
      onMqttXmlSampleInput()
    }
  } catch (e) {
    const msg = e.response?.data?.detail ?? t('adapters.bindingForm.errors.noPayloadReceived')
    if (cfg.source_data_type === 'json') mqttJsonParseError.value = msg
    if (cfg.source_data_type === 'xml')  mqttXmlParseError.value  = msg
  } finally {
    mqttSampleLoading.value = false
  }
}

// Auto-load payload when switching to JSON/XML mode (if topic already set)
watch(() => cfg.source_data_type, sdt => {
  if (sdt === 'json' || sdt === 'xml') loadMqttSample()
})

// Force direction to SOURCE when ZEITSCHALTUHR is selected
watch(selectedAdapterType, type => {
  if (type === 'ZEITSCHALTUHR') form.direction = 'SOURCE'
  if (type === 'IOBROKER') {
    activeTab.value = 'conn'
    showAdvancedTabs.value = false
  }
  if (type === 'SNMP' && !cfg.poll_interval) cfg.poll_interval = 30.0
})

// Zeitschaltuhr helpers
function ztToggleWeekday(idx) {
  const i = cfg.weekdays.indexOf(idx)
  if (i >= 0) cfg.weekdays.splice(i, 1)
  else cfg.weekdays.push(idx)
}

function ztToggleMonth(m) {
  const i = cfg.months.indexOf(m)
  if (i >= 0) cfg.months.splice(i, 1)
  else cfg.months.push(m)
}

function ztToggleHoliday(name) {
  if (cfg.selected_holidays.length === 0) {
    // All selected (empty = no filter): unchecking one → select all except this one
    cfg.selected_holidays = ztHolidays.value.map(h => h.name).filter(n => n !== name)
  } else {
    const i = cfg.selected_holidays.indexOf(name)
    if (i >= 0) {
      cfg.selected_holidays.splice(i, 1)
      // If all removed → treat as "all selected" (empty list = no filter)
    } else {
      cfg.selected_holidays.push(name)
      // If now all are explicitly selected, collapse to empty (= no filter)
      if (cfg.selected_holidays.length === ztHolidays.value.length) {
        cfg.selected_holidays = []
      }
    }
  }
}

function buildWinExpr(ep) {
  switch (ep.type) {
    case 'fixed': {
      const mm = String(ep.month).padStart(2, '0')
      const dd = String(ep.day).padStart(2, '0')
      return `${mm}-${dd}`
    }
    case 'easter':
      return ep.offset === 0 ? 'easter+0' : `easter${ep.sign}${ep.offset}`
    case 'advent':
      return ep.offset === 0 ? 'advent+0' : `advent${ep.sign}${ep.offset}`
    case 'holiday_name':
      if (!ep.name) return ''
      return ep.offset === 0 ? `holiday:${ep.name}` : `holiday:${ep.name}${ep.sign}${ep.offset}`
    default:
      return ''
  }
}

function parseWinExprInto(expr, ep) {
  if (!expr) return
  const exprUp = expr.toUpperCase()
  const fixedM = expr.match(/^(\d{1,2})-(\d{1,2})$/)
  if (fixedM) {
    ep.type = 'fixed'; ep.month = parseInt(fixedM[1], 10); ep.day = parseInt(fixedM[2], 10)
    return
  }
  const easterM = exprUp.match(/^EASTER([+-])?(\d+)?$/)
  if (easterM) {
    ep.type = 'easter'; ep.sign = easterM[1] ?? '+'; ep.offset = parseInt(easterM[2] ?? '0', 10)
    return
  }
  const adventM = exprUp.match(/^ADVENT([+-])?(\d+)?$/)
  if (adventM) {
    ep.type = 'advent'; ep.sign = adventM[1] ?? '+'; ep.offset = parseInt(adventM[2] ?? '0', 10)
    return
  }
  if (exprUp.startsWith('HOLIDAY:')) {
    const remainder = expr.slice(8)
    const offsetM = remainder.match(/([+-])(\d+)$/)
    ep.type = 'holiday_name'
    if (offsetM) {
      ep.name = remainder.slice(0, offsetM.index).trim()
      ep.sign = offsetM[1]; ep.offset = parseInt(offsetM[2], 10)
    } else {
      ep.name = remainder.trim(); ep.sign = '+'; ep.offset = 0
    }
  }
}

function describeWinEp(ep) {
  switch (ep.type) {
    case 'fixed': {
      const mon = WIN_MONTHS.find(m => m.v === ep.month)?.l ?? String(ep.month)
      return `${ep.day}. ${mon}`
    }
    case 'easter':
      return ep.offset === 0 ? t('adapters.bindingForm.ztEasterSunday') : t('adapters.bindingForm.ztEasterOffset', { sign: ep.sign, n: ep.offset })
    case 'advent': {
      const presets = {
        '0': t('adapters.bindingForm.ztAdvent1'),
        '7': t('adapters.bindingForm.ztAdvent2'),
        '14': t('adapters.bindingForm.ztAdvent3'),
        '21': t('adapters.bindingForm.ztAdvent4'),
        '24': t('adapters.bindingForm.ztChristmasEve'),
      }
      if (ep.sign === '+' && presets[String(ep.offset)]) return presets[String(ep.offset)]
      return ep.offset === 0 ? t('adapters.bindingForm.ztAdvent1') : t('adapters.bindingForm.ztAdventOffset', { sign: ep.sign, n: ep.offset })
    }
    case 'holiday_name':
      if (!ep.name) return t('adapters.bindingForm.notSet')
      return ep.offset === 0 ? ep.name : t('adapters.bindingForm.ztHolidayOffset', { name: ep.name, sign: ep.sign, n: ep.offset })
    default:
      return t('adapters.bindingForm.notSet')
  }
}

async function onWinTypeChange(ep) {
  if (ep.type === 'holiday_name' && selectedInstanceId.value) await loadZsuHolidays()
}

function onAnwOffsetSelectChange() {
  if (anwOffsetSelect.value === '') {
    cfg.offset_override = null
  } else if (anwOffsetSelect.value !== 'custom') {
    cfg.offset_override = parseInt(anwOffsetSelect.value)
  }
}

function onAnwOffsetCustomInput() {
  if (cfg.offset_override != null) {
    cfg.offset_override = Math.min(30, Math.max(1, cfg.offset_override || 1))
  }
}

function collectXmlLeafPaths(el, prefix) {
  const result = []

  // Group children by tag name so we can detect repeated elements
  const byTag = {}
  for (const child of el.children) {
    ;(byTag[child.tagName] ??= []).push(child)
  }

  for (const [tag, siblings] of Object.entries(byTag)) {
    for (let i = 0; i < siblings.length; i++) {
      const child = siblings[i]

      // Build path segment — include attribute predicate when helpful
      let segment = tag
      if (siblings.length > 1 || child.attributes.length > 0) {
        // Prefer a named attribute (e.g. id) over positional index
        const attr = child.attributes[0]
        segment = attr
          ? `${tag}[@${attr.name}='${attr.value}']`
          : `${tag}[${i + 1}]`
      }

      const path = prefix ? `${prefix}/${segment}` : segment

      if (child.children.length === 0) {
        result.push({ path, text: child.textContent.trim() })
      } else {
        result.push(...collectXmlLeafPaths(child, path))
      }
    }
  }
  return result
}

function onMqttXmlSampleInput() {
  mqttXmlParseError.value = null
  mqttXmlElements.value = []
  const s = mqttXmlSample.value.trim()
  if (!s) return
  const parser = new DOMParser()
  const doc = parser.parseFromString(s, 'application/xml')
  const parseErr = doc.querySelector('parsererror')
  if (parseErr) {
    mqttXmlParseError.value = t('adapters.bindingForm.errors.invalidXml', { msg: parseErr.textContent.split('\n')[0].trim() })
    return
  }
  mqttXmlElements.value = collectXmlLeafPaths(doc.documentElement, '')
  if (mqttXmlElements.value.length === 0)
    mqttXmlParseError.value = t('adapters.bindingForm.errors.noChildElementsFound')
}

// Flatten all leaf paths from a JSON object/array to dot-notation (max depth 6)
function _flattenJsonLeaves(obj, prefix = '', depth = 0) {
  if (depth > 6 || obj === null || typeof obj !== 'object') {
    return prefix ? [{ key: prefix, text: obj === null ? 'null' : String(obj) }] : []
  }
  const paths = []
  if (Array.isArray(obj)) {
    obj.forEach((item, i) => {
      const key = `${prefix}[${i}]`
      paths.push(..._flattenJsonLeaves(item, key, depth + 1))
    })
  } else {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k
      if (v !== null && typeof v === 'object') {
        paths.push(..._flattenJsonLeaves(v, key, depth + 1))
      } else {
        paths.push({ key, text: v === null ? 'null' : String(v) })
      }
    }
  }
  return paths
}

function onMqttJsonSampleInput() {
  mqttJsonParseError.value = null
  mqttJsonKeys.value = []
  const s = mqttJsonSample.value.trim()
  if (!s) return
  try {
    const obj = JSON.parse(s)
    if (obj !== null && typeof obj === 'object') {
      mqttJsonKeys.value = _flattenJsonLeaves(obj)
    } else {
      mqttJsonParseError.value = t('adapters.bindingForm.errors.sampleMustBeJsonObjectOrArray')
    }
  } catch (e) {
    mqttJsonParseError.value = t('adapters.bindingForm.errors.invalidJson', { msg: e.message })
  }
}

function onGaSelect(item) {
  if (item.dpt && item.dpt !== cfg.dpt_id) cfg.dpt_id = item.dpt
}

function onPresetSelect(e) {
  const val = e.target.value
  if (!val) {
    form.value_formula  = ''
    form.formula_preset = ''
  } else if (val !== '__custom__') {
    form.value_formula  = val
    form.formula_preset = val
  }
}

function buildConfig() {
  const type = selectedAdapterType.value
  if (type === 'KNX') {
    const c = { group_address: cfg.group_address, dpt_id: cfg.dpt_id || 'DPT9.001' }
    if (cfg.state_group_address?.trim()) c.state_group_address = cfg.state_group_address.trim()
    if (cfg.respond_to_read) c.respond_to_read = true
    return c
  }
  if (type === 'MODBUS_TCP' || type === 'MODBUS_RTU') {
    return {
      unit_id: cfg.unit_id, register_type: cfg.register_type, address: cfg.address,
      count: cfg.count, data_format: cfg.data_format, scale_factor: cfg.scale_factor,
      byte_order: cfg.byte_order, word_order: cfg.word_order, poll_interval: cfg.poll_interval,
    }
  }
  if (type === 'MQTT') {
    const c = { topic: cfg.topic, retain: cfg.retain }
    if (cfg.publish_topic?.trim())    c.publish_topic    = cfg.publish_topic.trim()
    if (cfg.payload_template?.trim()) c.payload_template = cfg.payload_template.trim()
    // source_data_type + json_key
    if (cfg.source_data_type) {
      c.source_data_type = cfg.source_data_type
      if (cfg.source_data_type === 'json' && cfg.json_key?.trim())
        c.json_key = cfg.json_key.trim()
      if (cfg.source_data_type === 'xml' && cfg.xml_path?.trim())
        c.xml_path = cfg.xml_path.trim()
    }
    return c
  }
  if (type === 'ONEWIRE') {
    return { sensor_id: cfg.sensor_id, sensor_type: cfg.sensor_type || 'DS18B20' }
  }
  if (type === 'HOME_ASSISTANT') {
    const c = { entity_id: cfg.entity_id }
    if (cfg.attribute?.trim())        c.attribute        = cfg.attribute.trim()
    if (cfg.service_domain?.trim())   c.service_domain   = cfg.service_domain.trim()
    if (cfg.service_name?.trim())     c.service_name     = cfg.service_name.trim()
    if (cfg.service_data_key?.trim()) c.service_data_key = cfg.service_data_key.trim()
    return c
  }
  if (type === 'IOBROKER') {
    const c = { state_id: cfg.state_id }
    if (cfg.command_state_id?.trim()) c.command_state_id = cfg.command_state_id.trim()
    if (form.direction === 'DEST' || form.direction === 'BOTH') c.ack = !!cfg.ack
    if ((form.direction === 'SOURCE' || form.direction === 'BOTH') && cfg.source_data_type)
      c.source_data_type = cfg.source_data_type
    if (cfg.source_data_type === 'json' && cfg.json_key?.trim())
      c.json_key = cfg.json_key.trim()
    return c
  }
  if (type === 'ZEITSCHALTUHR') {
    const c = {
      timer_type:   cfg.timer_type,
      meta_type:    cfg.meta_type,
      weekdays:     [...cfg.weekdays],
      holiday_mode: cfg.holiday_mode,
      vacation_mode: cfg.vacation_mode,
    }
    if (cfg.timer_type === 'holiday') {
      c.selected_holidays = [...(cfg.selected_holidays ?? [])]
    }
    if (cfg.timer_type === 'annual') {
      c.months        = [...cfg.months]
      c.day_of_month  = cfg.day_of_month ?? 0
    }
    if (cfg.timer_type !== 'meta') {
      c.time_ref       = cfg.time_ref
      c.minute         = cfg.minute ?? 0
      c.every_hour     = cfg.every_hour
      c.every_minute   = cfg.every_minute
      c.value          = cfg.value || '1'
      if (cfg.time_ref === 'absolute') {
        c.hour = cfg.hour ?? 0
      } else {
        c.offset_minutes = cfg.offset_minutes ?? 0
      }
      if (cfg.time_ref === 'solar_altitude') {
        c.solar_altitude_deg = cfg.solar_altitude_deg ?? 0.0
        c.sun_direction      = cfg.sun_direction || 'rising'
      }
      if (cfg.date_window_enabled) {
        c.date_window_enabled = true
        c.date_window_from    = buildWinExpr(winFrom)
        c.date_window_to      = buildWinExpr(winTo)
      }
    }
    return c
  }
  if (type === 'ANWESENHEITSSIMULATION') {
    const c = {}
    if (cfg.offset_override != null) c.offset_override = cfg.offset_override
    if (cfg.on_presence_override != null) {
      c.on_presence_override = cfg.on_presence_override
      if (cfg.on_presence_override === 'setzen' && cfg.on_presence_value?.trim())
        c.on_presence_value = cfg.on_presence_value.trim()
    }
    return c
  }
  if (type === 'SNMP') {
    const c = {
      host: cfg.host || '192.168.1.1',
      oid:  cfg.oid  || '1.3.6.1.2.1.1.1.0',
    }
    if (cfg.port && cfg.port !== 161)            c.port        = cfg.port
    if (cfg.data_type && cfg.data_type !== 'auto') c.data_type = cfg.data_type
    if (cfg.poll_interval)                         c.poll_interval = cfg.poll_interval
    if (cfg.timeout && cfg.timeout !== 5.0)        c.timeout    = cfg.timeout
    if (cfg.retries !== undefined && cfg.retries !== 1) c.retries = cfg.retries
    return c
  }
  return {}
}

async function submit() {
  error.value  = null
  saving.value = true
  try {
    const config     = buildConfig()
    const effectiveDirection = selectedAdapterType.value === 'ANWESENHEITSSIMULATION' ? 'SOURCE' : form.direction
    const throttleMs = form.throttle_value > 0
      ? Math.round(form.throttle_value * THROTTLE_FACTORS[form.throttle_unit]) : null
    let resolvedValueMap = null
    if (form.value_map_preset === 'custom') {
      try { resolvedValueMap = JSON.parse(form.value_map_custom) } catch { /* invalid JSON — ignore */ }
    } else if (form.value_map_preset) {
      resolvedValueMap = VALUE_MAP_PRESETS.find(p => p.key === form.value_map_preset)?.map ?? null
    }
    const filterPayload = {
      value_formula:      form.value_formula?.trim() || null,
      value_map:          resolvedValueMap,
      send_throttle_ms:   throttleMs,
      send_on_change:     form.send_on_change,
      send_min_delta:     (form.send_min_delta ?? 0) > 0     ? form.send_min_delta     : null,
      send_min_delta_pct: (form.send_min_delta_pct ?? 0) > 0 ? form.send_min_delta_pct : null,
    }
    if (props.initial) {
      await dpApi.updateBinding(props.dpId, props.initial.id, {
        direction: effectiveDirection, config, enabled: form.enabled, ...filterPayload,
      })
    } else {
      if (!form.adapter_instance_id) {
        error.value = t('adapters.bindingForm.errors.selectAdapterInstance'); saving.value = false; return
      }
      await dpApi.createBinding(props.dpId, {
        adapter_instance_id: form.adapter_instance_id,
        direction: effectiveDirection, config, enabled: form.enabled, ...filterPayload,
      })
    }
    emit('save')
  } catch (e) {
    error.value = e.response?.data?.detail ?? t('common.saveError')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
@reference "tailwindcss";
.tab-btn {
  @apply flex items-center px-4 py-2 text-sm text-slate-500 dark:text-slate-400 border-b-2 border-transparent
         hover:text-slate-700 dark:hover:text-slate-200 hover:border-slate-400 dark:hover:border-slate-500 transition-colors cursor-pointer;
}
.tab-active {
  @apply text-blue-500 dark:text-blue-400 border-blue-500 dark:border-blue-400 font-medium;
}
.section-header {
  @apply text-xs font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400 border-b border-slate-200 dark:border-slate-700 pb-1;
}
.optional-divider {
  @apply text-xs text-slate-500 border-b border-slate-200/80 dark:border-slate-700/50 pb-1 mt-1;
}
.optional { @apply text-slate-500 font-normal text-xs ml-1; }
.hint     { @apply text-xs text-slate-500 mt-0.5; }
</style>
