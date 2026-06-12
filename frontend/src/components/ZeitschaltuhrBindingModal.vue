<script setup lang="ts">
/**
 * ZeitschaltuhrBindingModal — Inline-Editor für eine Zeitschaltuhr-Verknüpfung.
 */
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { datapoints as dpApi, adapters as adapterApi } from '@/api/client'
import type { HolidayEntry } from '@/api/client'

const props = defineProps<{
  datapointId: string
  instanceId: string
  bindingId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', enabled: boolean): void
}>()

const { t } = useI18n()

// ── State ─────────────────────────────────────────────────────────────────────

const loading  = ref(true)
const saving   = ref(false)
const errorMsg = ref('')

const bindingEnabled = ref(true)
const instanceId     = ref('')

interface ZstCfg {
  timer_type:          string
  meta_type:           string
  weekdays:            number[]
  months:              number[]
  day_of_month:        number
  time_ref:            string
  hour:                number
  minute:              number
  offset_minutes:      number
  solar_altitude_deg:  number
  sun_direction:       string
  every_hour:          boolean
  every_minute:        boolean
  holiday_mode:        string
  vacation_mode:       string
  selected_holidays:   string[]
  date_window_enabled: boolean
  date_window_from:    string
  date_window_to:      string
  value:               string
}

const DEFAULT_CFG: ZstCfg = {
  timer_type:          'daily',
  meta_type:           'none',
  weekdays:            [0, 1, 2, 3, 4, 5, 6],
  months:              [],
  day_of_month:        0,
  time_ref:            'absolute',
  hour:                0,
  minute:              0,
  offset_minutes:      0,
  solar_altitude_deg:  0,
  sun_direction:       'rising',
  every_hour:          false,
  every_minute:        false,
  holiday_mode:        'ignore',
  vacation_mode:       'ignore',
  selected_holidays:   [],
  date_window_enabled: false,
  date_window_from:    '',
  date_window_to:      '',
  value:               '1',
}

const cfg = reactive<ZstCfg>({ ...DEFAULT_CFG })

// ── Holiday list ──────────────────────────────────────────────────────────────

const holidays        = ref<HolidayEntry[]>([])
const holidaysLoading = ref(false)
const holidaysError   = ref('')

async function loadHolidays() {
  if (!instanceId.value) return
  holidaysLoading.value = true
  holidaysError.value   = ''
  try {
    holidays.value = await adapterApi.zsuHolidays(instanceId.value)
  } catch {
    holidaysError.value = t('zst.holidayLoadError')
  } finally {
    holidaysLoading.value = false
  }
}

async function loadHolidaysIfNeeded() {
  if (holidays.value.length === 0 && instanceId.value) await loadHolidays()
}

// ── Date window endpoint state ────────────────────────────────────────────────

interface WinEp {
  type:   'fixed' | 'easter' | 'advent' | 'holiday_name'
  month:  number    // 1-12
  day:    number    // 1-31
  sign:   '+' | '-'
  offset: number
  name:   string
}

const WINDOW_MONTHS = computed(() => [
  { v: 1,  l: t('zst.winMonthJan') }, { v: 2,  l: t('zst.winMonthFeb') }, { v: 3,  l: t('zst.winMonthMar') },
  { v: 4,  l: t('zst.winMonthApr') }, { v: 5,  l: t('zst.winMonthMai') }, { v: 6,  l: t('zst.winMonthJun') },
  { v: 7,  l: t('zst.winMonthJul') }, { v: 8,  l: t('zst.winMonthAug') }, { v: 9,  l: t('zst.winMonthSep') },
  { v: 10, l: t('zst.winMonthOkt') }, { v: 11, l: t('zst.winMonthNov') }, { v: 12, l: t('zst.winMonthDez') },
])

const winFrom = reactive<WinEp>({ type: 'fixed', month: 1,  day: 1,  sign: '+', offset: 0, name: '' })
const winTo   = reactive<WinEp>({ type: 'fixed', month: 12, day: 31, sign: '+', offset: 0, name: '' })

