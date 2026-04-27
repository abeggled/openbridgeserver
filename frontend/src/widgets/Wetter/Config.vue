<script setup lang="ts">
import { reactive, watch } from 'vue'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const cfg = reactive({
  label:                        (props.modelValue.label                        as string)  ?? '',
  url:                          (props.modelValue.url                          as string)  ?? '',
  refreshInterval:              (props.modelValue.refreshInterval              as number)  ?? 600,
  units:                        (props.modelValue.units                        as string)  ?? 'metric',
  show_feels_like:              (props.modelValue.show_feels_like              as boolean) ?? true,
  show_humidity:                (props.modelValue.show_humidity                as boolean) ?? true,
  show_wind:                    (props.modelValue.show_wind                    as boolean) ?? true,
  show_pressure:                (props.modelValue.show_pressure                as boolean) ?? false,
  show_uvi:                     (props.modelValue.show_uvi                     as boolean) ?? false,
  show_clouds:                  (props.modelValue.show_clouds                  as boolean) ?? false,
  show_visibility:              (props.modelValue.show_visibility               as boolean) ?? false,
  show_sunrise_sunset:          (props.modelValue.show_sunrise_sunset          as boolean) ?? false,
  show_forecast:                (props.modelValue.show_forecast                as boolean) ?? true,
  forecast_days:                (props.modelValue.forecast_days                as number)  ?? 4,
  show_forecast_precipitation:  (props.modelValue.show_forecast_precipitation  as boolean) ?? true,
  show_alerts:                  (props.modelValue.show_alerts                  as boolean) ?? true,
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-4">

    <!-- Bezeichnung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Bezeichnung (Ortname)</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Zürich, Garten, Berghaus …"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
      <p class="text-xs text-gray-600 mt-1">Leer lassen → Timezone aus API-Antwort wird verwendet</p>
    </div>

    <!-- API-URL -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Wetter-API-URL</label>
      <input
        v-model="cfg.url"
        type="text"
        placeholder="https://api.openweathermap.org/data/3.0/onecall?lat=47.37&lon=8.54&appid=…&units=metric&lang=de"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 font-mono focus:outline-none focus:border-blue-500"
      />
      <p class="text-xs text-gray-600 mt-1">
        OpenWeatherMap One Call API 3.0 empfohlen. Der API-Key wird als Teil der URL übergeben
        (<code class="text-gray-500">appid=…</code>).
      </p>
    </div>

    <!-- Einheiten & Aktualisierung -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="block text-xs text-gray-400 mb-1">Einheiten</label>
        <select
          v-model="cfg.units"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        >
          <option value="metric">Metrisch (°C, m/s)</option>
          <option value="imperial">Imperial (°F, mph)</option>
        </select>
        <p class="text-xs text-gray-600 mt-1">Muss mit <code class="text-gray-500">units=</code> in der URL übereinstimmen</p>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">Aktualisierung (Sekunden)</label>
        <input
          v-model.number="cfg.refreshInterval"
          type="number"
          min="60"
          max="86400"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        />
      </div>
    </div>

    <!-- Aktuelle Werte -->
    <div>
      <p class="text-xs text-gray-400 mb-2 font-medium">Aktuelle Wetterwerte anzeigen</p>
      <div class="grid grid-cols-2 gap-1.5">
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_feels_like" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Gefühlt wie</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_humidity" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Luftfeuchtigkeit</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_wind" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Wind</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_pressure" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Luftdruck</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_uvi" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">UV-Index</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_clouds" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Bewölkung</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_visibility" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Sichtweite</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_sunrise_sunset" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Sonnenauf/-untergang</span>
        </label>
      </div>
    </div>

    <!-- Vorhersage -->
    <div>
      <label class="flex items-center gap-2 cursor-pointer mb-2">
        <input v-model="cfg.show_forecast" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
        <span class="text-xs text-gray-300 font-medium">Tagesvorhersage anzeigen</span>
      </label>
      <div v-if="cfg.show_forecast" class="pl-4 space-y-2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Anzahl Tage (1–7)</label>
          <input
            v-model.number="cfg.forecast_days"
            type="number"
            min="1"
            max="7"
            class="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <label class="flex items-center gap-2 cursor-pointer">
          <input v-model="cfg.show_forecast_precipitation" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
          <span class="text-xs text-gray-300">Niederschlagswahrscheinlichkeit</span>
        </label>
      </div>
    </div>

    <!-- Warnungen -->
    <label class="flex items-center gap-2 cursor-pointer">
      <input v-model="cfg.show_alerts" type="checkbox" class="rounded border-gray-600 bg-gray-800 text-blue-500" />
      <span class="text-xs text-gray-300">Wetterwarnungen anzeigen</span>
    </label>

  </div>
</template>
