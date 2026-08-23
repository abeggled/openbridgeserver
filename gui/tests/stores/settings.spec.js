import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const settingsApiMock = {
  get: vi.fn(),
  update: vi.fn(),
  displaySettings: vi.fn(),
}
const setLocaleMock = vi.fn()

vi.mock('@/api/client', () => ({ settingsApi: settingsApiMock }))
vi.mock('@/i18n', () => ({ setLocale: setLocaleMock }))

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    settingsApiMock.get.mockReset()
    settingsApiMock.update.mockReset()
    settingsApiMock.displaySettings.mockReset()
    settingsApiMock.displaySettings.mockResolvedValue({ data: {} })
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

    await store.save('Europe/Zurich', 'dd.MM.yyyy', 'HH:mm:ss', 'gsw', 'de-CH', 'CHF')

    expect(settingsApiMock.update).toHaveBeenCalledWith({
      timezone: 'Europe/Zurich', date_format: 'dd.MM.yyyy', time_format: 'HH:mm:ss', language: 'gsw',
      region_format: 'de-CH', currency: 'CHF',
    })
    expect(store.language).toBe('gsw')
    expect(store.regionFormat).toBe('de-CH')
    expect(store.currency).toBe('CHF')
  })

  it('defaults the regional format and currency to auto and keeps them on save (#1073)', async () => {
    settingsApiMock.update.mockResolvedValue({})
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    expect(store.regionFormat).toBe('auto')
    expect(store.currency).toBe('auto')

    await store.save('Europe/Zurich')

    expect(settingsApiMock.update).toHaveBeenCalledWith(
      expect.objectContaining({ region_format: 'auto', currency: 'auto' }),
    )
  })

  it('loads the regional format and the selectable option lists (#1073)', async () => {
    settingsApiMock.get.mockResolvedValue({
      data: { timezone: 'UTC', language: 'de', region_format: 'de-CH', currency: 'CHF' },
    })
    settingsApiMock.displaySettings.mockResolvedValue({
      data: { supported_region_formats: ['auto', 'de-CH'], supported_currencies: ['auto', 'CHF'] },
    })
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    await store.load()

    expect(store.regionFormat).toBe('de-CH')
    expect(store.currency).toBe('CHF')
    expect(store.supportedRegionFormats).toEqual(['auto', 'de-CH'])
    expect(store.supportedCurrencies).toEqual(['auto', 'CHF'])
  })

  it('keeps empty option lists when the public display settings are unreachable (#1073)', async () => {
    // No timezone in the payload — the store keeps its detected default.
    settingsApiMock.get.mockResolvedValue({ data: {} })
    settingsApiMock.displaySettings.mockRejectedValue(new Error('offline'))
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()

    await store.load()

    expect(store.supportedRegionFormats).toEqual([])
    expect(store.supportedCurrencies).toEqual([])
    expect(store.loaded).toBe(true)
  })

  it('saves a language change without resubmitting formats', async () => {
    settingsApiMock.update.mockResolvedValue({})
    const { useSettingsStore } = await import('@/stores/settings')
    const store = useSettingsStore()
    await store.saveLanguage('en')

    expect(settingsApiMock.update).toHaveBeenCalledWith({ language: 'en' })
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
