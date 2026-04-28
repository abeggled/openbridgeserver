<template>
  <div v-if="node" class="h-full flex flex-col bg-surface-800 border-l border-slate-200 dark:border-slate-700/60 w-72">

    <!-- Header -->
    <div class="px-4 py-3 border-b border-slate-200 dark:border-slate-700/60 flex items-center justify-between">
      <h3 class="text-sm font-semibold text-slate-700 dark:text-slate-200">{{ nodeDef?.label ?? node.type }}</h3>
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
        <div v-show="activeTab === 'connection'" class="p-4 flex flex-col gap-3">
          <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>
          <div class="form-group">
            <label class="label">Objekt</label>
            <input v-model="dpSearch" type="text" class="input text-sm" placeholder="Suchen…" @input="searchDps" />
            <div v-if="dpResults.length"
              class="mt-1 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden max-h-40 overflow-y-auto">
              <button v-for="dp in dpResults" :key="dp.id"
                @click="selectDp(dp)"
                class="w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200">
                {{ dp.name }}
                <span class="text-slate-500 ml-1">{{ dp.data_type }}</span>
              </button>
            </div>
            <div v-if="localData.datapoint_name" class="mt-1 text-xs text-teal-400">
              ✓ {{ localData.datapoint_name }}
            </div>
          </div>
        </div>

        <!-- Transformation -->
        <div v-show="activeTab === 'transform'" class="p-4 flex flex-col gap-3">
          <div class="section-label">Wert-Transformation</div>
          <div class="form-group">
            <label class="label">Formel <span class="text-slate-500 font-normal">— Variable: <code class="text-teal-400">x</code></span></label>
            <div class="flex gap-2">
              <select v-model="formulaPreset" @change="onPresetChange" class="input text-xs flex-1 min-w-0">
                <option value="">— Preset wählen —</option>
                <optgroup label="Multiplizieren">
                  <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup label="Dividieren">
                  <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
                </optgroup>
                <optgroup label="Benutzerdefiniert">
                  <option value="__custom__">Eigene Formel …</option>
                </optgroup>
              </select>
              <input
                v-model="localData.value_formula"
                @input="onFormulaInput"
                @change="emitUpdate"
                class="input text-xs font-mono w-28 shrink-0"
                placeholder="x * 100" />
            </div>
            <p class="text-xs text-slate-500 mt-1">
              Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
              und alle <code class="text-slate-400">math.*</code>-Funktionen.
              Leer = keine Transformation.
            </p>
          </div>

          <div class="section-label mt-1">Wertzuordnung</div>
          <div class="form-group">
            <label class="label">Wertzuordnung <span class="text-slate-500 font-normal text-xs">(optional)</span></label>
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
              <p class="text-xs text-slate-500 mt-0.5">JSON-Objekt — beliebig viele Einträge möglich.</p>
            </div>
            <p class="text-xs text-slate-500 mt-1">Wird nach der Formel angewendet.</p>
          </div>
        </div>

        <!-- Filter -->
        <div v-show="activeTab === 'filter'" class="p-4 flex flex-col gap-4">

          <div>
            <div class="section-label">Zeitlicher Filter</div>
            <label class="label mt-2">Min. Zeitabstand zwischen zwei {{ isWrite ? 'Schreibvorgängen' : 'Auslösungen' }}</label>
            <div class="flex gap-2 mt-1">
              <input
                v-model="localData.throttle_value"
                @change="emitUpdate"
                type="number" min="0"
                class="input text-sm flex-1"
                placeholder="z.B. 1" />
              <select v-model="localData.throttle_unit" @change="emitUpdate" class="input text-sm w-20 shrink-0">
                <option value="ms">ms</option>
                <option value="s">s</option>
                <option value="min">min</option>
                <option value="h">h</option>
              </select>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              {{ isWrite ? 'Schreibvorgänge' : 'Auslösungen' }} innerhalb des Intervalls werden verworfen.
            </p>
          </div>

          <div>
            <div class="section-label">Wert-Filter</div>

            <label class="flex items-start gap-2 mt-2 cursor-pointer">
              <input
                type="checkbox"
                :checked="boolVal(isWrite ? 'only_on_change' : 'trigger_on_change')"
                @change="e => { setBool(isWrite ? 'only_on_change' : 'trigger_on_change', e.target.checked); emitUpdate() }"
                class="mt-0.5 accent-teal-500" />
              <span class="text-xs text-slate-600 dark:text-slate-300 leading-snug">
                {{ isWrite
                  ? 'Nur schreiben wenn Wert sich geändert hat (kein Duplikat)'
                  : 'Nur auslösen wenn Wert sich geändert hat (kein Duplikat)' }}
              </span>
            </label>

            <label class="label mt-3">Nur {{ isWrite ? 'schreiben' : 'auslösen' }} bei Mindest-Abweichung</label>
            <div class="flex gap-2 mt-1">
              <div class="flex-1">
                <input
                  v-model="localData.min_delta"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  placeholder="z.B. 0.5" />
                <p class="text-xs text-slate-600 mt-0.5">Absolut</p>
              </div>
              <div v-if="!isWrite" class="flex-1">
                <input
                  v-model="localData.min_delta_pct"
                  @change="emitUpdate"
                  type="number" min="0" step="any"
                  class="input text-sm w-full"
                  placeholder="z.B. 2" />
                <p class="text-xs text-slate-600 mt-0.5">Relativ (%)</p>
              </div>
            </div>
            <p class="text-xs text-slate-500 mt-1">
              Leer = inaktiv. {{ !isWrite ? 'Beide aktiv = beide müssen erfüllt sein. ' : '' }}Nur für numerische Werte.
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
          <label class="label">Vorgefertigte Zeitpläne</label>
          <select :value="cronPresetValue" @change="onCronPresetChange" class="input text-sm">
            <option value="">— Preset wählen —</option>
            <optgroup label="Minuten-Intervalle">
              <option v-for="p in CRON_PRESETS_INTERVAL" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Stunden-Intervalle">
              <option v-for="p in CRON_PRESETS_HOURLY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Täglich">
              <option v-for="p in CRON_PRESETS_DAILY" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
            <optgroup label="Wöchentlich / Monatlich">
              <option v-for="p in CRON_PRESETS_OTHER" :key="p.expr" :value="p.expr">{{ p.label }}</option>
            </optgroup>
          </select>
          <p v-if="cronDescription" class="text-xs text-amber-400 mt-1">▶ {{ cronDescription }}</p>
        </div>

        <!-- Visual field builder -->
        <div class="form-group">
          <label class="label">Zeitplan anpassen</label>
          <div class="cron-grid mt-1">
            <div v-for="f in cronFields" :key="f.key" class="cron-field">
              <input
                v-model="f.value"
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
            <span><code>*</code> jeden</span>
            <span><code>*/5</code> alle 5</span>
            <span><code>1-5</code> Bereich</span>
            <span><code>1,3</code> Liste</span>
          </div>
        </div>

        <!-- Raw expression -->
        <div class="form-group">
          <label class="label">Ausdruck (direkt bearbeiten)</label>
          <input
            v-model="localData.cron"
            @change="onCronExprChange"
            class="input text-sm font-mono"
            placeholder="0 7 * * *"
          />
          <p class="text-xs text-slate-500 mt-1">
            Felder: <code class="text-slate-400">Minute · Stunde · Tag · Monat · Wochentag</code>
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
          <div class="section-label">Hauptformel</div>
          <label class="label">Formel <span class="text-slate-500 font-normal">— Variablen: <code class="text-teal-400">a</code>, <code class="text-teal-400">b</code></span></label>
          <input
            v-model="localData.formula"
            @change="emitUpdate"
            class="input text-sm font-mono"
            placeholder="a + b" />
          <p class="text-xs text-slate-500 mt-1">
            Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
            und alle <code class="text-slate-400">math.*</code>-Funktionen.
          </p>
        </div>

        <!-- Ausgangs-Transformation -->
        <div class="form-group">
          <div class="section-label">Ausgangs-Transformation</div>
          <label class="label">Formel <span class="text-slate-500 font-normal">— Variable: <code class="text-teal-400">x</code> (= Ergebnis)</span></label>
          <div class="flex gap-2">
            <select :value="outputFormulaPreset" @change="onOutputPresetChange" class="input text-xs flex-1 min-w-0">
              <option value="">— Preset wählen —</option>
              <optgroup label="Multiplizieren">
                <option v-for="p in MULTIPLY_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup label="Dividieren">
                <option v-for="p in DIVIDE_PRESETS" :key="p.f" :value="p.f">{{ p.label }}</option>
              </optgroup>
              <optgroup label="Benutzerdefiniert">
                <option value="__custom__">Eigene Formel …</option>
              </optgroup>
            </select>
            <input
              v-model="localData.output_formula"
              @change="emitUpdate"
              class="input text-xs font-mono w-28 shrink-0"
              placeholder="x * 100" />
          </div>
          <p class="text-xs text-slate-500 mt-1">
            Verfügbar: <code class="text-slate-400">abs round min max sqrt floor ceil</code>
            und alle <code class="text-slate-400">math.*</code>-Funktionen.
            Leer = keine Transformation.
          </p>
        </div>
      </div>
    </template>

    <!-- ── api_client: special rendering with conditional auth fields ──── -->
    <template v-else-if="isApiClientNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Standard request fields -->
        <div class="form-group">
          <label class="label">URL</label>
          <input v-model="localData.url" type="text" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-url" />
        </div>
        <div class="form-group">
          <label class="label">Methode</label>
          <select v-model="localData.method" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-method">
            <option v-for="m in ['GET','POST','PUT','PATCH','DELETE']" :key="m" :value="m">{{ m }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Request Content-Type</label>
          <select v-model="localData.content_type" class="input text-sm" @change="emitUpdate">
            <option v-for="ct in ['application/json','text/plain','application/x-www-form-urlencoded']" :key="ct" :value="ct">{{ ct }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Response Content-Typ</label>
          <select v-model="localData.response_type" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-response-type">
            <option value="application/json">application/json</option>
            <option value="text/plain">text/plain</option>
          </select>
        </div>
        <div class="form-group">
          <label class="label">Header (JSON-Objekt, optional)</label>
          <input v-model="localData.headers" type="text" class="input text-sm font-mono" @change="emitUpdate"
            placeholder='{"X-Api-Key": "abc"}' />
        </div>
        <div class="form-group">
          <label class="label">Timeout (s)</label>
          <input v-model="localData.timeout_s" type="number" class="input text-sm" @change="emitUpdate" />
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" v-model="localData.verify_ssl" @change="emitUpdate" class="accent-teal-500" />
          <span class="text-xs text-slate-600 dark:text-slate-300">SSL-Zertifikat prüfen</span>
        </label>

        <!-- Auth section -->
        <div class="section-label mt-1">Authentifizierung</div>
        <div class="form-group">
          <label class="label">Typ</label>
          <select v-model="localData.auth_type" class="input text-sm" @change="emitUpdate"
            data-testid="api-client-auth-type">
            <option value="none">Keine</option>
            <option value="basic">Basic Auth</option>
            <option value="digest">Digest Auth</option>
            <option value="bearer">Bearer Token</option>
          </select>
        </div>
        <template v-if="localData.auth_type === 'basic' || localData.auth_type === 'digest'">
          <div class="form-group" data-testid="api-client-auth-basic">
            <label class="label">Benutzername</label>
            <input v-model="localData.auth_username" type="text" class="input text-sm"
              autocomplete="off" @change="emitUpdate" />
          </div>
          <div class="form-group">
            <label class="label">Passwort</label>
            <input v-model="localData.auth_password" type="password" class="input text-sm"
              autocomplete="new-password" @change="emitUpdate" />
          </div>
        </template>
        <template v-if="localData.auth_type === 'bearer'">
          <div class="form-group" data-testid="api-client-auth-bearer">
            <label class="label">Bearer Token</label>
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
            <label class="label">Anzahl Eingänge</label>
            <input
              type="number" min="2" max="20"
              :value="concatCount"
              @change="onConcatCountChange"
              class="input text-sm"
              data-testid="concat-count"
            />
          </div>
          <div class="form-group flex-1">
            <label class="label">Trennzeichen</label>
            <input
              v-model="localData.separator"
              @change="emitUpdate"
              class="input text-sm font-mono"
              placeholder="leer = ohne"
              data-testid="concat-separator"
            />
          </div>
        </div>

        <!-- Per-slot static text -->
        <div class="section-label">Statischer Text pro Eingang</div>
        <p class="text-xs text-slate-500 -mt-2">
          Verbundene Eingänge überschreiben den statischen Text. Leere Felder ergeben einen leeren Teilstring.
        </p>
        <div class="flex flex-col gap-2">
          <div v-for="i in concatSlots" :key="i" class="flex items-center gap-2">
            <span class="text-xs text-slate-400 w-5 text-right shrink-0">{{ i }}</span>
            <input
              :value="localData[`text_${i}`] ?? ''"
              @input="localData[`text_${i}`] = $event.target.value"
              @change="emitUpdate"
              class="input text-sm flex-1"
              :placeholder="`Eingang ${i} …`"
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
          <div class="section-label">Empfangene Daten</div>
          <textarea
            :value="extractorPreview"
            readonly
            rows="7"
            class="input text-xs font-mono resize-y"
            style="background:#0f172a;color:#94a3b8"
            placeholder="Noch keine Daten empfangen. Graph ausführen um Daten zu laden."
            data-testid="extractor-preview"
          />
        </div>

        <!-- Path picker dropdown (only when we have parsed paths) -->
        <div v-if="extractorPaths.length" class="form-group">
          <label class="label">Pfad aus Daten wählen</label>
          <select @change="onExtractorPathSelect" class="input text-sm"
            data-testid="extractor-path-select">
            <option value="">— Pfad wählen —</option>
            <option v-for="p in extractorPaths" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>

        <!-- Manual path input -->
        <div class="form-group">
          <label class="label">{{ node.type === 'json_extractor' ? 'JSON-Pfad' : 'XPath' }}</label>
          <input
            v-model="localData[node.type === 'json_extractor' ? 'json_path' : 'xml_path']"
            @change="emitUpdate"
            class="input text-sm font-mono"
            :placeholder="node.type === 'json_extractor' ? 'z.B. data.temperature' : 'z.B. .//temperature'"
            data-testid="extractor-path-input"
          />
          <p v-if="extractorPreviewValue !== null" class="text-xs text-teal-400 mt-1">
            ↳ {{ String(extractorPreviewValue) }}
          </p>
        </div>
      </div>
    </template>

    <!-- ── substring_extractor ──────────────────────────────────────────── -->
    <template v-else-if="isSubstringExtractorNode">
      <div class="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ nodeDef?.description }}</p>

        <!-- Modus -->
        <div class="form-group">
          <label class="label">Modus</label>
          <select v-model="localData.mode" @change="emitUpdate" class="input text-sm" data-testid="substr-mode">
            <option value="rechts_von">Rechts von (nach)</option>
            <option value="links_von">Links von (vor)</option>
            <option value="zwischen">Zwischen</option>
            <option value="ausschneiden">Ausschneiden (Position + Länge)</option>
            <option value="regex">Regulärer Ausdruck (RegEx)</option>
          </select>
        </div>

        <!-- Fields: links_von / rechts_von -->
        <template v-if="localData.mode === 'links_von' || localData.mode === 'rechts_von'">
          <div class="form-group">
            <label class="label">Suchbegriff</label>
            <input v-model="localData.search" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="z.B. :" data-testid="substr-search" />
          </div>
          <div class="form-group">
            <label class="label">Vorkommen</label>
            <select v-model="localData.occurrence" @change="emitUpdate" class="input text-sm">
              <option value="first">Erstes</option>
              <option value="last">Letztes</option>
            </select>
          </div>
        </template>

        <!-- Fields: zwischen -->
        <template v-else-if="localData.mode === 'zwischen'">
          <div class="form-group">
            <label class="label">Start-Markierung</label>
            <input v-model="localData.start_marker" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="z.B. [" data-testid="substr-start-marker" />
          </div>
          <div class="form-group">
            <label class="label">End-Markierung</label>
            <input v-model="localData.end_marker" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="z.B. ]" data-testid="substr-end-marker" />
          </div>
        </template>

        <!-- Fields: ausschneiden -->
        <template v-else-if="localData.mode === 'ausschneiden'">
          <div class="form-group">
            <label class="label">Startposition (0-basiert)</label>
            <input v-model.number="localData.start" @change="emitUpdate" type="number" min="0"
              class="input text-sm" data-testid="substr-start" />
          </div>
          <div class="form-group">
            <label class="label">Länge (–1 = bis Ende)</label>
            <input v-model.number="localData.length" @change="emitUpdate" type="number" min="-1"
              class="input text-sm" data-testid="substr-length" />
          </div>
        </template>

        <!-- Fields: regex -->
        <template v-else-if="localData.mode === 'regex'">
          <div class="form-group">
            <label class="label">
              RegEx-Muster
              <a :href="substringRegex101Url" target="_blank" rel="noopener"
                class="ml-2 text-teal-400 underline text-xs" title="In regex101.com öffnen">
                ↗ regex101.com
              </a>
            </label>
            <input v-model="localData.pattern" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="z.B. (\d+\.\d+)" data-testid="substr-pattern" />
          </div>
          <div class="form-group">
            <label class="label">Flags (z.B. i, m, s)</label>
            <input v-model="localData.flags" @change="emitUpdate" class="input text-sm font-mono"
              placeholder="i" data-testid="substr-flags" />
          </div>
          <div class="form-group">
            <label class="label">Capture-Gruppe (0 = gesamter Treffer)</label>
            <input v-model.number="localData.group" @change="emitUpdate" type="number" min="0"
              class="input text-sm" data-testid="substr-group" />
          </div>
        </template>

        <!-- Test: empfangene Daten oder manuelle Eingabe -->
        <div class="form-group">
          <div class="section-label">Test-Eingabe</div>
          <textarea
            v-model="substrTestInput"
            rows="5"
            class="input text-xs font-mono resize-y"
            style="background:#0f172a;color:#94a3b8"
            :placeholder="extractorPreview || 'Teststring eingeben oder Graph ausführen…'"
            data-testid="substr-test-input"
          />
          <p v-if="substrTestResult !== null" class="text-xs text-teal-400 mt-1" data-testid="substr-test-result">
            ↳ {{ String(substrTestResult) }}
          </p>
          <p v-else-if="substrTestInput || extractorPreview" class="text-xs text-slate-500 mt-1">
            ↳ kein Treffer
          </p>
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
import { dpApi, searchApi } from '@/api/client'

const props = defineProps({
  node:        { type: Object, default: null },
  nodeTypes:   { type: Array,  default: () => [] },
  nodeOutputs: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update', 'close'])

// ── State ──────────────────────────────────────────────────────────────────
const localData          = ref({})
const dpSearch           = ref('')
const dpResults          = ref([])
const activeTab          = ref('connection')
const valueMapPreset     = ref('')
const valueMapCustom     = ref('')
const valueMapCustomError = ref('')

// ── Value Map Presets ──────────────────────────────────────────────────────
const VALUE_MAP_PRESETS = [
  { key: '',            label: '— keine Wertzuordnung —',            map: null },
  { key: 'num_invert',  label: '0 ↔ 1 (numerisch invertieren)',       map: { '0': '1', '1': '0' } },
  { key: 'bool_onoff',  label: 'true/false → on/off',                 map: { 'true': 'on', 'false': 'off' } },
  { key: 'onoff_bool',  label: 'on/off → true/false',                 map: { 'on': 'true', 'off': 'false' } },
  { key: 'num_onoff',   label: '0/1 → off/on',                        map: { '0': 'off', '1': 'on' } },
  { key: 'onoff_num',   label: 'off/on → 0/1',                        map: { 'off': '0', 'on': '1' } },
  { key: 'custom',      label: 'Benutzerdefiniert (JSON) …',           map: null },
]

// ── Formula Presets ────────────────────────────────────────────────────────
const MULTIPLY_PRESETS = [
  { f: 'x * 86400',  label: '× 86.400 (Tage → Sekunden)'    },
  { f: 'x * 3600',   label: '× 3.600 (Stunden → Sekunden)'  },
  { f: 'x * 1440',   label: '× 1.440 (Tage → Minuten)'      },
  { f: 'x * 1000',   label: '× 1.000'                        },
  { f: 'x * 100',    label: '× 100'                          },
  { f: 'x * 60',     label: '× 60 (Minuten → Sekunden)'      },
  { f: 'x * 10',     label: '× 10'                           },
]
const DIVIDE_PRESETS = [
  { f: 'round(x / 10, 1)',    label: '÷ 10 (Festkomma)'              },
  { f: 'x / 60',              label: '÷ 60 (Sekunden → Minuten)'     },
  { f: 'round(x / 100, 2)',   label: '÷ 100 (Festkomma)'             },
  { f: 'round(x / 1000, 3)',  label: '÷ 1.000 (Festkomma)'           },
  { f: 'x / 1440',            label: '÷ 1.440 (Minuten → Tage)'      },
  { f: 'x / 3600',            label: '÷ 3.600 (Sekunden → Stunden)'  },
  { f: 'x / 86400',           label: '÷ 86.400 (Sekunden → Tage)'    },
]
const ALL_PRESETS = [...MULTIPLY_PRESETS, ...DIVIDE_PRESETS]

// ── Cron Presets ───────────────────────────────────────────────────────────
const CRON_PRESETS_INTERVAL = [
  { expr: '* * * * *',      label: 'Jede Minute'     },
  { expr: '*/5 * * * *',    label: 'Alle 5 Minuten'  },
  { expr: '*/10 * * * *',   label: 'Alle 10 Minuten' },
  { expr: '*/15 * * * *',   label: 'Alle 15 Minuten' },
  { expr: '*/30 * * * *',   label: 'Alle 30 Minuten' },
]
const CRON_PRESETS_HOURLY = [
  { expr: '0 * * * *',     label: 'Jede Stunde'     },
  { expr: '0 */2 * * *',   label: 'Alle 2 Stunden'  },
  { expr: '0 */4 * * *',   label: 'Alle 4 Stunden'  },
  { expr: '0 */6 * * *',   label: 'Alle 6 Stunden'  },
  { expr: '0 */12 * * *',  label: 'Alle 12 Stunden' },
]
const CRON_PRESETS_DAILY = [
  { expr: '0 0 * * *',       label: 'Täglich um 00:00 (Mitternacht)' },
  { expr: '0 6 * * *',       label: 'Täglich um 06:00'               },
  { expr: '0 7 * * *',       label: 'Täglich um 07:00'               },
  { expr: '0 8 * * *',       label: 'Täglich um 08:00'               },
  { expr: '0 9 * * *',       label: 'Täglich um 09:00'               },
  { expr: '0 12 * * *',      label: 'Täglich um 12:00'               },
  { expr: '0 17 * * *',      label: 'Täglich um 17:00'               },
  { expr: '0 18 * * *',      label: 'Täglich um 18:00'               },
  { expr: '0 22 * * *',      label: 'Täglich um 22:00'               },
  { expr: '0 6,18 * * *',    label: 'Täglich um 06:00 und 18:00'     },
  { expr: '0 8,12,18 * * *', label: 'Täglich um 08:00, 12:00, 18:00' },
  { expr: '0 6 * * 1-5',     label: 'Werktags (Mo–Fr) um 06:00'      },
  { expr: '0 7 * * 1-5',     label: 'Werktags (Mo–Fr) um 07:00'      },
  { expr: '0 9 * * 1-5',     label: 'Werktags (Mo–Fr) um 09:00'      },
  { expr: '0 8-17 * * 1-5',  label: 'Werktags, stündlich 08–17 Uhr'  },
]
const CRON_PRESETS_OTHER = [
  { expr: '0 9 * * 1',    label: 'Jeden Montag um 09:00'           },
  { expr: '0 0 * * 0',    label: 'Jeden Sonntag um Mitternacht'    },
  { expr: '0 0 * * 1',    label: 'Jeden Montag um Mitternacht'     },
  { expr: '0 0 1 * *',    label: 'Ersten Tag des Monats (00:00)'   },
  { expr: '0 0 15 * *',   label: '15. des Monats (00:00)'          },
  { expr: '0 0 1 1 *',    label: 'Einmal jährlich (1. Januar)'     },
  { expr: '0 0 1 4 *',    label: 'Einmal jährlich (1. April)'      },
  { expr: '0 0 1 10 *',   label: 'Einmal jährlich (1. Oktober)'    },
]
const ALL_CRON_PRESETS = [
  ...CRON_PRESETS_INTERVAL,
  ...CRON_PRESETS_HOURLY,
  ...CRON_PRESETS_DAILY,
  ...CRON_PRESETS_OTHER,
]

// ── Cron field state ───────────────────────────────────────────────────────
const cronFields = ref([
  { key: 'min',     value: '0', label: 'Min', placeholder: '0', hint: '0–59'        },
  { key: 'hour',    value: '7', label: 'Std', placeholder: '*', hint: '0–23'        },
  { key: 'day',     value: '*', label: 'Tag', placeholder: '*', hint: '1–31'        },
  { key: 'month',   value: '*', label: 'Mon', placeholder: '*', hint: '1–12'        },
  { key: 'weekday', value: '*', label: 'WT',  placeholder: '*', hint: '0–6 (0=So)'  },
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
  return ALL_CRON_PRESETS.find(p => p.expr === expr)?.expr ?? ''
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
  return ALL_CRON_PRESETS.find(p => p.expr === expr)?.label ?? ''
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

// ── string_concat: dynamic slot count ─────────────────────────────────────
const concatCount = computed(() => Math.max(2, Math.min(20, Number(localData.value.count) || 2)))
const concatSlots = computed(() => Array.from({ length: concatCount.value }, (_, i) => i + 1))

function onConcatCountChange(e) {
  const v = Math.max(2, Math.min(20, parseInt(e.target.value) || 2))
  localData.value.count = v
  emitUpdate()
}

// ── Extractor: preview + path helpers ─────────────────────────────────────
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

// Collect unique .//tagname XPath expressions from XML
function _collectXmlPaths(element, seen = new Set()) {
  const xp = `.//${element.tagName}`
  if (!seen.has(xp)) seen.add(xp)
  for (const child of Array.from(element.children)) {
    _collectXmlPaths(child, seen)
  }
  return [...seen]
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
      if (doc.querySelector('parsererror')) return null
      const el = doc.evaluate(path, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
      return el ? el.textContent?.trim() ?? null : null
    } catch { return null }
  }
})

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
    return ALL_PRESETS.find(p => p.f === f)?.f ?? '__custom__'
  },
  set(v) { void v },
})

const outputFormulaPreset = computed(() => {
  const f = localData.value.output_formula || ''
  if (!f) return ''
  return ALL_PRESETS.find(p => p.f === f)?.f ?? '__custom__'
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
  { id: 'connection', label: 'Verbindung',     dot: false              },
  { id: 'transform',  label: 'Transformation', dot: hasTransform.value },
  { id: 'filter',     label: 'Filter',         dot: hasFilter.value    },
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
        const preset = VALUE_MAP_PRESETS.find(p => p.map && JSON.stringify(p.map) === mapStr)
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
  const preset = VALUE_MAP_PRESETS.find(p => p.key === valueMapPreset.value)
  localData.value.value_map = preset?.map ?? null
  emitUpdate()
}

function onExtractorPathSelect(e) {
  const path = e.target.value
  if (!path || !props.node) return
  const key = props.node.type === 'json_extractor' ? 'json_path' : 'xml_path'
  localData.value[key] = path
  emitUpdate()
}

function onValueMapCustomInput(e) {
  const val = e.target.value.trim()
  if (!val) { valueMapCustomError.value = ''; return }
  try {
    JSON.parse(val)
    valueMapCustomError.value = ''
  } catch (err) {
    valueMapCustomError.value = `Ungültiges JSON: ${err.message}`
  }
}

function onValueMapCustomChange() {
  valueMapCustomError.value = ''
  try {
    localData.value.value_map = JSON.parse(valueMapCustom.value)
  } catch (e) {
    valueMapCustomError.value = `Ungültiges JSON: ${e.message}`
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
</style>
