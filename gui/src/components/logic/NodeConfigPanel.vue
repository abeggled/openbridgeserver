<template>
  <div v-if="node" class="h-full flex flex-col bg-surface-800 border-l border-slate-200 dark:border-slate-700/60 w-72">

    <!-- Header -->
    <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-200">{{ $te('logic.nodeTypes.' + node?.type) ? $t('logic.nodeTypes.' + node?.type) : (nodeDef?.label ?? node?.type) }}</h3>
      <button @click="$emit('close')" class="btn-icon text-slate-500">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
        </svg>
      </button>
    </div>

    <!-- ── DataPoint nodes: tab UI ────────────────────────────────────── -->
    <template v-if="isDatapointNode">

      <!-- Tab bar -->
      <div class="flex border-b border-slate-200 dark:border-slate-700/60">
        <button v-for="tab in tabs" :key="tab.id"
          @click="activeTab = tab.id"
          :class="['tab-btn', activeTab === tab.id && 'tab-btn--active']">
          {{ tab.label }}
          <span v-if="tab.dot" class="tab-dot">•</span>
        </button>
      </div>

      <div class="flex-1 overflow-y-auto">

        <!-- Verbindung -->
        <div v-show="activeTab === 'connection'" class="p-4 flex flex-col h-full">
          <p class="text-xs text-slate-500 mb-3 shrink-0">{{ nodeDef?.description }}</p>
          <div class="flex flex-col flex-1 min-h-0 gap-1">
            <label class="label shrink-0">{{ $t('logic.ports.object') }}</label>
            <input v-model="dpSearch" type="text" class="input text-sm shrink-0" :placeholder="$t('logic.nodeConfig.connection.searchPlaceholder')" @input="searchDps" />
            <div v-if="dpResults.length"
              class="mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden flex-1 min-h-0 overflow-y-auto">
              <button v-for="dp in dpResults" :key="dp.id"
                @click="selectDp(dp)"
                class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200">
                {{ dp.name }}
                <span class="text-slate-500 ml-1">{{ dp.data_type }}</span>
              </button>
            </div>
            <div v-if="localData.datapoint_name" class="mt-1 text-xs text-teal-400 shrink-0">
              ✓ {{ localData.datapoint_name }}
            </div>
          </div>
        </div>

        <!-- Transformation -->
        <div v-show="activeTab === 'transform'" class="p-4 flex flex-col gap-3">
          <div class="section-label">{{ $t('logic.nodeConfig.transform.sectionLabel') }}</div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.transform.formulaLabel') }} <span class="text-slate-500 font-normal">{{ $t('logic.nodeConfig.transform.formulaVar', { var: 'x' }) }} <code class="text-teal-400">x</code></span></label>
            <div class="flex gap-2">
              <select v-model="formulaPreset" @change="onPresetChange" class="input text-xs flex-1 min-w-0">
                <option value="">{{ $t('logic.nodeConfig.transform.presetPlaceholder') }}</option>
                <optgroup :label="$t('logic.nodeConfig.transform.multiplyGroup')">
                  <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup :label="$t('logic.nodeConfig.transform.divideGroup')">
                  <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup :label="$t('logic.nodeConfig.transform.customGroup')">
                  <option value="__custom__">{{ $t('logic.nodeConfig.transform.customFormula') }}</option>
                </optgroup>
              </select>
              <input
                v-model="localData.value_formula"
                @input="onFormulaInput"
                @change="emitUpdate"
                class="input text-xs font-mono w-28 shrink-0"
                placeholder="x * 100" />
            </div>
            <p class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.transform.formulaHint') }}</p>
          </div>

          <div class="section-label mt-1">{{ $t('logic.nodeConfig.transform.mapSection') }}</div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.transform.mapSection') }} <span class="text-slate-500 font-normal text-xs">{{ $t('logic.nodeConfig.common.optional') }}</span></label>
            <select v-model="valueMapPreset" @change="onValueMapPresetChange" class="input text-xs"
              data-testid="value-map-preset">
              <option v-for="p in VALUE_MAP_PRESETS" :key="p.key" :value="p.key">{{ p.label }}</option>
            </select>
            <div v-if="valueMapPreset === 'custom'" class="mt-1">
              <textarea
                v-model="valueMapCustom"
                @input="onValueMapCustomInput"
                @change="onValueMapCustomChange"
                class="input text-xs font-mono h-24 resize-y"
                placeholder='{"0": "Aus", "1": "Init", "2": "Aktiv", "3": "Fehler"}'
                data-testid="value-map-custom"
              />
              <p v-if="valueMapCustomError" class="text-xs text-red-400 mt-0.5">{{ valueMapCustomError }}</p>
              <p class="text-xs text-slate-500 mt-0.5">{{ $t('logic.nodeConfig.transform.mapJsonHint') }}</p>
            </div>
            <p class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.transform.mapAfterFormula') }}</p>
          </div>
        </div>

        <!-- Filter -->
        <div v-show="activeTab === 'filter'" class="p-4 flex flex-col gap-4">

          <div>
            <div class="section-label">{{ $t('logic.nodeConfig.filter.timeSection') }}</div>
            <label class="label mt-2">{{ $t('logic.nodeConfig.filter.minInterval', { action: $t(isWrite ? 'logic.nodeConfig.filter.writes' : 'logic.nodeConfig.filter.triggers') }) }}</label>
            <div class="flex gap-2 mt-1">
              <input
                v-model="localData.throttle_value"
                @change="emitUpdate"
                type="number" min="0"
                class="input text-sm flex-1"
                :placeholder="$t('logic.nodeConfig.filter.throttlePlaceholder')" />
              <select v-model="localData.throttle_unit" @change="emitUpdate" class="input text-sm w-20 shrink-0">
                <option value="ms">ms</option>
                <option value="s">s</option>
                <option value="min">min</option>
                <option value="h">h</option>
              </select>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              {{ $t('logic.nodeConfig.filter.throttleHint', { action: $t(isWrite ? 'logic.nodeConfig.filter.writes' : 'logic.nodeConfig.filter.triggers') }) }}
            </p>
          </div>

          <div>
            <div class="section-label">{{ $t('logic.nodeConfig.filter.valueSection') }}</div>

            <label class="flex items-start gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                :checked="boolVal(isWrite ? 'only_on_change' : 'trigger_on_change')"
                @change="e => { setBool(isWrite ? 'only_on_change' : 'trigger_on_change', e.target.checked); emitUpdate() }"
                class="mt-0.5 accent-teal-500" />
              <span class="text-xs text-slate-600 dark:text-slate-300 leading-snug">
                {{ isWrite
                  ? $t('logic.nodeConfig.filter.onlyOnChangeWrite')
                  : $t('logic.nodeConfig.filter.onlyOnChangeTrigger') }}
              </span>
            </label>

            <label class="label mt-3">{{ $t('logic.nodeConfig.filter.minDelta', { action: $t(isWrite ? 'logic.nodeConfig.filter.write' : 'logic.nodeConfig.filter.trigger') }) }}</label>
            <div class="flex gap-2 mt-1">
              <div class="flex-1">
                <input
                  v-model="localData.min_delta"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  :placeholder="$t('logic.nodeConfig.filter.deltaPlaceholder')" />
                <p class="text-xs text-slate-600 mt-0.5">{{ $t('logic.nodeConfig.filter.absolute') }}</p>
              </div>
              <div v-if="!isWrite" class="flex-1">
                <input
                  v-model="localData.min_delta_pct"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  :placeholder="$t('logic.nodeConfig.filter.relativePlaceholder')" />
                <p class="text-xs text-slate-600 mt-0.5">{{ $t('logic.nodeConfig.filter.relative') }}</p>
              </div>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              {{ $t('logic.nodeConfig.filter.deltaHint') }}
            </p>
          </div>
        </div>

      </div>
    </template>

    <!-- ── Trigger node: cron builder ──────────────────────────────────── -->
    <template v-else-if="isCronNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Presets -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.cron.presetsLabel') }}</label>
          <select :value="cronPresetValue" @change="onCronPresetChange" class="input text-sm">
            <option value="">{{ $t('logic.nodeConfig.cron.presetPlaceholder') }}</option>
            <optgroup :label="$t('logic.nodeConfig.cron.intervalGroup')">
              <option v-for="p in CRON_PRESETS_INTERVAL" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup :label="$t('logic.nodeConfig.cron.hourlyGroup')">
              <option v-for="p in CRON_PRESETS_HOURLY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup :label="$t('logic.nodeConfig.cron.dailyGroup')">
              <option v-for="p in CRON_PRESETS_DAILY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup :label="$t('logic.nodeConfig.cron.otherGroup')">
              <option v-for="p in CRON_PRESETS_OTHER" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
          </select>
          <p v-if="cronDescription" class="text-xs text-amber-400 mt-1">▶ {{ cronDescription }}</p>
        </div>

        <!-- Visual field builder -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.cron.customizeLabel') }}</label>
          <div class="cron-grid mt-1">
            <div v-for="f in CRON_FIELD_LABELS" :key="f.key" class="cron-field">
              <input
                v-model="cronFields.find(cf => cf.key === f.key).value"
                @input="onCronFieldChange"
                class="input text-sm text-center font-mono px-1"
                :placeholder="f.placeholder"
                :title="f.label + ' (' + f.hint + ')'"
              />
              <span class="cron-field-label">{{ f.label }}</span>
              <span class="cron-field-hint">{{ f.hint }}</span>
            </div>
          </div>
          <div class="cron-legend mt-2">
            <span><code>*</code> {{ $t('logic.nodeConfig.cron.legendEvery') }}</span>
            <span><code>*/5</code> {{ $t('logic.nodeConfig.cron.legendEveryN') }}</span>
            <span><code>1-5</code> {{ $t('logic.nodeConfig.cron.legendRange') }}</span>
            <span><code>1,3</code> {{ $t('logic.nodeConfig.cron.legendList') }}</span>
          </div>
        </div>

        <!-- Raw expression -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.cron.exprLabel') }}</label>
          <input
            v-model="localData.cron"
            @change="onCronExprChange"
            class="input text-sm font-mono"
            placeholder="0 7 * * *"
          />
          <p class="text-xs text-slate-500 mt-1">
            {{ $t('logic.nodeConfig.cron.exprHint') }}
            — <a href="https://crontab.guru" target="_blank" rel="noopener"
               class="text-amber-400 hover:underline">crontab.guru ↗</a>
          </p>
        </div>
      </div>
    </template>

    <!-- ── math_formula: Formel + Ausgangs-Transformation ──────────────── -->
    <template v-else-if="isMathFormulaNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Hauptformel -->
        <div class="form-group">
          <div class="section-label">{{ $t('logic.nodeConfig.mathFormula.mainSection') }}</div>
          <label class="label">{{ $t('logic.nodeConfig.transform.formulaLabel') }} <span class="text-slate-500 font-normal">{{ $t('logic.nodeConfig.mathFormula.varsHint') }} <code class="text-teal-400">a</code>, <code class="text-teal-400">b</code></span></label>
          <input
            v-model="localData.formula"
            @change="emitUpdate"
            class="input text-sm font-mono"
            placeholder="a + b" />
          <p class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.transform.formulaHint') }}</p>
        </div>

        <!-- Ausgangs-Transformation -->
        <div class="form-group">
          <div class="section-label">{{ $t('logic.nodeConfig.mathFormula.outputSection') }}</div>
          <label class="label">{{ $t('logic.nodeConfig.transform.formulaLabel') }} <span class="text-slate-500 font-normal">{{ $t('logic.nodeConfig.mathFormula.outputVarHint') }} <code class="text-teal-400">x</code></span></label>
          <div class="flex gap-2">
            <select :value="outputFormulaPreset" @change="onOutputPresetChange" class="input text-xs flex-1 min-w-0">
              <option value="">{{ $t('logic.nodeConfig.transform.presetPlaceholder') }}</option>
              <optgroup :label="$t('logic.nodeConfig.transform.multiplyGroup')">
                <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup :label="$t('logic.nodeConfig.transform.divideGroup')">
                <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup :label="$t('logic.nodeConfig.transform.customGroup')">
                <option value="__custom__">{{ $t('logic.nodeConfig.transform.customFormula') }}</option>
              </optgroup>
            </select>
            <input
              v-model="localData.output_formula"
              @change="emitUpdate"
              class="input text-xs font-mono w-28 shrink-0"
              placeholder="x * 100" />
          </div>
          <p class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.mathFormula.noTransform') }}</p>
        </div>
      </div>
    </template>

    <!-- ── api_client: special rendering with conditional auth fields ──── -->
    <template v-else-if="isApiClientNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Standard request fields -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.urlLabel') }}</label>
          <input v-model="localData.url" type="text" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-url" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.methodLabel') }}</label>
          <select v-model="localData.method" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-method">
            <option v-for="m in ['GET','POST','PUT','PATCH','DELETE']" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.requestContentType') }}</label>
          <select v-model="localData.content_type" class="input text-sm" @change="emitUpdate">
            <option v-for="ct in ['application/json','text/plain','application/x-www-form-urlencoded']" :key="ct" :value="ct">{{ ct }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.responseContentType') }}</label>
          <select v-model="localData.response_type" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-response-type">
            <option value="application/json">application/json</option>
            <option value="text/plain">text/plain</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.headersLabel') }}</label>
          <input v-model="localData.headers" type="text" class="input text-sm font-mono" @change="emitUpdate"
            placeholder='{"X-Api-Key": "abc"}' />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.timeoutLabel') }}</label>
          <input v-model="localData.timeout_s" type="number" class="input text-sm" @change="emitUpdate" />
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="localData.verify_ssl" @change="emitUpdate" class="accent-teal-500" />
          <span class="text-xs text-slate-600 dark:text-slate-300">{{ $t('logic.nodeConfig.apiClient.verifySsl') }}</span>
        </label>

        <!-- Auth section -->
        <div class="section-label mt-1">{{ $t('logic.nodeConfig.apiClient.authSection') }}</div>
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.apiClient.authType') }}</label>
          <select v-model="localData.auth_type" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-auth-type">
            <option value="none">{{ $t('logic.nodeConfig.apiClient.authNone') }}</option>
            <option value="basic">{{ $t('logic.nodeConfig.apiClient.authBasic') }}</option>
            <option value="digest">{{ $t('logic.nodeConfig.apiClient.authDigest') }}</option>
            <option value="bearer">{{ $t('logic.nodeConfig.apiClient.authBearer') }}</option>
          </select>
        </div>
        <template v-if="localData.auth_type === 'basic' || localData.auth_type === 'digest'">
          <div class="form-group" data-testid="api-client-auth-basic">
            <label class="label">{{ $t('logic.nodeConfig.apiClient.username') }}</label>
            <input v-model="localData.auth_username" type="text" class="input text-sm"
              autocomplete="off" @change="emitUpdate" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.apiClient.password') }}</label>
            <input v-model="localData.auth_password" type="password" class="input text-sm"
              autocomplete="new-password" @change="emitUpdate" />
          </div>
        </template>
        <template v-if="localData.auth_type === 'bearer'">
          <div class="form-group" data-testid="api-client-auth-bearer">
            <label class="label">{{ $t('logic.nodeConfig.apiClient.authBearer') }}</label>
            <input v-model="localData.auth_token" type="password" class="input text-sm"
              autocomplete="new-password" @change="emitUpdate" />
          </div>
        </template>
      </div>
    </template>

    <!-- ── string_concat ────────────────────────────────────────────────── -->
    <template v-else-if="isStringConcatNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Count + Separator -->
        <div class="flex gap-3">
          <div class="form-group flex-1">
            <label class="label">{{ $t('logic.nodeConfig.stringConcat.inputCount') }}</label>
            <input
              type="number" min="2" max="20"
              :value="concatCount"
              @change="onConcatCountChange"
              class="input text-sm"
              data-testid="concat-count"
            />
          </div>
          <div class="form-group flex-1">
            <label class="label">{{ $t('logic.nodeConfig.stringConcat.separator') }}</label>
            <input
              v-model="localData.separator"
              @change="emitUpdate"
              class="input text-sm font-mono"
              :placeholder="$t('logic.nodeConfig.stringConcat.separatorPlaceholder')"
              data-testid="concat-separator"
            />
          </div>
        </div>

        <!-- Per-slot static text -->
        <div class="section-label">{{ $t('logic.nodeConfig.stringConcat.staticSection') }}</div>
        <p class="text-xs text-slate-500 -mt-2">{{ $t('logic.nodeConfig.stringConcat.staticHint') }}</p>
        <div class="flex flex-col gap-2">
          <div v-for="i in concatSlots" :key="i" class="flex items-center gap-2">
            <span class="text-xs text-slate-400 w-5 text-right shrink-0">{{ i }}</span>
            <input
              :value="localData[`text_${i}`] ?? ''"
              @input="localData[`text_${i}`] = $event.target.value"
              @change="emitUpdate"
              class="input text-sm flex-1"
              :placeholder="$t('logic.nodeConfig.stringConcat.inputPlaceholder', { n: i })"
              :data-testid="`concat-text-${i}`"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- ── json_extractor / xml_extractor ───────────────────────────────── -->
    <template v-else-if="isExtractorNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Preview: last received raw data -->
        <div class="form-group">
          <div class="section-label">{{ $t('logic.nodeConfig.extractor.receivedData') }}</div>
          <textarea
            :value="extractorPreview"
            readonly
            rows="7"
            class="input text-xs font-mono resize-y"
            style="background:#0f172a;color:#94a3b8"
            :placeholder="$t('logic.nodeConfig.extractor.noDataPlaceholder')"
            data-testid="extractor-preview"
          />
        </div>

        <!-- ── JSON Extractor: multi-output UI ──────────────────────────── -->
        <template v-if="node.type === 'json_extractor'">

          <!-- Legacy single-path: show upgrade banner -->
          <template v-if="localData.json_path && !jsonPaths.length">
            <div class="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-300">
              <p class="font-semibold mb-1">Legacy-Konfiguration</p>
              <p class="text-amber-400/80 mb-2">Pfad: <code class="font-mono">{{ localData.json_path }}</code></p>
              <button @click="migrateJsonToMultiPath" class="btn btn-sm bg-amber-600 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded">
                Zu mehreren Ausgängen upgraden
              </button>
            </div>
          </template>

          <!-- Multi-path path picker dropdown (one shared, fills active row) -->
          <div v-if="extractorPaths.length" class="form-group">
            <label class="label">
              {{ $t('logic.nodeConfig.extractor.selectPath') }}<span v-if="activeExtractorRow !== null" class="text-teal-400"> → Ausgang {{ activeExtractorRow + 1 }}</span>
            </label>
            <select @change="onExtractorPathSelect" class="input text-sm" data-testid="extractor-path-select">
              <option value="">{{ $t('logic.nodeConfig.extractor.pathPlaceholder') }}</option>
              <option v-for="p in extractorPaths" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>

          <!-- Output rows -->
          <div class="form-group">
            <div class="flex items-center justify-between mb-1">
              <span class="section-label">Ausgänge ({{ jsonPaths.length }})</span>
              <button
                @click="addJsonPath"
                class="w-6 h-6 flex items-center justify-center rounded text-teal-400 hover:bg-teal-400/10 font-bold text-lg leading-none"
                title="Ausgang hinzufügen"
              >+</button>
            </div>

            <div
              v-for="(entry, i) in jsonPaths" :key="i"
              class="extractor-output-row mt-2 p-2 rounded-lg border border-slate-700/50 flex flex-col gap-1"
              :style="extractorOutputRowStyle"
            >
              <div class="flex items-center gap-1">
                <span class="extractor-output-index text-xs font-mono w-5 shrink-0 text-center">{{ i + 1 }}</span>
                <input
                  :value="entry.label"
                  @input="updateJsonPath(i, 'label', $event.target.value)"
                  class="input text-xs flex-1"
                  placeholder="Bezeichnung"
                />
                <button
                  @click="removeJsonPath(i)"
                  class="extractor-output-remove w-5 h-5 flex items-center justify-center rounded hover:bg-red-400/10 text-base leading-none shrink-0"
                  title="Ausgang entfernen"
                >−</button>
              </div>
              <input
                :value="entry.path"
                @input="updateJsonPath(i, 'path', $event.target.value)"
                @focus="activeExtractorRow = i"
                @blur="activeExtractorRow = null"
                class="input text-xs font-mono w-full"
                :class="activeExtractorRow === i ? 'ring-1 ring-teal-500/60' : ''"
                placeholder="z.B. data.temperature"
                data-testid="extractor-path-input"
              />
              <p v-if="jsonPathPreview(i) !== null" class="text-xs text-teal-400">
                ↳ {{ String(jsonPathPreview(i)) }}
              </p>
            </div>

            <p v-if="!jsonPaths.length && !localData.json_path" class="text-xs text-slate-500 mt-2 text-center py-2">
              Klicke <strong>+</strong> um Ausgänge hinzuzufügen.
            </p>
          </div>
        </template>

        <!-- ── XML Extractor: multi-output UI ───────────────────────────── -->
        <template v-else>

          <!-- Legacy single-path: show upgrade banner -->
          <template v-if="localData.xml_path && !xmlPaths.length">
            <div class="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-300">
              <p class="font-semibold mb-1">{{ $t('logic.nodeConfig.extractor.legacyConfig') }}</p>
              <p class="text-amber-400/80 mb-2">{{ $t('logic.nodeConfig.extractor.pathLabel') }}: <code class="font-mono">{{ localData.xml_path }}</code></p>
              <button @click="migrateXmlToMultiPath" class="btn btn-sm bg-amber-600 hover:bg-amber-500 text-white text-xs px-2 py-1 rounded">
                {{ $t('logic.nodeConfig.extractor.upgradeToMultipleOutputs') }}
              </button>
            </div>
          </template>

          <!-- Multi-path path picker dropdown (one shared, fills active row) -->
          <div v-if="extractorPaths.length" class="form-group">
            <label class="label">
              {{ $t('logic.nodeConfig.extractor.choosePath') }}<span v-if="activeExtractorRow !== null" class="text-teal-400"> → {{ $t('logic.nodeConfig.extractor.outputN', { n: activeExtractorRow + 1 }) }}</span>
            </label>
            <select @change="onExtractorPathSelect" class="input text-sm" data-testid="extractor-path-select">
              <option value="">{{ $t('logic.nodeConfig.extractor.pathPlaceholder') }}</option>
              <option v-for="p in extractorPaths" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>

          <!-- Output rows -->
          <div class="form-group">
            <div class="flex items-center justify-between mb-1">
              <span class="section-label">{{ $t('logic.nodeConfig.extractor.outputsCount', { n: xmlPaths.length }) }}</span>
              <button
                @click="addXmlPath"
                class="w-6 h-6 flex items-center justify-center rounded text-teal-400 hover:bg-teal-400/10 font-bold text-lg leading-none"
                :title="$t('logic.nodeConfig.extractor.addOutput')"
              >+</button>
            </div>

            <div
              v-for="(entry, i) in xmlPaths" :key="i"
              class="extractor-output-row mt-2 p-2 rounded-lg border border-slate-700/50 flex flex-col gap-1"
              :style="extractorOutputRowStyle"
            >
              <div class="flex items-center gap-1">
                <span class="extractor-output-index text-xs font-mono w-5 shrink-0 text-center">{{ i + 1 }}</span>
                <input
                  :value="entry.label"
                  @input="updateXmlPath(i, 'label', $event.target.value)"
                  class="input text-xs flex-1"
                  :placeholder="$t('logic.nodeConfig.extractor.labelPlaceholder')"
                />
                <button
                  @click="removeXmlPath(i)"
                  class="extractor-output-remove w-5 h-5 flex items-center justify-center rounded hover:bg-red-400/10 text-base leading-none shrink-0"
                  :title="$t('logic.nodeConfig.extractor.removeOutput')"
                >−</button>
              </div>
              <input
                :value="entry.path"
                @input="updateXmlPath(i, 'path', $event.target.value)"
                @focus="activeExtractorRow = i"
                @blur="activeExtractorRow = null"
                class="input text-xs font-mono w-full"
                :class="activeExtractorRow === i ? 'ring-1 ring-teal-500/60' : ''"
                :placeholder="$t('logic.nodeConfig.extractor.xmlPathPlaceholder')"
                data-testid="extractor-path-input"
              />
              <p v-if="xmlPathPreview(i) !== null" class="text-xs text-teal-400">
                ↳ {{ String(xmlPathPreview(i)) }}
              </p>
            </div>

            <p v-if="!xmlPaths.length && !localData.xml_path" class="text-xs text-slate-500 mt-2 text-center py-2">
              {{ $t('logic.nodeConfig.extractor.clickPlusToAddOutputs') }}
            </p>
          </div>
        </template>
      </div>
    </template>

    <!-- ── substring_extractor ──────────────────────────────────────────── -->
    <template v-else-if="isSubstringExtractorNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Modus -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.substr.modeLabel') }}</label>
          <select v-model="localData.mode" @change="emitUpdate" class="input text-sm" data-testid="substr-mode">
            <option value="rechts_von">{{ $t('logic.nodeConfig.substr.modes.rechts_von') }}</option>
            <option value="links_von">{{ $t('logic.nodeConfig.substr.modes.links_von') }}</option>
            <option value="zwischen">{{ $t('logic.nodeConfig.substr.modes.zwischen') }}</option>
            <option value="ausschneiden">{{ $t('logic.nodeConfig.substr.modes.ausschneiden') }}</option>
            <option value="regex">{{ $t('logic.nodeConfig.substr.modes.regex') }}</option>
          </select>
        </div>

        <!-- Fields: links_von / rechts_von -->
        <template v-if="localData.mode === 'links_von' || localData.mode === 'rechts_von'">
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.searchLabel') }}</label>
            <input v-model="localData.search" @change="emitUpdate" class="input text-sm font-mono"
              :placeholder="$t('logic.nodeConfig.substr.searchPlaceholder')" data-testid="substr-search" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.occurrenceLabel') }}</label>
            <select v-model="localData.occurrence" @change="emitUpdate" class="input text-sm">
              <option value="first">{{ $t('logic.nodeConfig.substr.first') }}</option>
              <option value="last">{{ $t('logic.nodeConfig.substr.last') }}</option>
            </select>
          </div>
        </template>

        <!-- Fields: zwischen -->
        <template v-else-if="localData.mode === 'zwischen'">
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.startMarker') }}</label>
            <input v-model="localData.start_marker" @change="emitUpdate" class="input text-sm font-mono"
              :placeholder="$t('logic.nodeConfig.substr.startMarkerPlaceholder')" data-testid="substr-start-marker" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.endMarker') }}</label>
            <input v-model="localData.end_marker" @change="emitUpdate" class="input text-sm font-mono"
              :placeholder="$t('logic.nodeConfig.substr.endMarkerPlaceholder')" data-testid="substr-end-marker" />
          </div>
        </template>

        <!-- Fields: ausschneiden -->
        <template v-else-if="localData.mode === 'ausschneiden'">
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.startPos') }}</label>
            <input v-model.number="localData.start" @change="emitUpdate" type="number" min="0"
              class="input text-sm" data-testid="substr-start" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.length') }}</label>
            <input v-model.number="localData.length" @change="emitUpdate" type="number" min="-1"
              class="input text-sm" data-testid="substr-length" />
          </div>
        </template>

        <!-- Fields: regex -->
        <template v-else-if="localData.mode === 'regex'">
          <div class="form-group">
            <label class="label">
              {{ $t('logic.nodeConfig.substr.regexPattern') }}
              <a :href="substringRegex101Url" target="_blank" rel="noopener"
                class="ml-2 text-teal-400 underline text-xs" :title="$t('logic.nodeConfig.substr.regexOpen')">
                ↗ regex101.com
              </a>
            </label>
            <input v-model="localData.pattern" @change="emitUpdate" class="input text-sm font-mono"
              :placeholder="$t('logic.nodeConfig.substr.regexPlaceholder')" data-testid="substr-pattern" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.flags') }}</label>
            <input v-model="localData.flags" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="i" data-testid="substr-flags" />
          </div>
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.substr.group') }}</label>
            <input v-model.number="localData.group" @change="emitUpdate" type="number" min="0"
              class="input text-sm" data-testid="substr-group" />
          </div>
        </template>

        <!-- Test: empfangene Daten oder manuelle Eingabe -->
        <div class="form-group">
          <div class="section-label">{{ $t('logic.nodeConfig.substr.testSection') }}</div>
          <textarea
            v-model="substrTestInput"
            rows="5"
            class="input text-xs font-mono resize-y"
            style="background:#0f172a;color:#94a3b8"
            :placeholder="extractorPreview || $t('logic.nodeConfig.substr.testPlaceholder')"
            data-testid="substr-test-input"
          />
          <p v-if="substrTestResult !== null" class="text-xs text-teal-400 mt-1" data-testid="substr-test-result">
            ↳ {{ String(substrTestResult) }}
          </p>
          <p v-else-if="substrTestInput || extractorPreview" class="text-xs text-slate-500 mt-1">
            {{ $t('logic.nodeConfig.substr.noMatch') }}
          </p>
        </div>
      </div>
    </template>

    <!-- ── iCalendar ──────────────────────────────────────────────────────── -->
    <template v-else-if="isICalNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- URL -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.ical.urlLabel') }}</label>
          <input v-model="localData.url" type="text" class="input text-sm"
            placeholder="https://example.com/calendar.ics"
            @change="emitUpdate" data-testid="ical-url" />
        </div>

        <!-- Refresh interval -->
        <div class="form-group">
          <label class="label">{{ $t('logic.nodeConfig.ical.refreshLabel') }}</label>
          <input v-model.number="localData.refresh_interval_min" type="number" min="1"
            class="input text-sm" @change="emitUpdate" data-testid="ical-refresh" />
          <p class="text-xs text-slate-500 mt-1">{{ $t('logic.nodeConfig.ical.refreshHint') }}</p>
        </div>

        <!-- RAW output info -->
        <p class="text-xs text-slate-400">
          {{ $t('logic.nodeConfig.ical.rawInfo') }}
        </p>

        <!-- Filter list -->
        <div class="section-label flex items-center justify-between">
          <span>{{ $t('logic.nodeConfig.ical.filtersSection') }}</span>
          <button @click="icalAddFilter" class="btn-secondary btn-sm text-teal-400"
            data-testid="ical-add-filter">{{ $t('logic.nodeConfig.ical.addFilter') }}</button>
        </div>

        <div v-if="icalFilters.length === 0" class="text-xs text-slate-500 italic">
          {{ $t('logic.nodeConfig.ical.noFilters') }}
        </div>

        <div v-for="(flt, i) in icalFilters" :key="i"
          class="border border-slate-700 rounded-lg p-3 flex flex-col gap-2 bg-slate-900/40">

          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-semibold text-teal-400">{{ $t('logic.nodeConfig.ical.filterN', { n: i + 1 }) }}</span>
            <button @click="icalRemoveFilter(i)"
              class="text-xs text-red-400 hover:text-red-300"
              :data-testid="`ical-remove-filter-${i}`">{{ $t('logic.nodeConfig.ical.remove') }}</button>
          </div>

          <!-- Name -->
          <div class="form-group">
            <label class="label">{{ $t('logic.nodeConfig.ical.nameLabel') }}</label>
            <input :value="flt.name" @input="icalUpdateFilter(i, 'name', $event.target.value)"
              @change="emitUpdate" type="text" class="input text-sm"
              :placeholder="$t('logic.nodeConfig.ical.namePlaceholder')" :data-testid="`ical-filter-name-${i}`" />
          </div>

          <!-- AND / OR toggle -->
          <div class="flex items-center gap-2">
            <span class="text-xs text-slate-400">{{ $t('logic.nodeConfig.ical.linkLogic') }}</span>
            <button
              @click="icalUpdateFilter(i, 'field_logic', 'or')"
              :class="['px-2 py-0.5 rounded text-xs font-semibold transition-colors',
                (flt.field_logic || 'or') === 'or'
                  ? 'bg-teal-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600']"
              :data-testid="`ical-filter-logic-or-${i}`">{{ $t('logic.nodeConfig.ical.or') }}</button>
            <button
              @click="icalUpdateFilter(i, 'field_logic', 'and')"
              :class="['px-2 py-0.5 rounded text-xs font-semibold transition-colors',
                flt.field_logic === 'and'
                  ? 'bg-teal-600 text-white'
                  : 'bg-slate-700 text-slate-400 hover:bg-slate-600']"
              :data-testid="`ical-filter-logic-and-${i}`">{{ $t('logic.nodeConfig.ical.and') }}</button>
          </div>

          <!-- Per-field patterns -->
          <div v-for="field in [
              { key: 'summary_pattern',     label: t('logic.nodeConfig.ical.summaryLabel'),     placeholder: t('logic.nodeConfig.ical.summaryPlaceholder') },
              { key: 'location_pattern',    label: t('logic.nodeConfig.ical.locationLabel'),    placeholder: t('logic.nodeConfig.ical.locationPlaceholder') },
              { key: 'description_pattern', label: t('logic.nodeConfig.ical.descriptionLabel'), placeholder: t('logic.nodeConfig.ical.descriptionPlaceholder') },
            ]" :key="field.key" class="form-group">
            <label class="label text-slate-400">{{ field.label }}</label>
            <input
              :value="flt[field.key] || ''"
              @input="icalUpdateFilter(i, field.key, $event.target.value)"
              @change="emitUpdate"
              type="text" class="input text-sm font-mono"
              :placeholder="field.placeholder + ' ' + $t('logic.nodeConfig.ical.ignoreEmpty')"
              :data-testid="`ical-filter-${field.key}-${i}`" />
          </div>
          <p class="text-xs text-slate-500 -mt-1">{{ $t('logic.nodeConfig.ical.reSyntax') }}</p>

          <!-- Case sensitive -->
          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox"
              :checked="!!flt.case_sensitive"
              @change="icalUpdateFilter(i, 'case_sensitive', $event.target.checked)"
              class="accent-teal-500" :data-testid="`ical-filter-case-${i}`" />
            <span class="text-xs text-slate-300">{{ $t('logic.nodeConfig.ical.caseSensitive') }}</span>
          </label>

          <!-- Output port summary -->
          <div class="text-xs text-slate-500 mt-1 font-mono leading-relaxed">
            {{ $t('logic.nodeConfig.ical.outputs', { i }) }}
          </div>
        </div>
      </div>
    </template>

    <!-- ── All other node types: generic rendering ─────────────────────── -->
    <template v-else>
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p v-if="nodeDef?.description" class="text-xs text-slate-500">{{ nodeDef.description }}</p>
        <template v-if="nodeDef?.config_schema">
          <div v-for="(schema, key) in configFields" :key="key" class="form-group">
            <label class="label">{{ schema.label ?? key }}</label>
            <textarea v-if="schema.type === 'string' && key === 'script'"
              v-model="localData[key]" rows="6"
              class="input text-xs font-mono resize-y" @change="emitUpdate" />
            <select v-else-if="schema.enum"
              v-model="localData[key]" class="input text-sm" @change="emitUpdate">
              <option v-for="opt in schema.enum" :key="opt" :value="opt">{{ opt }}</option>
            </select>
            <input v-else-if="schema.type === 'boolean'"
              type="checkbox" v-model="localData[key]"
              class="text-sm" @change="emitUpdate" />
            <input v-else
              v-model="localData[key]"
              :type="schema.subtype === 'password' ? 'password' : schema.type === 'number' ? 'number' : 'text'"
              class="input text-sm" @change="emitUpdate" />
          </div>

        </template>
      </div>
    </template>

  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { dpApi, searchApi } from '@/api/client'
