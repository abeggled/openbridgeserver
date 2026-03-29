import { defineStore } from 'pinia'
import { ref } from 'vue'
import { settingsApi } from '@/api/client'

export const useSettingsStore = defineStore('settings', () => {
  const timezone = ref('Europe/Zurich')
  const loaded   = ref(false)

  async function load() {
    try {
      const { data } = await settingsApi.get()
      timezone.value = data.timezone ?? 'Europe/Zurich'
    } catch {
      // Backend not reachable or not logged in yet — keep default
    } finally {
      loaded.value = true
    }
  }

  async function save(tz) {
    const { data } = await settingsApi.update({ timezone: tz })
    timezone.value = data.timezone
  }

  return { timezone, loaded, load, save }
})
