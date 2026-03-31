import { defineStore } from 'pinia'
import { ref } from 'vue'
import { settingsApi } from '@/api/client'

export const useSettingsStore = defineStore('settings', () => {
  const timezone = ref(Intl.DateTimeFormat().resolvedOptions().timeZone)
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
    } catch {}
    loaded.value = true
    applyTheme()
  }

  async function save(tz) {
    await settingsApi.update({ timezone: tz })
    timezone.value = tz
  }

  function setTheme(value) {
    theme.value = value
    localStorage.setItem('theme', value)
    applyTheme()
  }

  return { timezone, theme, loaded, load, save, setTheme, applyTheme }
})
