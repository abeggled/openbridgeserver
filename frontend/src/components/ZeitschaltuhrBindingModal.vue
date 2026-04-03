<script setup lang="ts">
/**
 * ZeitschaltuhrBindingModal — Inline-Editor für eine Zeitschaltuhr-Verknüpfung.
 *
 * Lädt die aktuelle Binding-Konfiguration via API und speichert Änderungen
 * per PATCH zurück. Nur sichtbar wenn der Benutzer ein gültiges JWT besitzt.
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { datapoints as dpApi } from '@/api/client'

const props = defineProps<{
  datapointId: string
  bindingId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', enabled: boolean): void
}>()

// ── State ─────────────────────────────────────────────────────────────────────

const loading  = ref(true)
const saving   = ref(false)
const errorMsg = ref('')

const bindingEnabled = ref(true)

interface ZstCfg {
  timer_type:         string
  meta_type:          string
  weekdays:           number[]
  months:             number[]
  day_of_month:       number
  time_ref:           string
  hour:               number
  minute:             number
  offset_minutes:     number
  solar_altitude_deg: number
  sun_direction:      string
  every_hour:         boolean
  every_minute:       boolean
  holiday_mode:       string
  vacation_mode:      string
  value:              string
}

const DEFAULT_CFG: ZstCfg = {
  timer_type:         'daily',
  meta_type:          'none',
  weekdays:           [0, 1, 2, 3, 4, 5, 6],
  months:             [],
  day_of_month:       0,
  time_ref:           'absolute',
  hour:               0,
  minute:             0,
  offset_minutes:     0,
  solar_altitude_deg: 0,
  sun_direction:      'rising',
  every_hour:         false,
  every_minute:       false,
  holiday_mode:       'ignore',
  vacation_mode:      'ignore',
  value:              '1',
}

const cfg = reactive<ZstCfg>({ ...DEFAULT_CFG })

// ── Load ──────────────────────────────────────────────────────────────────────

onMounted(loadBinding)

async function loadBinding() {
  loading.value = true
  errorMsg.value = ''
  try {
    const bindings = await dpApi.listBindings(props.datapointId)
    const b = bindings.find((b) => b.id === props.bindingId)
    if (!b) { errorMsg.value = 'Verknüpfung nicht gefunden.'; return }
    bindingEnabled.value = b.enabled
    Object.assign(cfg, DEFAULT_CFG, b.config)
    // Sicherstellen dass weekdays/months Arrays sind
    if (!Array.isArray(cfg.weekdays)) cfg.weekdays = [0, 1, 2, 3, 4, 5, 6]
    if (!Array.isArray(cfg.months))   cfg.months   = []
  } catch {
    errorMsg.value = 'Verknüpfung konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function save() {
  saving.value = true
  errorMsg.value = ''
  try {
    await dpApi.updateBinding(props.datapointId, props.bindingId, {
      config:  { ...cfg },
      enabled: bindingEnabled.value,
    })
    emit('saved', bindingEnabled.value)
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : 'Fehler beim Speichern.'
  } finally {
    saving.value = false
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
const MONTH_LABELS   = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez']

function toggleWeekday(idx: number) {
  const i = cfg.weekdays.indexOf(idx)
  if (i >= 0) cfg.weekdays.splice(i, 1)
  else        cfg.weekdays.push(idx)
  cfg.weekdays.sort((a, b) => a - b)
}

function toggleMonth(m: number) {
  const i = cfg.months.indexOf(m)
  if (i >= 0) cfg.months.splice(i, 1)
  else        cfg.months.push(m)
  cfg.months.sort((a, b) => a - b)
}

const showTimeRef  = computed(() => cfg.timer_type !== 'meta')
const showAbsolute = computed(() => showTimeRef.value && cfg.time_ref === 'absolute')
const showOffset   = computed(() => showTimeRef.value && cfg.time_ref !== 'absolute' && cfg.time_ref !== 'solar_altitude')
const showSolar    = computed(() => showTimeRef.value && cfg.time_ref === 'solar_altitude')

// ── Input class helper (dark-mode Tailwind, matches existing Config.vue) ──────
const iCls = 'w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50'
const lCls = 'block text-xs text-gray-400 mb-1'
const hCls = 'text-xs text-gray-500 mt-0.5'
</script>

<template>
  <!-- Overlay -->
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')"
  >
    <!-- Dialog -->
    <div class="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col">

      <!-- Header -->
      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-700 flex-shrink-0">
        <h2 class="text-sm font-semibold text-gray-100">🕐 Verknüpfung bearbeiten</h2>
        <button
          class="text-gray-400 hover:text-gray-200 text-lg leading-none"
          @click="emit('close')"
        >×</button>
      </div>

      <!-- Body -->
      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-4">

        <div v-if="loading" class="text-sm text-gray-400 text-center py-6">Lade …</div>

        <template v-else-if="!errorMsg">

          <!-- Aktiviert -->
          <div class="flex items-center gap-2">
            <input
              id="zt-enabled"
              type="checkbox"
              v-model="bindingEnabled"
              class="w-4 h-4 rounded accent-blue-500"
            />
            <label for="zt-enabled" class="text-sm text-gray-200">Verknüpfung aktiviert</label>
          </div>

          <hr class="border-gray-700" />

          <!-- Typ -->
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :class="lCls">Typ</label>
              <select v-model="cfg.timer_type" :class="iCls">
                <option value="daily">Tagesschaltuhr (täglich/wöchentlich)</option>
                <option value="annual">Jahresschaltuhr (monatlich/jährlich)</option>
                <option value="meta">Metadaten (Feiertag-/Ferienstatus)</option>
              </select>
            </div>
            <div v-if="cfg.timer_type === 'meta'">
              <label :class="lCls">Metadaten-Typ</label>
              <select v-model="cfg.meta_type" :class="iCls">
                <optgroup label="Feiertage">
                  <option value="holiday_today">Feiertag heute (bool)</option>
                  <option value="holiday_tomorrow">Feiertag morgen (bool)</option>
                  <option value="holiday_name_today">Feiertagsname heute (string)</option>
                  <option value="holiday_name_tomorrow">Feiertagsname morgen (string)</option>
                </optgroup>
                <optgroup label="Ferienperioden">
                  <option value="vacation_1">Ferienperiode 1 aktiv (bool)</option>
                  <option value="vacation_2">Ferienperiode 2 aktiv (bool)</option>
                  <option value="vacation_3">Ferienperiode 3 aktiv (bool)</option>
                  <option value="vacation_4">Ferienperiode 4 aktiv (bool)</option>
                  <option value="vacation_5">Ferienperiode 5 aktiv (bool)</option>
                  <option value="vacation_6">Ferienperiode 6 aktiv (bool)</option>
                </optgroup>
              </select>
              <p :class="hCls">Wird täglich um Mitternacht automatisch aktualisiert.</p>
            </div>
          </div>

          <template v-if="cfg.timer_type !== 'meta'">

            <!-- Wochentage -->
            <div>
              <label :class="lCls">Wochentage</label>
              <div class="flex gap-1.5 flex-wrap">
                <button
                  v-for="(lbl, idx) in WEEKDAY_LABELS"
                  :key="idx"
                  type="button"
                  class="px-2.5 py-1 text-xs font-medium rounded border transition-colors"
                  :class="cfg.weekdays.includes(idx)
                    ? 'bg-blue-600 border-blue-600 text-white'
                    : 'bg-gray-800 border-gray-600 text-gray-300 hover:border-blue-500'"
                  @click="toggleWeekday(idx)"
                >{{ lbl }}</button>
                <button type="button" class="ml-2 text-xs text-gray-500 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4,5,6]">Alle</button>
                <button type="button" class="text-xs text-gray-500 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4]">Mo–Fr</button>
                <button type="button" class="text-xs text-gray-500 hover:text-blue-400" @click="cfg.weekdays = [5,6]">Sa+So</button>
              </div>
            </div>

            <!-- Monate + Tag (nur Jahresschaltuhr) -->
            <template v-if="cfg.timer_type === 'annual'">
              <div>
                <label :class="lCls">Monate <span class="text-gray-600">(leer = alle)</span></label>
                <div class="flex gap-1 flex-wrap">
                  <button
                    v-for="(lbl, idx) in MONTH_LABELS"
                    :key="idx+1"
                    type="button"
                    class="px-2 py-1 text-xs font-medium rounded border transition-colors"
                    :class="cfg.months.includes(idx+1)
                      ? 'bg-blue-600 border-blue-600 text-white'
                      : 'bg-gray-800 border-gray-600 text-gray-300 hover:border-blue-500'"
                    @click="toggleMonth(idx + 1)"
                  >{{ lbl }}</button>
                  <button type="button" class="ml-1 text-xs text-gray-500 hover:text-blue-400" @click="cfg.months = []">Alle</button>
                </div>
              </div>
              <div class="w-36">
                <label :class="lCls">Tag im Monat <span class="text-gray-600">(0 = alle)</span></label>
                <input v-model.number="cfg.day_of_month" type="number" min="0" max="31" :class="iCls" />
              </div>
            </template>

            <!-- Zeitreferenz -->
            <hr class="border-gray-700" />
            <div class="w-56">
              <label :class="lCls">Zeitreferenz</label>
              <select v-model="cfg.time_ref" :class="iCls">
                <option value="absolute">Absolute Zeit</option>
                <option value="sunrise">Sonnenaufgang + Offset</option>
                <option value="sunset">Sonnenuntergang + Offset</option>
                <option value="solar_noon">Sonnenmittag + Offset</option>
                <option value="solar_altitude">Sonnenhöhenwinkel</option>
              </select>
            </div>

            <!-- Absolute Zeit -->
            <div v-if="showAbsolute" class="grid grid-cols-2 gap-3">
              <div>
                <label :class="lCls">Stunde</label>
                <input v-model.number="cfg.hour" type="number" min="0" max="23" :class="iCls" />
              </div>
              <div>
                <label :class="lCls">Minute</label>
                <input v-model.number="cfg.minute" type="number" min="0" max="59" :class="iCls" />
              </div>
            </div>

            <!-- Offset (Sonnenauf/-untergang etc.) -->
            <div v-if="showOffset" class="w-44">
              <label :class="lCls">Offset in Minuten</label>
              <input v-model.number="cfg.offset_minutes" type="number" :class="iCls" placeholder="0" />
              <p :class="hCls">Positiv = danach, negativ = davor</p>
            </div>

            <!-- Sonnenhöhenwinkel -->
            <div v-if="showSolar" class="grid grid-cols-2 gap-3">
              <div>
                <label :class="lCls">Sonnenhöhenwinkel (°)</label>
                <input v-model.number="cfg.solar_altitude_deg" type="number" min="-18" max="90" step="0.5" :class="iCls" />
                <p :class="hCls">−18° = naut. Dämmerung · 0° = Horizont</p>
              </div>
              <div>
                <label :class="lCls">Sonnenrichtung</label>
                <select v-model="cfg.sun_direction" :class="iCls">
                  <option value="rising">Aufsteigend (morgens)</option>
                  <option value="setting">Absteigend (abends)</option>
                </select>
              </div>
            </div>

            <!-- Takt -->
            <hr class="border-gray-700" />
            <div class="grid grid-cols-2 gap-3">
              <div class="flex items-center gap-2">
                <input id="zt-every-minute" type="checkbox" v-model="cfg.every_minute" class="w-4 h-4 rounded accent-blue-500" />
                <div>
                  <label for="zt-every-minute" class="text-xs text-gray-300">Jede Minute schalten</label>
                  <p :class="hCls">Wochentag-Filter gilt weiterhin</p>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <input id="zt-every-hour" type="checkbox" v-model="cfg.every_hour" class="w-4 h-4 rounded accent-blue-500" />
                <div>
                  <label for="zt-every-hour" class="text-xs text-gray-300">Jede Stunde schalten</label>
                  <p :class="hCls">Zur eingestellten Minute</p>
                </div>
              </div>
            </div>
            <div v-if="cfg.every_hour && !cfg.every_minute" class="w-32">
              <label :class="lCls">Zur Minute</label>
              <input v-model.number="cfg.minute" type="number" min="0" max="59" :class="iCls" />
            </div>

            <!-- Feiertag / Ferien -->
            <hr class="border-gray-700" />
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label :class="lCls">Feiertagsbehandlung</label>
                <select v-model="cfg.holiday_mode" :class="iCls">
                  <option value="ignore">Ignorieren (wie Normaltage)</option>
                  <option value="skip">Nicht schalten an Feiertagen</option>
                  <option value="only">Nur an Feiertagen schalten</option>
                  <option value="as_sunday">Feiertage wie Sonntag</option>
                </select>
              </div>
              <div>
                <label :class="lCls">Ferienbehandlung</label>
                <select v-model="cfg.vacation_mode" :class="iCls">
                  <option value="ignore">Ignorieren (wie Normaltage)</option>
                  <option value="skip">Nicht schalten in Ferien</option>
                  <option value="only">Nur in Ferien schalten</option>
                  <option value="as_sunday">Ferientage wie Sonntag</option>
                </select>
              </div>
            </div>

            <!-- Ausgabewert -->
            <hr class="border-gray-700" />
            <div class="w-40">
              <label :class="lCls">Schalt-Wert</label>
              <input v-model="cfg.value" type="text" :class="iCls" placeholder="1" />
              <p :class="hCls">z.B. 1 / 0 / true / false</p>
            </div>

          </template><!-- /timer_type !== meta -->

        </template>

        <p v-if="errorMsg" class="text-sm text-red-400">{{ errorMsg }}</p>

      </div><!-- /body -->

      <!-- Footer -->
      <div class="flex justify-end gap-2 px-5 py-3 border-t border-gray-700 flex-shrink-0">
        <button
          class="px-3 py-1.5 text-sm text-gray-400 hover:text-gray-200 rounded"
          @click="emit('close')"
        >Abbrechen</button>
        <button
          class="px-4 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white rounded disabled:opacity-50"
          :disabled="saving || loading || !!errorMsg"
          @click="save"
        >
          {{ saving ? 'Speichern …' : 'Speichern' }}
        </button>
      </div>

    </div>
  </div>
</template>