function buildWinExpr(ep: WinEp): string {
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

function parseWinExprInto(expr: string, ep: WinEp) {
  if (!expr) return
  const exprUp = expr.toUpperCase()

  const fixedM = expr.match(/^(\d{1,2})-(\d{1,2})$/)
  if (fixedM) {
    ep.type  = 'fixed'
    ep.month = parseInt(fixedM[1], 10)
    ep.day   = parseInt(fixedM[2], 10)
    return
  }
  const easterM = exprUp.match(/^EASTER([+-])?(\d+)?$/)
  if (easterM) {
    ep.type   = 'easter'
    ep.sign   = (easterM[1] ?? '+') as '+' | '-'
    ep.offset = parseInt(easterM[2] ?? '0', 10)
    return
  }
  const adventM = exprUp.match(/^ADVENT([+-])?(\d+)?$/)
  if (adventM) {
    ep.type   = 'advent'
    ep.sign   = (adventM[1] ?? '+') as '+' | '-'
    ep.offset = parseInt(adventM[2] ?? '0', 10)
    return
  }
  if (exprUp.startsWith('HOLIDAY:')) {
    const remainder  = expr.slice(8)
    const offsetM    = remainder.match(/([+-])(\d+)$/)
    ep.type = 'holiday_name'
    if (offsetM) {
      ep.name   = remainder.slice(0, offsetM.index).trim()
      ep.sign   = offsetM[1] as '+' | '-'
      ep.offset = parseInt(offsetM[2], 10)
    } else {
      ep.name   = remainder.trim()
      ep.sign   = '+'
      ep.offset = 0
    }
  }
}

function describeWinEp(ep: WinEp): string {
  switch (ep.type) {
    case 'fixed': {
      const mon = WINDOW_MONTHS.value.find(m => m.v === ep.month)?.l ?? String(ep.month)
      return `${ep.day}. ${mon}`
    }
    case 'easter':
      if (ep.offset === 0) return t('zst.easterSunday')
      return t('zst.easterOffset', { sign: ep.sign, offset: ep.offset })
    case 'advent': {
      const presets: Record<string, string> = {
        '0': t('zst.advent1'), '7': t('zst.advent2'), '14': t('zst.advent3'),
        '21': t('zst.advent4'), '24': t('zst.heiligabend'),
      }
      if (ep.sign === '+' && presets[String(ep.offset)]) return presets[String(ep.offset)]
      if (ep.offset === 0) return t('zst.advent1')
      return t('zst.adventOffset', { sign: ep.sign, offset: ep.offset })
    }
    case 'holiday_name':
      if (!ep.name) return '—'
      return ep.offset === 0 ? ep.name : `${ep.name} ${ep.sign}${ep.offset} ${t('zst.days')}`
    default:
      return '—'
  }
}

async function onWinTypeChange(ep: WinEp) {
  if (ep.type === 'holiday_name') await loadHolidaysIfNeeded()
}

// ── Load ──────────────────────────────────────────────────────────────────────

onMounted(loadBinding)

async function loadBinding() {
  loading.value = true
  errorMsg.value = ''
  try {
    const bindings = await dpApi.listBindings(props.datapointId)
    const b = bindings.find((b) => b.id === props.bindingId && b.adapter_instance_id === props.instanceId)
    if (!b) { errorMsg.value = t('zst.bindingNotFound'); return }
    bindingEnabled.value = b.enabled
    instanceId.value     = b.adapter_instance_id ?? ''
    Object.assign(cfg, DEFAULT_CFG, b.config)
    if (!Array.isArray(cfg.weekdays))          cfg.weekdays          = [0, 1, 2, 3, 4, 5, 6]
    if (!Array.isArray(cfg.months))            cfg.months            = []
    if (!Array.isArray(cfg.selected_holidays)) cfg.selected_holidays = []
    // Parse window expressions into UI state
    if (cfg.date_window_from) parseWinExprInto(cfg.date_window_from, winFrom)
    if (cfg.date_window_to)   parseWinExprInto(cfg.date_window_to,   winTo)
    // Pre-load holidays if needed
    if (cfg.timer_type === 'holiday' || winFrom.type === 'holiday_name' || winTo.type === 'holiday_name') {
      await loadHolidaysIfNeeded()
    }
  } catch {
    errorMsg.value = t('zst.bindingLoadError')
  } finally {
    loading.value = false
  }
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function save() {
  saving.value = true
  errorMsg.value = ''
  try {
    const configToSave = { ...cfg }
    if (cfg.date_window_enabled) {
      configToSave.date_window_from = buildWinExpr(winFrom)
      configToSave.date_window_to   = buildWinExpr(winTo)
    } else {
      configToSave.date_window_from = ''
      configToSave.date_window_to   = ''
    }
    await dpApi.updateBinding(props.datapointId, props.bindingId, {
      config:  configToSave,
      enabled: bindingEnabled.value,
    })
    emit('saved', bindingEnabled.value)
  } catch (e: unknown) {
    errorMsg.value = e instanceof Error ? e.message : t('zst.bindingSaveError')
  } finally {
    saving.value = false
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = computed(() => [
  t('zst.weekdayMo'), t('zst.weekdayDi'), t('zst.weekdayMi'), t('zst.weekdayDo'),
  t('zst.weekdayFr'), t('zst.weekdaySa'), t('zst.weekdaySo'),
])
const MONTH_LABELS = computed(() => [
  t('zst.monthJan'), t('zst.monthFeb'), t('zst.monthMar'), t('zst.monthApr'),
  t('zst.monthMai'), t('zst.monthJun'), t('zst.monthJul'), t('zst.monthAug'),
  t('zst.monthSep'), t('zst.monthOkt'), t('zst.monthNov'), t('zst.monthDez'),
])

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

function toggleHoliday(name: string) {
  if (cfg.selected_holidays.length === 0) {
    cfg.selected_holidays = holidays.value.map(h => h.name).filter(n => n !== name)
  } else {
    const i = cfg.selected_holidays.indexOf(name)
    if (i >= 0) {
      cfg.selected_holidays.splice(i, 1)
    } else {
      cfg.selected_holidays.push(name)
      if (cfg.selected_holidays.length === holidays.value.length) cfg.selected_holidays = []
    }
  }
}

async function onTimerTypeChange() {
  if (cfg.timer_type === 'holiday' || winFrom.type === 'holiday_name' || winTo.type === 'holiday_name') {
    await loadHolidaysIfNeeded()
  }
}

const showTimeRef  = computed(() => cfg.timer_type !== 'meta')
const showAbsolute = computed(() => showTimeRef.value && cfg.time_ref === 'absolute')
const showOffset   = computed(() => showTimeRef.value && cfg.time_ref !== 'absolute' && cfg.time_ref !== 'solar_altitude')
const showSolar    = computed(() => showTimeRef.value && cfg.time_ref === 'solar_altitude')

const winFromExpr = computed(() => buildWinExpr(winFrom))
const winToExpr   = computed(() => buildWinExpr(winTo))

const iCls = 'w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 disabled:opacity-50'
const lCls = 'block text-xs text-gray-500 dark:text-gray-400 mb-1'
const hCls = 'text-xs text-gray-400 dark:text-gray-500 mt-0.5'
</script>

<template>
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
    @click.self="emit('close')"
  >
    <div class="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col">

      <div class="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
        <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">🕐 {{ $t('zst.editBinding') }}</h2>
        <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none" @click="emit('close')">×</button>
      </div>

      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        <div v-if="loading" class="text-sm text-gray-500 dark:text-gray-400 text-center py-6">{{ $t('common.loading') }}</div>

        <template v-else-if="!errorMsg">

          <!-- Aktiviert -->
          <div class="flex items-center gap-2">
            <input id="zt-enabled" type="checkbox" v-model="bindingEnabled" class="w-4 h-4 rounded accent-blue-500" />
            <label for="zt-enabled" class="text-sm text-gray-700 dark:text-gray-200">{{ $t('zst.bindingEnabled') }}</label>
          </div>

          <hr class="border-gray-200 dark:border-gray-700" />

          <!-- Typ -->
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label :class="lCls">{{ $t('zst.timerType') }}</label>
              <select v-model="cfg.timer_type" :class="iCls" @change="onTimerTypeChange">
                <option value="daily">{{ $t('zst.timerTypeDaily') }}</option>
                <option value="annual">{{ $t('zst.timerTypeAnnual') }}</option>
                <option value="holiday">{{ $t('zst.timerTypeHoliday') }}</option>
                <option value="meta">{{ $t('zst.timerTypeMeta') }}</option>
              </select>
            </div>
            <div v-if="cfg.timer_type === 'meta'">
              <label :class="lCls">{{ $t('zst.metaType') }}</label>
              <select v-model="cfg.meta_type" :class="iCls">
                <optgroup :label="$t('zst.metaHolidayGroup')">
                  <option value="holiday_today">{{ $t('zst.metaHolidayToday') }}</option>
                  <option value="holiday_tomorrow">{{ $t('zst.metaHolidayTomorrow') }}</option>
                  <option value="holiday_name_today">{{ $t('zst.metaHolidayNameToday') }}</option>
                  <option value="holiday_name_tomorrow">{{ $t('zst.metaHolidayNameTomorrow') }}</option>
                </optgroup>
                <optgroup :label="$t('zst.metaVacationGroup')">
                  <option value="vacation_1">{{ $t('zst.metaVacation1') }}</option>
                  <option value="vacation_2">{{ $t('zst.metaVacation2') }}</option>
                  <option value="vacation_3">{{ $t('zst.metaVacation3') }}</option>
                  <option value="vacation_4">{{ $t('zst.metaVacation4') }}</option>
                  <option value="vacation_5">{{ $t('zst.metaVacation5') }}</option>
                  <option value="vacation_6">{{ $t('zst.metaVacation6') }}</option>
                </optgroup>
              </select>
              <p :class="hCls">{{ $t('zst.metaTypeHint') }}</p>
            </div>
          </div>

          <template v-if="cfg.timer_type !== 'meta'">

            <!-- Feiertagsschaltuhr: Feiertagsauswahl -->
            <template v-if="cfg.timer_type === 'holiday'">
              <div>
                <label :class="lCls">{{ $t('zst.holidaysLabel') }} <span class="text-gray-400 dark:text-gray-600 font-normal">{{ $t('zst.holidaysHint') }}</span></label>
                <p :class="hCls" class="mb-2">{{ $t('zst.holidaysDesc') }}</p>
                <div v-if="holidaysLoading" class="text-xs text-gray-400 py-2">{{ $t('common.loading') }}</div>
                <div v-else-if="holidaysError" class="text-xs text-red-400 py-2">{{ holidaysError }}</div>
                <div v-else-if="holidays.length === 0" class="text-xs text-gray-400 italic py-2">{{ $t('zst.noHolidays') }}</div>
                <div v-else class="space-y-0.5 max-h-52 overflow-y-auto border border-gray-200 dark:border-gray-700 rounded p-2 bg-gray-50 dark:bg-gray-800/50">
                  <label v-for="h in holidays" :key="h.name" class="flex items-center gap-2 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/40 px-1.5 py-1 rounded text-xs">
                    <input type="checkbox" :checked="cfg.selected_holidays.length === 0 || cfg.selected_holidays.includes(h.name)" class="w-3.5 h-3.5 rounded flex-shrink-0 accent-blue-500" @change="toggleHoliday(h.name)" />
                    <span class="font-mono text-gray-400 dark:text-gray-500 flex-shrink-0">{{ h.date }}</span>
                    <span class="text-gray-700 dark:text-gray-200 truncate">{{ h.name }}</span>
                  </label>
                </div>
                <div class="flex items-center gap-3 mt-1.5">
                  <button type="button" class="text-xs text-gray-400 hover:text-blue-400" @click="cfg.selected_holidays = []">{{ $t('zst.allHolidays') }}</button>
                  <span class="text-xs text-gray-300 dark:text-gray-600">·</span>
                  <span class="text-xs text-gray-400">{{ cfg.selected_holidays.length === 0 ? $t('zst.allHolidaysLabel') : $t('zst.selectedCount', { count: cfg.selected_holidays.length }) }}</span>
                  <button type="button" class="text-xs text-gray-400 hover:text-blue-400 ml-auto" @click="loadHolidays">{{ $t('zst.reloadHolidays') }}</button>
                </div>
              </div>
            </template>

            <!-- Wochentage (nicht für Feiertagsschaltuhr) -->
            <div v-if="cfg.timer_type !== 'holiday'">
              <label :class="lCls">{{ $t('zst.weekdays') }}</label>
              <div class="flex gap-1.5 flex-wrap">
                <button v-for="(lbl, idx) in WEEKDAY_LABELS" :key="idx" type="button"
                  class="px-2.5 py-1 text-xs font-medium rounded border transition-colors"
                  :class="cfg.weekdays.includes(idx) ? 'bg-blue-600 border-blue-600 text-white' : 'bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:border-blue-500'"
                  @click="toggleWeekday(idx)">{{ lbl }}</button>
                <button type="button" class="ml-2 text-xs text-gray-400 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4,5,6]">{{ $t('zst.weekdayAll') }}</button>
                <button type="button" class="text-xs text-gray-400 hover:text-blue-400" @click="cfg.weekdays = [0,1,2,3,4]">{{ $t('zst.weekdayMoFr') }}</button>
                <button type="button" class="text-xs text-gray-400 hover:text-blue-400" @click="cfg.weekdays = [5,6]">{{ $t('zst.weekdaySaSo') }}</button>
              </div>
            </div>

            <!-- Monate + Tag (nur Jahresschaltuhr) -->
            <template v-if="cfg.timer_type === 'annual'">
              <div>
                <label :class="lCls">{{ $t('zst.months') }} <span class="text-gray-400 dark:text-gray-600">{{ $t('zst.monthsHint') }}</span></label>
                <div class="flex gap-1 flex-wrap">
                  <button v-for="(lbl, idx) in MONTH_LABELS" :key="idx+1" type="button"
                    class="px-2 py-1 text-xs font-medium rounded border transition-colors"
                    :class="cfg.months.includes(idx+1) ? 'bg-blue-600 border-blue-600 text-white' : 'bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:border-blue-500'"
                    @click="toggleMonth(idx + 1)">{{ lbl }}</button>
                  <button type="button" class="ml-1 text-xs text-gray-400 hover:text-blue-400" @click="cfg.months = []">{{ $t('zst.monthAll') }}</button>
                </div>
              </div>
              <div class="w-36">
                <label :class="lCls">{{ $t('zst.dayOfMonth') }} <span class="text-gray-400 dark:text-gray-600">{{ $t('zst.dayOfMonthHint') }}</span></label>
                <input v-model.number="cfg.day_of_month" type="number" min="0" max="31" :class="iCls" />
              </div>
            </template>

            <!-- Zeitreferenz -->
            <hr class="border-gray-200 dark:border-gray-700" />
            <div class="w-56">
              <label :class="lCls">{{ $t('zst.timeRef') }}</label>
              <select v-model="cfg.time_ref" :class="iCls">
                <option value="absolute">{{ $t('zst.timeRefAbsolute') }}</option>
                <option value="sunrise">{{ $t('zst.timeRefSunrise') }}</option>
                <option value="sunset">{{ $t('zst.timeRefSunset') }}</option>
                <option value="solar_noon">{{ $t('zst.timeRefSolarNoon') }}</option>
                <option value="solar_altitude">{{ $t('zst.timeRefSolarAltitude') }}</option>
              </select>
            </div>

            <div v-if="showAbsolute" class="grid grid-cols-2 gap-3">
              <div><label :class="lCls">{{ $t('zst.hour') }}</label><input v-model.number="cfg.hour" type="number" min="0" max="23" :class="iCls" /></div>
              <div><label :class="lCls">{{ $t('zst.minute') }}</label><input v-model.number="cfg.minute" type="number" min="0" max="59" :class="iCls" /></div>
            </div>
            <div v-if="showOffset" class="w-44">
              <label :class="lCls">{{ $t('zst.offsetMinutes') }}</label>
              <input v-model.number="cfg.offset_minutes" type="number" :class="iCls" placeholder="0" />
              <p :class="hCls">{{ $t('zst.offsetMinutesHint') }}</p>
            </div>
            <div v-if="showSolar" class="grid grid-cols-2 gap-3">
              <div>
                <label :class="lCls">{{ $t('zst.solarAltitudeDeg') }}</label>
                <input v-model.number="cfg.solar_altitude_deg" type="number" min="-18" max="90" step="0.5" :class="iCls" />
                <p :class="hCls">{{ $t('zst.solarAltitudeDegHint') }}</p>
              </div>
              <div>
                <label :class="lCls">{{ $t('zst.sunDirection') }}</label>
                <select v-model="cfg.sun_direction" :class="iCls">
                  <option value="rising">{{ $t('zst.sunDirRising') }}</option>
                  <option value="setting">{{ $t('zst.sunDirSetting') }}</option>
                </select>
              </div>
            </div>

            <!-- Takt -->
            <hr class="border-gray-200 dark:border-gray-700" />
            <div class="grid grid-cols-2 gap-3">
              <div class="flex items-center gap-2">
                <input id="zt-every-minute" type="checkbox" v-model="cfg.every_minute" class="w-4 h-4 rounded accent-blue-500" />
                <div>
                  <label for="zt-every-minute" class="text-xs text-gray-600 dark:text-gray-300">{{ $t('zst.everyMinute') }}</label>
                  <p :class="hCls">{{ cfg.timer_type === 'holiday' ? $t('zst.everyMinuteHintHoliday') : $t('zst.everyMinuteHint') }}</p>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <input id="zt-every-hour" type="checkbox" v-model="cfg.every_hour" class="w-4 h-4 rounded accent-blue-500" />
                <div>
                  <label for="zt-every-hour" class="text-xs text-gray-600 dark:text-gray-300">{{ $t('zst.everyHour') }}</label>
                  <p :class="hCls">{{ $t('zst.everyHourHint') }}</p>
                </div>
              </div>
            </div>
            <div v-if="cfg.every_hour && !cfg.every_minute" class="w-32">
              <label :class="lCls">{{ $t('zst.atMinute') }}</label>
              <input v-model.number="cfg.minute" type="number" min="0" max="59" :class="iCls" />
            </div>

            <!-- Feiertag / Ferien -->
            <hr class="border-gray-200 dark:border-gray-700" />
            <div class="grid grid-cols-2 gap-3">
              <div v-if="cfg.timer_type !== 'holiday'">
                <label :class="lCls">{{ $t('zst.holidayMode') }}</label>
                <select v-model="cfg.holiday_mode" :class="iCls">
                  <option value="ignore">{{ $t('zst.holidayModeIgnore') }}</option>
                  <option value="skip">{{ $t('zst.holidayModeSkip') }}</option>
                  <option value="only">{{ $t('zst.holidayModeOnly') }}</option>
                  <option value="as_sunday">{{ $t('zst.holidayModeAsSunday') }}</option>
                </select>
              </div>
              <div>
                <label :class="lCls">{{ $t('zst.vacationMode') }}</label>
                <select v-model="cfg.vacation_mode" :class="iCls">
                  <option value="ignore">{{ $t('zst.vacationModeIgnore') }}</option>
                  <option value="skip">{{ $t('zst.vacationModeSkip') }}</option>
                  <option value="only">{{ $t('zst.vacationModeOnly') }}</option>
                  <option value="as_sunday">{{ $t('zst.vacationModeAsSunday') }}</option>
                </select>
              </div>
            </div>

            <!-- Datum-Fenster -->
            <hr class="border-gray-200 dark:border-gray-700" />
            <div>
              <div class="flex items-center gap-2 mb-3">
                <input id="zt-date-window" type="checkbox" v-model="cfg.date_window_enabled" class="w-4 h-4 rounded accent-blue-500" />
                <label for="zt-date-window" class="text-xs font-medium text-gray-700 dark:text-gray-200">{{ $t('zst.dateWindow') }}</label>
              </div>

              <template v-if="cfg.date_window_enabled">
                <!-- Helper: reusable endpoint row -->
                <template v-for="(ep, epLabel) in [{ ep: winFrom, label: $t('zst.windowFrom') }, { ep: winTo, label: $t('zst.windowTo') }]" :key="epLabel">
                  <div class="mb-3">
                    <label :class="lCls">{{ ep.label }}</label>
                    <div class="flex gap-2 flex-wrap items-center">
                      <select v-model="ep.ep.type" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-40" @change="onWinTypeChange(ep.ep)">
                        <option value="fixed">{{ $t('zst.windowTypeFixed') }}</option>
                        <option value="easter">{{ $t('zst.windowTypeEaster') }}</option>
                        <option value="advent">{{ $t('zst.windowTypeAdvent') }}</option>
                        <option value="holiday_name">{{ $t('zst.windowTypeHolidayName') }}</option>
                      </select>
                      <!-- Fixed -->
                      <template v-if="ep.ep.type === 'fixed'">
                        <select v-model.number="ep.ep.month" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-28">
                          <option v-for="m in WINDOW_MONTHS" :key="m.v" :value="m.v">{{ m.l }}</option>
                        </select>
                        <input v-model.number="ep.ep.day" type="number" min="1" max="31" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-14" />
                      </template>
                      <!-- Easter / Advent -->
                      <template v-else-if="ep.ep.type === 'easter' || ep.ep.type === 'advent'">
                        <select v-model="ep.ep.sign" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-12">
                          <option value="+">+</option>
                          <option value="-">−</option>
                        </select>
                        <input v-model.number="ep.ep.offset" type="number" min="0" max="400" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-16" />
                        <span class="text-xs text-gray-400">{{ $t('zst.days') }}</span>
                      </template>
                      <!-- Holiday by name -->
                      <template v-else-if="ep.ep.type === 'holiday_name'">
                        <div v-if="holidaysLoading" class="text-xs text-gray-400">{{ $t('common.loading') }}</div>
                        <select v-else v-model="ep.ep.name" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 flex-1 min-w-0">
                          <option value="">{{ $t('zst.pickHoliday') }}</option>
                          <option v-for="h in holidays" :key="h.name" :value="h.name">{{ h.date }} · {{ h.name }}</option>
                        </select>
                        <select v-model="ep.ep.sign" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-12">
                          <option value="+">+</option>
                          <option value="-">−</option>
                        </select>
                        <input v-model.number="ep.ep.offset" type="number" min="0" max="400" placeholder="0" class="bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-xs text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 w-16" />
                        <span class="text-xs text-gray-400">{{ $t('zst.days') }}</span>
                      </template>
                    </div>
                    <p :class="hCls">{{ describeWinEp(ep.ep) }}</p>
                  </div>
                </template>

                <!-- Preview -->
                <div v-if="winFromExpr && winToExpr" class="text-xs font-mono text-blue-500 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1">
                  {{ winFromExpr }} → {{ winToExpr }}
                </div>
              </template>
            </div>

            <!-- Ausgabewert -->
            <hr class="border-gray-200 dark:border-gray-700" />
            <div class="w-40">
              <label :class="lCls">{{ $t('zst.switchValue') }}</label>
              <input v-model="cfg.value" type="text" :class="iCls" placeholder="1" />
              <p :class="hCls">{{ $t('zst.switchValueHint') }}</p>
            </div>

          </template><!-- /timer_type !== meta -->

        </template>

        <p v-if="errorMsg" class="text-sm text-red-400">{{ errorMsg }}</p>

      </div>

      <div class="flex justify-end gap-2 px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
        <button class="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 rounded" @click="emit('close')">{{ $t('common.cancel') }}</button>
        <button
          class="px-4 py-1.5 text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white rounded disabled:opacity-50"
          :disabled="saving || loading || !!errorMsg"
          @click="save"
        >{{ saving ? $t('common.save') + ' …' : $t('common.save') }}</button>
      </div>

    </div>
  </div>
</template>
