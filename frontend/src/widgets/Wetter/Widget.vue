<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import type { DataPointValue } from '@/types'
import { getJwt } from '@/api/client'

// ── OWM One Call API Typen ─────────────────────────────────────────────────────

interface OWMWeatherCondition {
  id: number
  main: string
  description: string
  icon: string
}

interface OWMCurrent {
  dt: number
  sunrise: number
  sunset: number
  temp: number
  feels_like: number
  humidity: number
  pressure: number
  uvi: number
  visibility: number
  wind_speed: number
  wind_deg: number
  clouds: number
  weather: OWMWeatherCondition[]
}

interface OWMDailyTemp {
  min: number
  max: number
  day: number
}

interface OWMDaily {
  dt: number
  temp: OWMDailyTemp
  weather: OWMWeatherCondition[]
  pop: number
  humidity: number
  sunrise: number
  sunset: number
}

interface OWMAlert {
  sender_name: string
  event: string
  start: number
  end: number
  description: string
}

interface OWMData {
  lat: number
  lon: number
  timezone: string
  current: OWMCurrent
  daily: OWMDaily[]
  alerts?: OWMAlert[]
}

// ── Props ──────────────────────────────────────────────────────────────────────

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  statusValue: DataPointValue | null
  editorMode: boolean
}>()

// ── Config ─────────────────────────────────────────────────────────────────────

const label                    = computed(() => (props.config.label                    as string)  ?? '')
const url                      = computed(() => (props.config.url                      as string)  ?? '')
const refreshInterval          = computed(() => (props.config.refreshInterval          as number)  ?? 600)
const units                    = computed(() => (props.config.units                    as string)  ?? 'metric')
const showFeelsLike            = computed(() => (props.config.show_feels_like          as boolean) ?? true)
const showHumidity             = computed(() => (props.config.show_humidity            as boolean) ?? true)
const showWind                 = computed(() => (props.config.show_wind                as boolean) ?? true)
const showPressure             = computed(() => (props.config.show_pressure            as boolean) ?? false)
const showUvi                  = computed(() => (props.config.show_uvi                 as boolean) ?? false)
const showClouds               = computed(() => (props.config.show_clouds              as boolean) ?? false)
const showVisibility           = computed(() => (props.config.show_visibility          as boolean) ?? false)
const showSunriseSunset        = computed(() => (props.config.show_sunrise_sunset      as boolean) ?? false)
const showForecast             = computed(() => (props.config.show_forecast            as boolean) ?? true)
const forecastDays             = computed(() => Math.min(7, Math.max(1, (props.config.forecast_days as number) ?? 4)))
const showForecastPrecipitation = computed(() => (props.config.show_forecast_precipitation as boolean) ?? true)
const showAlerts               = computed(() => (props.config.show_alerts              as boolean) ?? true)

// ── Wetterdaten ────────────────────────────────────────────────────────────────

const weatherData  = ref<OWMData | null>(null)
const loading      = ref(false)
const errorMsg     = ref('')
const lastUpdated  = ref<number | null>(null)

// ── Icon-Mapping (OWM-Iconcode → Emoji) ───────────────────────────────────────

const ICON_MAP: Record<string, string> = {
  '01d': '☀️',  '01n': '🌙',
  '02d': '🌤️', '02n': '🌤️',
  '03d': '🌥️', '03n': '🌥️',
  '04d': '☁️',  '04n': '☁️',
  '09d': '🌧️', '09n': '🌧️',
  '10d': '🌦️', '10n': '🌧️',
  '11d': '⛈️',  '11n': '⛈️',
  '13d': '❄️',  '13n': '❄️',
  '50d': '🌫️', '50n': '🌫️',
}

function weatherIcon(icon: string): string {
  return ICON_MAP[icon] ?? '🌡️'
}

// ── Einheiten ──────────────────────────────────────────────────────────────────

const tempUnit  = computed(() => units.value === 'imperial' ? '°F' : '°C')
const speedUnit = computed(() => units.value === 'imperial' ? 'mph' : 'm/s')

function fmtTemp(t: number): string {
  return Math.round(t) + tempUnit.value
}

function fmtSpeed(s: number): string {
  return s.toFixed(1) + ' ' + speedUnit.value
}

function windDir(deg: number): string {
  const dirs = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW']
  return dirs[Math.round(deg / 45) % 8]
}

function fmtTime(unixTs: number): string {
  return new Date(unixTs * 1000).toLocaleTimeString('de', { hour: '2-digit', minute: '2-digit' })
}

function fmtDay(unixTs: number): string {
  return new Date(unixTs * 1000).toLocaleDateString('de', { weekday: 'short' })
}

