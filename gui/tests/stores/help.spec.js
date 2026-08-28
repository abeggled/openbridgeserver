import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'

const helpApiMock = { index: vi.fn() }
const localeRef = ref('de')

vi.mock('@/api/client', () => ({ helpApi: helpApiMock }))
vi.mock('@/i18n', () => ({ default: { global: { locale: localeRef } } }))

const SAMPLE_INDEX = {
  generatedAt: '2026-08-24T00:00:00.000Z',
  helpIds: {
    'datapoints-overview': {
      de: '/help/datapoints/overview.html#datapoints-overview',
      en: '/help/en/datapoints/overview.html#datapoints-overview',
    },
    'de-only-section': {
      de: '/help/guide/foo.html#de-only-section',
    },
    'en-only-section': {
      en: '/help/en/guide/bar.html#en-only-section',
    },
    // Defensive edge case, not producible by the real generator (it never
    // writes an id with zero locale entries) — exercises the final `?? null`
    // fallback so that branch isn't left permanently uncovered.
    'empty-entry': {},
  },
}

describe('useHelpStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    helpApiMock.index.mockReset()
    localeRef.value = 'de'
  })

  it('starts closed with no current help_id', async () => {
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    expect(store.isOpen).toBe(false)
    expect(store.currentHelpId).toBe(null)
    expect(store.currentUrl).toBe(null)
  })

  it('starts with a zero drawerWidth until HelpDrawer reports its actual width', async () => {
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    expect(store.drawerWidth).toBe(0)
  })

  it('setDrawerWidth updates drawerWidth', async () => {
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    store.setDrawerWidth(420)
    expect(store.drawerWidth).toBe(420)
  })

  it('loadIndex fetches once and populates helpIndex', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    await store.loadIndex()

    expect(helpApiMock.index).toHaveBeenCalledTimes(1)
    expect(store.helpIndex).toEqual(SAMPLE_INDEX)
    expect(store.loadError).toBe(false)
  })

  it('loadIndex does not refetch once the index is already loaded', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    await store.loadIndex()
    await store.loadIndex()

    expect(helpApiMock.index).toHaveBeenCalledTimes(1)
  })

  it('loadIndex sets loadError on failure and allows a later retry', async () => {
    helpApiMock.index.mockRejectedValueOnce(new Error('offline'))
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    await store.loadIndex()
    expect(store.loadError).toBe(true)
    expect(store.helpIndex).toBe(null)

    helpApiMock.index.mockResolvedValueOnce({ data: SAMPLE_INDEX })
    await store.loadIndex()

    expect(store.loadError).toBe(false)
    expect(store.helpIndex).toEqual(SAMPLE_INDEX)
    expect(helpApiMock.index).toHaveBeenCalledTimes(2)
  })

  it('open() sets isOpen and currentHelpId and triggers loadIndex', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    store.open('datapoints-overview')

    expect(store.isOpen).toBe(true)
    expect(store.currentHelpId).toBe('datapoints-overview')
    expect(helpApiMock.index).toHaveBeenCalledTimes(1)
  })

  it('close() sets isOpen to false without clearing currentHelpId', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    store.open('datapoints-overview')
    store.close()

    expect(store.isOpen).toBe(false)
    expect(store.currentHelpId).toBe('datapoints-overview')
  })

  it('currentUrl resolves the URL for the active locale', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    localeRef.value = 'en'

    store.open('datapoints-overview')
    await store.loadIndex()

    expect(store.currentUrl).toBe('/help/en/datapoints/overview.html#datapoints-overview')
  })

  it('currentUrl falls back to de when the active locale has no entry', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    localeRef.value = 'fr'

    store.open('de-only-section')
    await store.loadIndex()

    expect(store.currentUrl).toBe('/help/guide/foo.html#de-only-section')
  })

  it('currentUrl falls back to the first available locale when neither the active locale nor de has an entry', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    localeRef.value = 'fr'

    store.open('en-only-section')
    await store.loadIndex()

    expect(store.currentUrl).toBe('/help/en/guide/bar.html#en-only-section')
  })

  it('currentUrl is null when the resolved entry has no locale keys at all', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    store.open('empty-entry')
    await store.loadIndex()

    expect(store.currentUrl).toBe(null)
  })

  it('currentUrl is null when the help_id is unknown', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()

    store.open('does-not-exist')
    await store.loadIndex()

    expect(store.currentUrl).toBe(null)
  })

  it('reservedRight is 0px while closed, regardless of a persisted drawerWidth', async () => {
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    store.setDrawerWidth(500)

    expect(store.reservedRight).toBe('0px')
  })

  it('reservedRight mirrors drawerWidth (clamped to 90vw and a minimum remaining width) once open — shared by App.vue and every full-viewport popup so they never drift out of sync with the drawer', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    store.open('datapoints-overview')
    store.setDrawerWidth(420)

    expect(store.reservedRight).toBe('min(420px, 90vw, max(0px, 100vw - 300px))')
  })

  it('reservedRight includes the minimum-remaining-width floor even for a small drawerWidth (Codex review, PR #1180 — a narrow viewport could otherwise squeeze a full-viewport modal beside the drawer to a sliver)', async () => {
    helpApiMock.index.mockResolvedValue({ data: SAMPLE_INDEX })
    const { useHelpStore } = await import('@/stores/help')
    const store = useHelpStore()
    store.open('datapoints-overview')
    store.setDrawerWidth(320)

    expect(store.reservedRight).toBe('min(320px, 90vw, max(0px, 100vw - 300px))')
  })
})
