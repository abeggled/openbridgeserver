import { defineStore } from 'pinia'
import { ref } from 'vue'
import { settingsApi } from '@/api/client'
import { setLocale } from '@/i18n'

export const useSettingsStore = defineStore('settings', () => {
  const timezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
  const dateFormat = ref('dd.MM.yyyy')
  const timeFormat = ref('HH:mm:ss')
  const language = ref(localStorage.getItem('obs-locale') ?? 'de')
  const theme    = ref(localStorage.getItem('theme') ?? 'system')
  const loaded   = ref(false)

  function applyTheme() {
    const isDark = theme.value === 'dark' ||
      (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.classList.toggle('dark', isDark)
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
    } catch {}
    loaded.value = true
    applyTheme()
  }

  async function save(tz, dateFmt = dateFormat.value, timeFmt = timeFormat.value, languageCode = language.value) {
    await settingsApi.update({ timezone: tz, date_format: dateFmt, time_format: timeFmt, language: languageCode })
    timezone.value = tz
    dateFormat.value = dateFmt
    timeFormat.value = timeFmt
    language.value = languageCode
  }

  async function saveLanguage(languageCode) {
    await settingsApi.update({ timezone: timezone.value, language: languageCode })
    language.value = languageCode
  }

  function setTheme(value) {
    theme.value = value
    localStorage.setItem('theme', value)
    applyTheme()
  }

  return { timezone, dateFormat, timeFormat, language, theme, loaded, load, save, saveLanguage, setTheme, applyTheme }
})