function fmtPercent(v: number): string {
  return Math.round(v * 100) + ' %'
}

// ── Ortname ────────────────────────────────────────────────────────────────────

const locationName = computed(() => {
  if (label.value) return label.value
  if (!weatherData.value) return ''
  const tz = weatherData.value.timezone
  // "Europe/Zurich" → "Zurich"
  return tz.includes('/') ? tz.split('/').pop()!.replace('_', ' ') : tz
})

// ── Daten laden ────────────────────────────────────────────────────────────────

async function fetchWeather(): Promise<void> {
  if (!url.value || props.editorMode) return

  loading.value = true
  errorMsg.value = ''

  try {
    const jwt = getJwt() ?? ''
    const params = new URLSearchParams({ url: url.value })
    const resp = await fetch(`/api/v1/weather/fetch?${params.toString()}`, {
      headers: jwt ? { Authorization: `Bearer ${jwt}` } : {},
    })

    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`
      try {
        const body = await resp.json()
        if (body?.detail) detail = String(body.detail)
      } catch { /* JSON-Parsing fehlgeschlagen */ }
      throw new Error(detail)
    }

    weatherData.value = await resp.json() as OWMData
    lastUpdated.value = Date.now()
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// ── Auto-Refresh ───────────────────────────────────────────────────────────────

let refreshTimer: ReturnType<typeof setInterval> | null = null

function startRefresh(): void {
  stopRefresh()
  if (props.editorMode || !url.value) return
  const ms = Math.max(60, refreshInterval.value) * 1000
  refreshTimer = setInterval(fetchWeather, ms)
}

function stopRefresh(): void {
  if (refreshTimer !== null) { clearInterval(refreshTimer); refreshTimer = null }
}

onMounted(() => { fetchWeather(); startRefresh() })
onUnmounted(stopRefresh)
watch([url, refreshInterval], () => { fetchWeather(); startRefresh() })

// ── Forecast-Slice ─────────────────────────────────────────────────────────────

const forecastSlice = computed(() => {
  if (!weatherData.value?.daily) return []
  // Index 0 = heute, ab Index 1 = morgige + folgende Tage
  return weatherData.value.daily.slice(1, 1 + forecastDays.value)
})

// ── Warnungen ──────────────────────────────────────────────────────────────────

const activeAlerts = computed(() => {
  if (!showAlerts.value || !weatherData.value?.alerts?.length) return []
  const now = Date.now() / 1000
  return weatherData.value.alerts.filter(a => a.end > now)
})
</script>

<template>
  <div
    class="h-full w-full flex flex-col overflow-hidden bg-gray-900 rounded text-white select-none"
    data-testid="wetter-widget"
  >

    <!-- ── Editor-Platzhalter ───────────────────────────────────────────────── -->
    <div
      v-if="editorMode && !url"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-2"
    >
      <span class="text-4xl">🌤️</span>
      <span class="text-xs">Wetter-API-URL konfigurieren</span>
    </div>

    <!-- ── Editor-Modus mit URL (kein Live-Fetch) ──────────────────────────── -->
    <div
      v-else-if="editorMode && url"
      class="flex-1 flex flex-col items-center justify-center text-gray-500 gap-1"
    >
      <span class="text-3xl">🌤️</span>
      <span class="text-xs text-gray-400">{{ label || 'Wetter' }}</span>
      <span class="text-xs text-gray-600">Vorschau im Live-Modus</span>
    </div>

    <!-- ── Kein URL im Live-Modus ───────────────────────────────────────────── -->
    <div
      v-else-if="!url"
      class="flex-1 flex items-center justify-center text-gray-600 text-xs"
    >
      Keine API-URL konfiguriert
    </div>

    <!-- ── Laden ────────────────────────────────────────────────────────────── -->
    <div
      v-else-if="loading && !weatherData"
      class="flex-1 flex items-center justify-center text-gray-500 text-xs gap-2"
    >
      <span class="animate-spin">⏳</span>
      <span>Wetterdaten werden geladen …</span>
    </div>

    <!-- ── Fehler ────────────────────────────────────────────────────────────── -->
    <div
      v-else-if="errorMsg && !weatherData"
      class="flex-1 flex flex-col items-center justify-center gap-2 px-4"
    >
      <span class="text-2xl">⚠️</span>
      <span class="text-xs text-red-400 text-center">{{ errorMsg }}</span>
      <button
        class="mt-1 px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-xs text-gray-200 transition-colors"
        @click="fetchWeather"
      >
        Neu laden
      </button>
    </div>

    <!-- ── Wetterdaten ───────────────────────────────────────────────────────── -->
    <template v-else-if="weatherData">

      <!-- Header: Ort + Aktualisierung -->
      <div class="flex items-center justify-between px-3 pt-2 pb-1 shrink-0">
        <span
          class="text-sm font-semibold text-gray-100 truncate"
          data-testid="wetter-location"
        >
          {{ locationName }}
        </span>
        <button
          class="text-gray-500 hover:text-gray-300 transition-colors text-xs ml-2 shrink-0"
          title="Jetzt aktualisieren"
          @click="fetchWeather"
        >
          ↺
        </button>
      </div>

      <!-- Aktuelles Wetter ────────────────────────────────────────────────────── -->
      <div class="flex items-center gap-3 px-3 pb-1 shrink-0" data-testid="wetter-current">
        <span class="text-4xl leading-none">
          {{ weatherIcon(weatherData.current.weather[0]?.icon ?? '') }}
        </span>
        <div class="flex flex-col min-w-0">
          <span class="text-3xl font-bold leading-none" data-testid="wetter-temp">
            {{ fmtTemp(weatherData.current.temp) }}
          </span>
          <span class="text-xs text-gray-400 truncate capitalize mt-0.5">
            {{ weatherData.current.weather[0]?.description ?? '' }}
          </span>
        </div>
      </div>

      <!-- Detail-Zeile ─────────────────────────────────────────────────────────── -->
      <div class="flex flex-wrap gap-x-3 gap-y-0.5 px-3 pb-1 text-xs text-gray-400 shrink-0">
        <span v-if="showFeelsLike" data-testid="wetter-feels-like">
          🌡️ {{ fmtTemp(weatherData.current.feels_like) }}
        </span>
        <span v-if="showHumidity" data-testid="wetter-humidity">
          💧 {{ weatherData.current.humidity }} %
        </span>
        <span v-if="showWind" data-testid="wetter-wind">
          💨 {{ fmtSpeed(weatherData.current.wind_speed) }}
          {{ windDir(weatherData.current.wind_deg) }}
        </span>
        <span v-if="showPressure">
          📊 {{ weatherData.current.pressure }} hPa
        </span>
        <span v-if="showUvi">
          ☀️ UV {{ weatherData.current.uvi.toFixed(1) }}
        </span>
        <span v-if="showClouds">
          ☁️ {{ weatherData.current.clouds }} %
        </span>
        <span v-if="showVisibility">
          👁️ {{ (weatherData.current.visibility / 1000).toFixed(1) }} km
        </span>
        <span v-if="showSunriseSunset">
          🌅 {{ fmtTime(weatherData.current.sunrise) }}
          🌇 {{ fmtTime(weatherData.current.sunset) }}
        </span>
      </div>

      <!-- Trennlinie -->
      <div v-if="showForecast && forecastSlice.length" class="border-t border-gray-800 mx-3 my-1 shrink-0" />

      <!-- Vorhersage ───────────────────────────────────────────────────────────── -->
      <div
        v-if="showForecast && forecastSlice.length"
        class="flex gap-1 px-3 pb-2 overflow-x-auto shrink-0"
        data-testid="wetter-forecast"
      >
        <div
          v-for="day in forecastSlice"
          :key="day.dt"
          class="flex flex-col items-center gap-0.5 min-w-[3rem] flex-1"
          data-testid="wetter-forecast-day"
        >
          <span class="text-xs text-gray-500 capitalize">{{ fmtDay(day.dt) }}</span>
          <span class="text-xl leading-none">{{ weatherIcon(day.weather[0]?.icon ?? '') }}</span>
          <span class="text-xs font-semibold text-gray-200">{{ fmtTemp(day.temp.max) }}</span>
          <span class="text-xs text-gray-500">{{ fmtTemp(day.temp.min) }}</span>
          <span
            v-if="showForecastPrecipitation && day.pop > 0"
            class="text-xs text-blue-400"
            :title="`Niederschlag: ${fmtPercent(day.pop)}`"
          >
            {{ fmtPercent(day.pop) }}
          </span>
        </div>
      </div>

      <!-- Warnungen ────────────────────────────────────────────────────────────── -->
      <div
        v-if="activeAlerts.length"
        class="mx-2 mb-2 rounded bg-orange-900/60 border border-orange-700 px-2 py-1.5 shrink-0"
        data-testid="wetter-alerts"
      >
        <div
          v-for="alert in activeAlerts"
          :key="alert.event + alert.start"
          class="text-xs text-orange-200"
        >
          ⚠️ {{ alert.event }}
          <span v-if="alert.sender_name" class="text-orange-400 ml-1">({{ alert.sender_name }})</span>
        </div>
      </div>

    </template>

  </div>
</template>
