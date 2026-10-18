import { defineStore } from 'pinia'
import { ref } from 'vue'
import { settingsApi } from '@/api/client'
import { setLocale } from '@/i18n'

export const useSettingsStore = defineStore('settings', () => {
  const timezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
  const dateFormat = ref('dd.MM.yyyy')
  const timeFormat = ref('HH:mm:ss')
  const language = ref(localStorage.getItem('obs-locale') ?? 'de')
  // Regional format for numbers/currency/date — an explicit setting, deliberately
  // independent of the UI language (issue #1073). 'auto' derives it from the language.
  const regionFormat = ref('auto')
  const currency = ref('auto')
  const supportedRegionFormats = ref([])
  const supportedCurrencies = ref([])
  const theme    = ref(localStorage.getItem('theme') ?? 'system')
  const loaded   = ref(false)
  // Reactive mirror of the resolved dark/light state applyTheme() puts on
  // <html>. `theme` alone isn't enough for anything that needs to react to
  // the *effective* palette (e.g. HelpDrawer's iframe) — when theme is
  // 'system', the resolved value can also change from the OS-level
  // prefers-color-scheme listener (see App.vue), which never touches
  // `theme.value` itself.
  const isDarkResolved = ref(false)

  function applyTheme() {
    const isDark = theme.value === 'dark' ||
      (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.classList.toggle('dark', isDark)
    isDarkResolved.value = isDark
  }

  async function load() {
    try {
      const { data } = await settingsApi.get()
      if (data.timezone) timezone.value = data.timezone
      if (data.date_format) dateFormat.value = data.date_format
      if (data.time_format) timeFormat.value = data.time_format
      if (data.language) {
        language.value = data.language
        setLocale(data.language)
      }
      if (data.region_format) regionFormat.value = data.region_format
      if (data.currency) currency.value = data.currency
    } catch {}
    try {
      const { data } = await settingsApi.displaySettings()
      supportedRegionFormats.value = data.supported_region_formats ?? []
      supportedCurrencies.value = data.supported_currencies ?? []
    } catch {}
    loaded.value = true
    applyTheme()
  }

  async function save(
    tz,
    dateFmt = dateFormat.value,
    timeFmt = timeFormat.value,
    languageCode = language.value,
    region = regionFormat.value,
    currencyCode = currency.value,
  ) {
    await settingsApi.update({
      timezone: tz,
      date_format: dateFmt,
      time_format: timeFmt,
      language: languageCode,
      region_format: region,
      currency: currencyCode,
    })
    timezone.value = tz
    dateFormat.value = dateFmt
    timeFormat.value = timeFmt
    language.value = languageCode
    regionFormat.value = region
    currency.value = currencyCode
  }

  async function saveLanguage(languageCode) {
    await settingsApi.update({ language: languageCode })
    language.value = languageCode
  }

  function setTheme(value) {
    theme.value = value
    localStorage.setItem('theme', value)
    applyTheme()
  }

  return {
    timezone, dateFormat, timeFormat, language, regionFormat, currency,
    supportedRegionFormats, supportedCurrencies,
    theme, isDarkResolved, loaded, load, save, saveLanguage, setTheme, applyTheme,
  }
})
