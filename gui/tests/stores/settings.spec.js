import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const settingsApiMock = {
  get: vi.fn(),
  update: vi.fn(),
}
const setLocaleMock = vi.fn()

vi.mock('@/api/client', () => ({ settingsApi: settingsApiMock }))
vi.mock('@/i18n', () => ({ setLocale: setLocaleMock }))

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    settingsApiMock.get.mockReset()
    settingsApiMock.update.mockReset()
    setLocaleMock.mockReset()
    localStorage.clear()
    document.documentElement.classList.remove('dark')
  })

  it('loads all server-side date and language settings', async () => {
    settingsApiMock.get.mockResolvedValue({
      data: { timezone: 'UTC', date_format: 'yyyy/MM/dd', time_format: 'H:mm', language: 'en' },
    })
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    await store.load()

    expect(store.timezone).toBe('UTC')
    expect(store.dateFormat).toBe('yyyy/MM/dd')
    expect(store.timeFormat).toBe('H:mm')
    expect(store.language).toBe('en')
    expect(store.loaded).toBe(true)
    expect(setLocaleMock).toHaveBeenCalledWith('en')
  })

  it('finishes loading when the settings request fails', async () => {
    settingsApiMock.get.mockRejectedValue(new Error('offline'))
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    await store.load()

    expect(store.loaded).toBe(true)
  })

  it('saves and applies all settings', async () => {
    settingsApiMock.update.mockResolvedValue({})
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    await store.save('Europe/Zurich', 'dd.MM.yyyy', 'HH:mm:ss', 'gsw')

    expect(settingsApiMock.update).toHaveBeenCalledWith({
      timezone: 'Europe/Zurich', date_format: 'dd.MM.yyyy', time_format: 'HH:mm:ss', language: 'gsw',
    })
    expect(store.language).toBe('gsw')
  })

  it('saves a language change without resubmitting formats', async () => {
    settingsApiMock.update.mockResolvedValue({})
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()
    store.timezone = 'UTC'

    await store.saveLanguage('en')

    expect(settingsApiMock.update).toHaveBeenCalledWith({ timezone: 'UTC', language: 'en' })
    expect(store.language).toBe('en')
  })

  it('persists and applies the selected theme', async () => {
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    store.setTheme('dark')

    expect(localStorage.getItem('theme')).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
