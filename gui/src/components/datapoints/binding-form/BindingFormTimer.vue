<template>
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
              @change="$emit('zt-toggle-holiday', h.name)"
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
          <button type="button" class="text-xs text-slate-400 hover:text-blue-400 ml-auto" @click="$emit('load-zsu-holidays')">{{ $t('adapters.bindingForm.ztReload') }}</button>
        </div>
      </div>
    </template>

    <!-- Wochentage (nicht für Feiertagsschaltuhr) -->
    <div v-if="cfg.timer_type !== 'holiday'" class="form-group">
      <label class="label">{{ $t('adapters.bindingForm.ztWeekdaysLabel') }}</label>
      <div class="flex gap-1.5 flex-wrap">
        <button
          v-for="(label, idx) in weekdayShorts"
          :key="idx"
          type="button"
          @click="$emit('zt-toggle-weekday', idx)"
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
            v-for="(label, idx) in monthShorts"
            :key="idx+1"
            type="button"
            @click="$emit('zt-toggle-month', idx+1)"
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
            <select v-model="ep.ep.type" class="input text-xs" style="width:160px" @change="$emit('win-type-change', ep.ep)">
              <option value="fixed">{{ $t('adapters.bindingForm.ztDateTypeFixed') }}</option>
              <option value="easter">{{ $t('adapters.bindingForm.ztDateTypeEaster') }}</option>
              <option value="advent">{{ $t('adapters.bindingForm.ztDateTypeAdvent') }}</option>
              <option value="holiday_name">{{ $t('adapters.bindingForm.ztDateTypeHolidayName') }}</option>
            </select>
            <template v-if="ep.ep.type === 'fixed'">
              <select v-model.number="ep.ep.month" class="input text-xs" style="width:110px">
                <option v-for="m in winMonths" :key="m.v" :value="m.v">{{ m.l }}</option>
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

<script setup>
defineProps({
  cfg: { type: Object, required: true },
  ztHolidays: { type: Array, required: true },
  ztHolidaysLoading: { type: Boolean, required: true },
  ztHolidaysError: { type: [String, null], default: null },
  weekdayShorts: { type: Array, required: true },
  monthShorts: { type: Array, required: true },
  winMonths: { type: Array, required: true },
  winFrom: { type: Object, required: true },
  winTo: { type: Object, required: true },
  buildWinExpr: { type: Function, required: true },
  describeWinEp: { type: Function, required: true },
})

defineEmits([
  'zt-toggle-holiday',
  'load-zsu-holidays',
  'zt-toggle-weekday',
  'zt-toggle-month',
  'win-type-change',
])
</script>