import { getAutoContrastText } from '@/utils/colorContrast'

const { t, te } = useI18n()

const props = defineProps({
  node:        { type: Object, default: null },
  nodeTypes:   { type: Array,  default: () => [] },
  nodeOutputs: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update', 'close'])

const EXTRACTOR_OUTPUT_BG = 'rgba(30, 41, 59, 0.6)'
const EXTRACTOR_OUTPUT_FG = getAutoContrastText(EXTRACTOR_OUTPUT_BG)
const extractorOutputRowStyle = {
  '--extractor-output-bg': EXTRACTOR_OUTPUT_BG,
  '--extractor-output-fg': EXTRACTOR_OUTPUT_FG,
}

// ── State ──────────────────────────────────────────────────────────────────
const localData          = ref({})
const dpSearch           = ref('')
const dpResults          = ref([])
const activeTab          = ref('connection')
const valueMapPreset     = ref('')
const valueMapCustom     = ref('')
const valueMapCustomError = ref('')

// ── Value Map Presets ──────────────────────────────────────────────────────
const VALUE_MAP_PRESETS = computed(() => [
  { key: '',            label: t('logic.nodeConfig.transform.noMapping'),            map: null },
  { key: 'num_invert',  label: t('logic.nodeConfig.transform.mapPresetNumInvert'),   map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: t('logic.nodeConfig.transform.mapPresetBoolOnOff'),   map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: t('logic.nodeConfig.transform.mapPresetOnOffBool'),   map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: t('logic.nodeConfig.transform.mapPresetNumOnOff'),    map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: t('logic.nodeConfig.transform.mapPresetOnOffNum'),    map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: t('logic.nodeConfig.transform.customJson'),            map: null },
])

// ── Formula Presets ────────────────────────────────────────────────────────
const MULTIPLY_PRESETS = computed(() => [
  { f: 'x * 86400',  label: t('logic.nodeConfig.presets.mul86400') },
  { f: 'x * 3600',   label: t('logic.nodeConfig.presets.mul3600')  },
  { f: 'x * 1440',   label: t('logic.nodeConfig.presets.mul1440')  },
  { f: 'x * 1000',   label: t('logic.nodeConfig.presets.mul1000')  },
  { f: 'x * 100',    label: t('logic.nodeConfig.presets.mul100')   },
  { f: 'x * 60',     label: t('logic.nodeConfig.presets.mul60')    },
  { f: 'x * 10',     label: t('logic.nodeConfig.presets.mul10')    },
])
const DIVIDE_PRESETS = computed(() => [
  { f: 'round(x / 10, 1)',    label: t('logic.nodeConfig.presets.div10')   },
  { f: 'x / 60',              label: t('logic.nodeConfig.presets.div60')   },
  { f: 'round(x / 100, 2)',   label: t('logic.nodeConfig.presets.div100')  },
  { f: 'round(x / 1000, 3)',  label: t('logic.nodeConfig.presets.div1000') },
  { f: 'x / 1440',            label: t('logic.nodeConfig.presets.div1440') },
  { f: 'x / 3600',            label: t('logic.nodeConfig.presets.div3600') },
  { f: 'x / 86400',           label: t('logic.nodeConfig.presets.div86400') },
])
const ALL_PRESETS = computed(() => [...MULTIPLY_PRESETS.value, ...DIVIDE_PRESETS.value])

// ── Cron Presets ───────────────────────────────────────────────────────────
const CRON_PRESETS_INTERVAL = computed(() => [
  { expr: '* * * * *',      label: t('logic.nodeConfig.cron.presets.everyMinute') },
  { expr: '*/5 * * * *',    label: t('logic.nodeConfig.cron.presets.every5Min')   },
  { expr: '*/10 * * * *',   label: t('logic.nodeConfig.cron.presets.every10Min')  },
  { expr: '*/15 * * * *',   label: t('logic.nodeConfig.cron.presets.every15Min')  },
  { expr: '*/30 * * * *',   label: t('logic.nodeConfig.cron.presets.every30Min')  },
])
const CRON_PRESETS_HOURLY = computed(() => [
  { expr: '0 * * * *',     label: t('logic.nodeConfig.cron.presets.everyHour')    },
  { expr: '0 */2 * * *',   label: t('logic.nodeConfig.cron.presets.every2Hours')  },
  { expr: '0 */4 * * *',   label: t('logic.nodeConfig.cron.presets.every4Hours')  },
  { expr: '0 */6 * * *',   label: t('logic.nodeConfig.cron.presets.every6Hours')  },
  { expr: '0 */12 * * *',  label: t('logic.nodeConfig.cron.presets.every12Hours') },
])
const CRON_PRESETS_DAILY = computed(() => [
  { expr: '0 0 * * *',       label: t('logic.nodeConfig.cron.presets.daily0000')      },
  { expr: '0 6 * * *',       label: t('logic.nodeConfig.cron.presets.daily0600')      },
  { expr: '0 7 * * *',       label: t('logic.nodeConfig.cron.presets.daily0700')      },
  { expr: '0 8 * * *',       label: t('logic.nodeConfig.cron.presets.daily0800')      },
  { expr: '0 9 * * *',       label: t('logic.nodeConfig.cron.presets.daily0900')      },
  { expr: '0 12 * * *',      label: t('logic.nodeConfig.cron.presets.daily1200')      },
  { expr: '0 17 * * *',      label: t('logic.nodeConfig.cron.presets.daily1700')      },
  { expr: '0 18 * * *',      label: t('logic.nodeConfig.cron.presets.daily1800')      },
  { expr: '0 22 * * *',      label: t('logic.nodeConfig.cron.presets.daily2200')      },
  { expr: '0 6,18 * * *',    label: t('logic.nodeConfig.cron.presets.daily0618')      },
  { expr: '0 8,12,18 * * *', label: t('logic.nodeConfig.cron.presets.daily081218')    },
  { expr: '0 6 * * 1-5',     label: t('logic.nodeConfig.cron.presets.weekdays0600')   },
  { expr: '0 7 * * 1-5',     label: t('logic.nodeConfig.cron.presets.weekdays0700')   },
  { expr: '0 9 * * 1-5',     label: t('logic.nodeConfig.cron.presets.weekdays0900')   },
  { expr: '0 8-17 * * 1-5',  label: t('logic.nodeConfig.cron.presets.weekdaysHourly') },
])
const CRON_PRESETS_OTHER = computed(() => [
  { expr: '0 9 * * 1',    label: t('logic.nodeConfig.cron.presets.mondays0900')      },
  { expr: '0 0 * * 0',    label: t('logic.nodeConfig.cron.presets.sundaysMidnight')  },
  { expr: '0 0 * * 1',    label: t('logic.nodeConfig.cron.presets.mondaysMidnight')  },
  { expr: '0 0 1 * *',    label: t('logic.nodeConfig.cron.presets.monthly1st')        },
  { expr: '0 0 15 * *',   label: t('logic.nodeConfig.cron.presets.monthly15th')       },
  { expr: '0 0 1 1 *',    label: t('logic.nodeConfig.cron.presets.yearlyJan')         },
  { expr: '0 0 1 4 *',    label: t('logic.nodeConfig.cron.presets.yearlyApr')         },
  { expr: '0 0 1 10 *',   label: t('logic.nodeConfig.cron.presets.yearlyOct')         },
])
const ALL_CRON_PRESETS = computed(() => [
  ...CRON_PRESETS_INTERVAL.value,
  ...CRON_PRESETS_HOURLY.value,
  ...CRON_PRESETS_DAILY.value,
  ...CRON_PRESETS_OTHER.value,
])

// ── Cron field state ───────────────────────────────────────────────────────
const cronFields = ref([
  { key: 'min',     value: '0', placeholder: '0' },
  { key: 'hour',    value: '7', placeholder: '*' },
  { key: 'day',     value: '*', placeholder: '*' },
  { key: 'month',   value: '*', placeholder: '*' },
  { key: 'weekday', value: '*', placeholder: '*' },
])

const CRON_FIELD_LABELS = computed(() => [
  { key: 'min',     label: t('logic.nodeConfig.cron.fields.min'),     placeholder: '0', hint: '0–59'                                        },
  { key: 'hour',    label: t('logic.nodeConfig.cron.fields.hour'),    placeholder: '*', hint: '0–23'                                        },
  { key: 'day',     label: t('logic.nodeConfig.cron.fields.day'),     placeholder: '*', hint: '1–31'                                        },
  { key: 'month',   label: t('logic.nodeConfig.cron.fields.month'),   placeholder: '*', hint: '1–12'                                        },
  { key: 'weekday', label: t('logic.nodeConfig.cron.fields.weekday'), placeholder: '*', hint: t('logic.nodeConfig.cron.fields.weekdayHint') },
])

function parseCronToFields(expr) {
  const parts = (expr || '0 7 * * *').trim().split(/\s+/)
  if (parts.length === 5) {
    cronFields.value[0].value = parts[0]
    cronFields.value[1].value = parts[1]
    cronFields.value[2].value = parts[2]
    cronFields.value[3].value = parts[3]
    cronFields.value[4].value = parts[4]
  }
}

function cronFieldsToExpr() {
  return cronFields.value.map(f => f.value || '*').join(' ')
}

function onCronFieldChange() {
  localData.value.cron = cronFieldsToExpr()
  emitUpdate()
}

function onCronExprChange() {
  parseCronToFields(localData.value.cron)
  emitUpdate()
}

const cronPresetValue = computed(() => {
  const expr = (localData.value.cron || '').trim()
  return ALL_CRON_PRESETS.value.find(p => p.expr === expr)?.expr ?? ''
})

function onCronPresetChange(e) {
  const expr = e.target.value
  if (expr) {
    localData.value.cron = expr
    parseCronToFields(expr)
    emitUpdate()
  }
}

const cronDescription = computed(() => {
  const expr = (localData.value.cron || '').trim()
  return ALL_CRON_PRESETS.value.find(p => p.expr === expr)?.label ?? ''
})

// ── Computed ───────────────────────────────────────────────────────────────
const nodeDef = computed(() => props.nodeTypes.find(nt => nt.type === props.node?.type))

const isDatapointNode = computed(() =>
  props.node?.type === 'datapoint_read' || props.node?.type === 'datapoint_write'
)
const isWrite          = computed(() => props.node?.type === 'datapoint_write')
const isCronNode       = computed(() => props.node?.type === 'timer_cron')
const isMathFormulaNode = computed(() => props.node?.type === 'math_formula')
const isApiClientNode  = computed(() => props.node?.type === 'api_client')
const isExtractorNode  = computed(() =>
  props.node?.type === 'json_extractor' || props.node?.type === 'xml_extractor'
)
const isSubstringExtractorNode = computed(() => props.node?.type === 'substring_extractor')
const isStringConcatNode = computed(() => props.node?.type === 'string_concat')
const isICalNode          = computed(() => props.node?.type === 'ical')

// ── iCal: filter management ───────────────────────────────────────────────
const icalFilters = computed(() => {
  try {
    const parsed = JSON.parse(localData.value.filters || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch { return [] }
})

function _icalSave(filters) {
  localData.value.filters = JSON.stringify(filters)
  localData.value.filter_count = filters.length
  emitUpdate()
}

function icalAddFilter() {
  const filters = icalFilters.value.slice()
  filters.push({
    name: t('logic.nodeConfig.ical.filterN', { n: filters.length + 1 }),
    field_logic: 'or',
    summary_pattern: '',
    location_pattern: '',
    description_pattern: '',
    case_sensitive: false,
  })
  _icalSave(filters)
}

function icalRemoveFilter(i) {
  const filters = icalFilters.value.slice()
  filters.splice(i, 1)
  _icalSave(filters)
}

function icalUpdateFilter(i, key, value) {
  const filters = icalFilters.value.map(f => ({ ...f }))
  filters[i][key] = value
  _icalSave(filters)
}

// ── string_concat: dynamic slot count ─────────────────────────────────────
const concatCount = computed(() => Math.max(2, Math.min(20, Number(localData.value.count) || 2)))
const concatSlots = computed(() => Array.from({ length: concatCount.value }, (_, i) => i + 1))

function onConcatCountChange(e) {
  const v = Math.max(2, Math.min(20, parseInt(e.target.value) || 2))
  localData.value.count = v
  emitUpdate()
}

// ── Extractor: preview + path helpers ─────────────────────────────────────
const activeExtractorRow = ref(null)

const extractorPreview = computed(() => {
  if (!props.node) return ''
  return props.nodeOutputs[props.node.id]?._preview ?? ''
})

// Flatten all dot-notation paths from a JSON object (max depth 6)
function _flattenJsonPaths(obj, prefix = '', depth = 0) {
  if (depth > 6 || obj === null || typeof obj !== 'object') {
    return prefix ? [prefix] : []
  }
  const paths = []
  if (Array.isArray(obj)) {
    obj.forEach((item, i) => {
      const key = `${prefix}[${i}]`
      if (item !== null && typeof item === 'object') {
        paths.push(..._flattenJsonPaths(item, key, depth + 1))
      } else {
        paths.push(key)
      }
    })
  } else {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k
      if (v !== null && typeof v === 'object') {
        paths.push(..._flattenJsonPaths(v, key, depth + 1))
      } else {
        paths.push(key)
      }
    }
  }
  return paths
}

// Collect XPath expressions from XML — simple .//tag plus positional .//tag[n]/child paths
function _collectXmlPaths(rootEl) {
  const paths = new Set()

  // Collect descendant paths anchored below a positional base (e.g. .//book[1])
  function collectBelow(el, basePath, depth) {
    if (depth > 5) return
    for (const child of Array.from(el.children)) {
      const tag = child.tagName
      const siblings = Array.from(el.children).filter(c => c.tagName === tag)
      const seg = siblings.length > 1 ? `${tag}[${siblings.indexOf(child) + 1}]` : tag
      const p = `${basePath}/${seg}`
      paths.add(p)
      collectBelow(child, p, depth + 1)
    }
  }

  function walk(el, depth) {
    if (depth > 8) return
    const tag = el.tagName
    paths.add(`.//${tag}`)

    // Attribute-based discriminators
    for (const attr of Array.from(el.attributes || [])) {
      paths.add(`.//${tag}[@${attr.name}="${attr.value}"]`)
    }

    // Positional path when there are multiple siblings with same tag
    const parent = el.parentElement
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === tag)
      if (siblings.length > 1) {
        const idx = siblings.indexOf(el) + 1
        const base = `.//` + tag + `[${idx}]`
        paths.add(base)
        collectBelow(el, base, 1)
      }
    }

    for (const child of Array.from(el.children)) {
      walk(child, depth + 1)
    }
  }

  walk(rootEl, 0)
  return [...paths]
}

const extractorPaths = computed(() => {
  const preview = extractorPreview.value
  if (!preview) return []
  if (props.node?.type === 'json_extractor') {
    try {
      const obj = JSON.parse(preview)
      return _flattenJsonPaths(obj)
    } catch { return [] }
  } else {
    try {
      const doc = new DOMParser().parseFromString(preview, 'text/xml')
      if (doc.querySelector('parsererror')) return []
      return _collectXmlPaths(doc.documentElement)
    } catch { return [] }
  }
})

// Live-evaluate current path against preview to show resolved value
const extractorPreviewValue = computed(() => {
  const preview = extractorPreview.value
  if (!preview) return null
  if (props.node?.type === 'json_extractor') {
    const path = (localData.value.json_path || '').trim()
    if (!path) return null
    try {
      const obj = JSON.parse(preview)
      // Traverse dotted path (same logic as backend _json_extract)
      const normPath = path.replace(/\[(\d+)\]/g, '.$1')
      const parts = normPath.split('.').filter(Boolean)
      let cur = obj
      for (const p of parts) {
        if (cur === null || typeof cur !== 'object') return null
        cur = Array.isArray(cur) ? cur[Number(p)] : cur[p]
      }
      return cur !== undefined ? cur : null
    } catch { return null }
  } else {
    const path = (localData.value.xml_path || '').trim()
    if (!path) return null
    try {
      const doc = new DOMParser().parseFromString(preview, 'text/xml')
      if (doc.querySelector('parseerror')) return null
      const el = doc.evaluate(path, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
      return el ? el.textContent?.trim() ?? null : null
    } catch { return null }
  }
})

// ── JSON Extractor: multi-path management ─────────────────────────────────
const jsonPaths = computed(() => {
  if (props.node?.type !== 'json_extractor') return []
  try {
    const parsed = JSON.parse(localData.value.json_paths || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch { return [] }
})

function _saveJsonPaths(paths) {
  localData.value.json_paths = JSON.stringify(paths)
  emitUpdate()
}

function addJsonPath() {
  const paths = jsonPaths.value.slice()
  paths.push({ label: `Wert ${paths.length + 1}`, path: '' })
  _saveJsonPaths(paths)
  activeExtractorRow.value = paths.length - 1
}

function removeJsonPath(i) {
  const paths = jsonPaths.value.slice()
  paths.splice(i, 1)
  _saveJsonPaths(paths)
  activeExtractorRow.value = paths.length > 0 ? Math.min(i, paths.length - 1) : null
}

function updateJsonPath(i, key, value) {
  const paths = jsonPaths.value.map(p => ({ ...p }))
  paths[i][key] = value
  _saveJsonPaths(paths)
}

function jsonPathPreview(i) {
  const preview = extractorPreview.value
  if (!preview) return null
  const entry = jsonPaths.value[i]
  if (!entry?.path) return null
  try {
    const obj = JSON.parse(preview)
    const normPath = entry.path.replace(/\[(\d+)\]/g, '.$1')
    const parts = normPath.split('.').filter(Boolean)
    let cur = obj
    for (const p of parts) {
      if (cur === null || typeof cur !== 'object') return null
      cur = Array.isArray(cur) ? cur[Number(p)] : cur[p]
    }
    return cur !== undefined ? cur : null
  } catch { return null }
}

function migrateJsonToMultiPath() {
  const legacyPath = (localData.value.json_path || '').trim()
  if (!legacyPath) return
  localData.value.json_paths = JSON.stringify([{ label: 'Wert 1', path: legacyPath }])
  localData.value.json_path = ''
  emitUpdate()
}

// ── XML Extractor: multi-path management ──────────────────────────────────
const xmlPaths = computed(() => {
  if (props.node?.type !== 'xml_extractor') return []
  try {
    const parsed = JSON.parse(localData.value.xml_paths || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch { return [] }
})

function _saveXmlPaths(paths) {
  localData.value.xml_paths = JSON.stringify(paths)
  emitUpdate()
}

function addXmlPath() {
  const paths = xmlPaths.value.slice()
  paths.push({ label: t('logic.nodeConfig.extractor.valueN', { n: paths.length + 1 }), path: '' })
  _saveXmlPaths(paths)
  activeExtractorRow.value = paths.length - 1
}

function removeXmlPath(i) {
  const paths = xmlPaths.value.slice()
  paths.splice(i, 1)
  _saveXmlPaths(paths)
  activeExtractorRow.value = paths.length > 0 ? Math.min(i, paths.length - 1) : null
}

function updateXmlPath(i, key, value) {
  const paths = xmlPaths.value.map(p => ({ ...p }))
  paths[i][key] = value
  _saveXmlPaths(paths)
}

function xmlPathPreview(i) {
  const preview = extractorPreview.value
  if (!preview) return null
  const entry = xmlPaths.value[i]
  if (!entry?.path) return null
  try {
    const doc = new DOMParser().parseFromString(preview, 'text/xml')
    if (doc.querySelector('parsererror')) return null
    const el = doc.evaluate(entry.path, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
    return el ? el.textContent?.trim() ?? null : null
  } catch { return null }
}

function migrateXmlToMultiPath() {
  const legacyPath = (localData.value.xml_path || '').trim()
  if (!legacyPath) return
  localData.value.xml_paths = JSON.stringify([{ label: t('logic.nodeConfig.extractor.valueN', { n: 1 }), path: legacyPath }])
  localData.value.xml_path = ''
  emitUpdate()
}

// ── Substring / RegEx extractor ───────────────────────────────────────────
const substrTestInput = ref('')

// Use typed test input if present, otherwise fall back to received preview
const _substrSource = computed(() => substrTestInput.value || extractorPreview.value || '')

const substrTestResult = computed(() => {
  const text = _substrSource.value
  if (!text) return null
  const mode = localData.value.mode || 'rechts_von'
  try {
    if (mode === 'links_von' || mode === 'rechts_von') {
      const search = localData.value.search || ''
      if (!search) return null
      const occ = localData.value.occurrence || 'first'
      const idx = occ === 'last' ? text.lastIndexOf(search) : text.indexOf(search)
      if (idx === -1) return null
      return mode === 'links_von' ? text.slice(0, idx) : text.slice(idx + search.length)
    }
    if (mode === 'zwischen') {
      const sm = localData.value.start_marker || ''
      const em = localData.value.end_marker   || ''
      if (!sm || !em) return null
      const is = text.indexOf(sm)
      if (is === -1) return null
      const afterS = is + sm.length
      const ie = text.indexOf(em, afterS)
      return ie === -1 ? null : text.slice(afterS, ie)
    }
    if (mode === 'ausschneiden') {
      const start  = Number(localData.value.start  ?? 0)
      const length = Number(localData.value.length ?? -1)
      return length < 0 ? text.slice(start) : text.slice(start, start + length)
    }
    if (mode === 'regex') {
      const pattern = localData.value.pattern || ''
      if (!pattern) return null
      const flagStr = (localData.value.flags || '').toLowerCase().replace(/[^ims]/g, '')
      const re = new RegExp(pattern, flagStr)
      const m  = text.match(re)
      if (!m) return null
      const group = Number(localData.value.group ?? 0)
      return m[group] !== undefined ? m[group] : null
    }
  } catch { return null }
  return null
})

const substringRegex101Url = computed(() => {
  const pattern = localData.value.pattern || ''
  const flags   = (localData.value.flags   || '').toLowerCase().replace(/[^ims]/g, '')
  const test    = _substrSource.value.slice(0, 2000)
  const params  = new URLSearchParams({ regex: pattern, flags, testString: test })
  return `https://regex101.com/?${params.toString()}`
})

const configFields = computed(() => {
  const schema = nodeDef.value?.config_schema ?? {}
  return Object.fromEntries(
    Object.entries(schema).filter(([k]) => !k.startsWith('datapoint_'))
  )
})

const formulaPreset = computed({
  get() {
    const f = localData.value.value_formula || ''
    if (!f) return ''
    return ALL_PRESETS.value.find(p => p.f === f)?.f ?? '__custom__'
  },
  set(v) { void v },
})

const outputFormulaPreset = computed(() => {
  const f = localData.value.output_formula || ''
  if (!f) return ''
  return ALL_PRESETS.value.find(p => p.f === f)?.f ?? '__custom__'
})

const hasTransform = computed(() =>
  !!(localData.value.value_formula || '').trim() || !!valueMapPreset.value
)
const hasFilter    = computed(() => {
  const d = localData.value
  return boolVal('trigger_on_change') || boolVal('only_on_change') ||
         !!(d.min_delta || d.min_delta_pct || d.throttle_value)
})

const tabs = computed(() => [
  { id: 'connection', label: t('logic.nodeConfig.tabs.connection'), dot: false              },
  { id: 'transform',  label: t('logic.nodeConfig.tabs.transform'),  dot: hasTransform.value },
  { id: 'filter',     label: t('logic.nodeConfig.tabs.filter'),     dot: hasFilter.value    },
])

// ── Helpers ────────────────────────────────────────────────────────────────
function boolVal(key) {
  const v = localData.value[key]
  return v === true || v === 'true'
}
function setBool(key, val) {
  localData.value[key] = val
}

// ── Watchers ───────────────────────────────────────────────────────────────
watch(() => props.node, (n) => {
  if (n) {
    localData.value = { ...n.data }
    dpSearch.value  = n.data.datapoint_name || ''
    dpResults.value = []
    activeTab.value = 'connection'
    activeExtractorRow.value = null
    if (n.type === 'timer_cron') {
      parseCronToFields(n.data.cron || '0 7 * * *')
    }
    if (n.type === 'api_client' && !localData.value.auth_type) {
      localData.value.auth_type = 'none'
    }
    if (n.type === 'datapoint_read' || n.type === 'datapoint_write') {
      searchDps()
      // Restore value_map UI state — but don't overwrite if user just picked 'custom'
      const vm = n.data.value_map
      if (vm && typeof vm === 'object') {
        const mapStr = JSON.stringify(vm)
        const preset = VALUE_MAP_PRESETS.value.find(p => p.map && JSON.stringify(p.map) === mapStr)
        valueMapPreset.value = preset?.key ?? 'custom'
        valueMapCustom.value = preset ? '' : JSON.stringify(vm, null, 2)
      } else if (valueMapPreset.value !== 'custom') {
        // Only reset to empty if the user hasn't actively chosen 'custom'
        valueMapPreset.value = ''
        valueMapCustom.value = ''
      }
    }
  }
}, { immediate: true })

// ── Preset / formula handlers ──────────────────────────────────────────────
function onPresetChange(e) {
  const val = e.target.value
  if (val && val !== '__custom__') {
    localData.value.value_formula = val
    emitUpdate()
  }
}
function onFormulaInput() { /* formulaPreset computed switches to __custom__ */ }

function onOutputPresetChange(e) {
  const val = e.target.value
  if (val && val !== '__custom__') {
    localData.value.output_formula = val
    emitUpdate()
  }
}

function onValueMapPresetChange() {
  valueMapCustomError.value = ''
  if (valueMapPreset.value === 'custom') {
    // Nur Textarea anzeigen, noch nichts speichern — value_map bleibt wie sie ist
    valueMapCustom.value = ''
    return
  }
  valueMapCustom.value = ''
  const preset = VALUE_MAP_PRESETS.value.find(p => p.key === valueMapPreset.value)
  localData.value.value_map = preset?.map ?? null
  emitUpdate()
}

function onExtractorPathSelect(e) {
  const path = e.target.value
  if (!path || !props.node) return
  const isJson = props.node.type === 'json_extractor'
  const pathList = isJson ? jsonPaths.value : xmlPaths.value
  const updateFn = isJson ? updateJsonPath : updateXmlPath
  const saveFn   = isJson ? _saveJsonPaths : _saveXmlPaths

  // Fill the active row, or last row, or add a new row
  let target = activeExtractorRow.value
  if (target === null || target >= pathList.length) {
    target = pathList.length - 1
  }
  if (target >= 0) {
    updateFn(target, 'path', path)
    activeExtractorRow.value = target
  } else {
    // No rows yet — add one
    saveFn([{ label: t('logic.nodeConfig.extractor.valueN', { n: 1 }), path }])
    activeExtractorRow.value = 0
  }
  e.target.value = ''
}

function onValueMapCustomInput(e) {
  const val = e.target.value.trim()
  if (!val) { valueMapCustomError.value = ''; return }
  try {
    JSON.parse(val)
    valueMapCustomError.value = ''
  } catch (err) {
    valueMapCustomError.value = t('logic.nodeConfig.invalidJson', { msg: err.message })
  }
}

function onValueMapCustomChange() {
  valueMapCustomError.value = ''
  try {
    localData.value.value_map = JSON.parse(valueMapCustom.value)
  } catch (e) {
    valueMapCustomError.value = t('logic.nodeConfig.invalidJson', { msg: e.message })
    localData.value.value_map = null
  }
  emitUpdate()
}

// ── DataPoint picker ───────────────────────────────────────────────────────
async function searchDps() {
  try {
    if (dpSearch.value.length < 1) {
      const { data } = await dpApi.list(0, 50)
      dpResults.value = data.items || data
    } else {
      const { data } = await searchApi.search({ q: dpSearch.value, size: 50 })
      dpResults.value = data.items || data
    }
  } catch { dpResults.value = [] }
}

function selectDp(dp) {
  localData.value.datapoint_id   = dp.id
  localData.value.datapoint_name = dp.name
  dpSearch.value  = dp.name
  dpResults.value = []
  emitUpdate()
}

// ── Emit ───────────────────────────────────────────────────────────────────
function emitUpdate() {
  emit('update', { ...localData.value })
}
</script>

<style scoped>
.tab-btn {
  flex: 1;
  padding: 8px 4px 6px;
  font-size: 11px;
  font-weight: 500;
  color: #475569;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color .15s, border-color .15s;
  white-space: nowrap;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
}
.tab-btn:hover   { color: #334155; }
.tab-btn--active { color: #0f172a; border-bottom-color: #0d9488; }
.tab-dot { color: #0d9488; font-size: 14px; line-height: 1; }

:global(.dark) .tab-btn          { color: #64748b; }
:global(.dark) .tab-btn:hover    { color: #94a3b8; }
:global(.dark) .tab-btn--active  { color: #e2e8f0; border-bottom-color: #14b8a6; }
:global(.dark) .tab-dot          { color: #14b8a6; }

.section-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
  color: #0d9488;
  margin-bottom: 4px;
}
:global(.dark) .section-label { color: #14b8a6; }

.form-group { display: flex; flex-direction: column; gap: 4px; }
.label      { font-size: 11px; font-weight: 500; color: #475569; }
:global(.dark) .label { color: #94a3b8; }

/* ── Cron builder ─────────────────────────────────────────────────────── */
.cron-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 5px;
}
.cron-field {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}
.cron-field .input {
  width: 100%;
  min-width: 0;
  padding-left: 2px;
  padding-right: 2px;
}
.cron-field-label {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .05em;
  color: #475569;
}
:global(.dark) .cron-field-label { color: #64748b; }

.cron-field-hint {
  font-size: 8px;
  color: #64748b;
  white-space: nowrap;
}
:global(.dark) .cron-field-hint { color: #475569; }

.cron-legend {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 9px;
  color: #64748b;
}
:global(.dark) .cron-legend { color: #475569; }

.cron-legend code {
  color: #475569;
  font-size: 9px;
}
:global(.dark) .cron-legend code { color: #94a3b8; }

.extractor-output-row {
  background: var(--extractor-output-bg);
}

.extractor-output-index {
  color: var(--extractor-output-fg);
  opacity: .95;
}

.extractor-output-remove {
  color: var(--extractor-output-fg);
  opacity: .85;
  transition: color .15s, opacity .15s;
}

.extractor-output-remove:hover {
  color: #f87171;
  opacity: 1;
}
</style>
