import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

let capturedLogCb = null
const wsUnregMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  capturedLogCb = null
  wsUnregMock.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/websocket')
})

function makeEntry(overrides = {}) {
  return {
    ts:      '2024-01-15T12:00:00Z',
    level:   'INFO',
    logger:  'obs.adapter',
    message: 'Started',
    ...overrides,
  }
}

async function mountLog({
  listData      = [],
  getLevelData  = { level: 'INFO' },
  wsConnected   = true,
  getLevelError = false,
  listError     = null,
} = {}) {
  const logsGetLevel = getLevelError
    ? vi.fn().mockRejectedValue(new Error('403'))
    : vi.fn().mockResolvedValue({ data: getLevelData })

  const logsList = listError
    ? vi.fn().mockRejectedValue(listError)
    : vi.fn().mockResolvedValue({ data: listData })

  const logsSetLevel = vi.fn().mockResolvedValue({})

  vi.doMock('@/api/client', () => ({
    logsApi:     { getLevel: logsGetLevel, list: logsList, setLevel: logsSetLevel },
    settingsApi: { get: vi.fn().mockResolvedValue({ data: {} }) },
    authApi:     { login: vi.fn(), me: vi.fn() },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
  }))

  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({
      connected:    wsConnected,
      onLogEntry:   (fn) => { capturedLogCb = fn; return wsUnregMock },
      onValue:      vi.fn().mockReturnValue(vi.fn()),
      subscribe:    vi.fn(),
    }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: LogView } = await import('@/views/LogView.vue')
  const wrapper = mount(LogView, {
    global: {
      plugins: [pinia],
      stubs: {
        Badge: {
          template: '<span :class="`badge-${variant}`"><slot /></span>',
          props: ['variant', 'size'],
        },
        Spinner: { template: '<span class="spinner" />' },
      },
    },
  })
  await flushPromises()
  return { wrapper, logsGetLevel, logsList, logsSetLevel }
}

// ─── Mount ───────────────────────────────────────────────────────────────────

describe('LogView — mount', () => {
  it('calls logsApi.getLevel on mount', async () => {
    const { logsGetLevel } = await mountLog()
    expect(logsGetLevel).toHaveBeenCalled()
  })

  it('calls logsApi.list on mount', async () => {
    const { logsList } = await mountLog()
    expect(logsList).toHaveBeenCalled()
  })

  it('registers onLogEntry handler on ws store', async () => {
    await mountLog()
    expect(capturedLogCb).toBeTypeOf('function')
  })

  it('keeps default logLevel when getLevel throws (non-admin)', async () => {
    const { wrapper } = await mountLog({ getLevelError: true })
    // Component still renders normally; logLevel stays at default 'INFO'
    expect(wrapper.find('select').exists()).toBe(true)
  })
})

// ─── Entries display ─────────────────────────────────────────────────────────

describe('LogView — entries display', () => {
  it('shows no-entries message when list is empty', async () => {
    const { wrapper } = await mountLog({ listData: [] })
    expect(wrapper.text()).toContain('Keine Log-Einträge vorhanden')
  })

  it('renders one row per entry', async () => {
    const { wrapper } = await mountLog({
      listData: [makeEntry(), makeEntry({ message: 'Second' })],
    })
    expect(wrapper.findAll('[data-testid="log-entry"]').length).toBe(2)
  })

  it('renders logger and message text in each row', async () => {
    const { wrapper } = await mountLog({
      listData: [makeEntry({ logger: 'knx.adapter', message: 'Verbunden' })],
    })
    expect(wrapper.text()).toContain('knx.adapter')
    expect(wrapper.text()).toContain('Verbunden')
  })

  it('shows error message when logsApi.list rejects', async () => {
    const { wrapper } = await mountLog({
      listError: { response: { data: { detail: 'Server Error' } } },
    })
    expect(wrapper.text()).toContain('Server Error')
  })
})

// ─── WS status badge ─────────────────────────────────────────────────────────

describe('LogView — WS status badge', () => {
  it('shows "Live" when ws is connected', async () => {
    const { wrapper } = await mountLog({ wsConnected: true })
    expect(wrapper.find('[data-testid="status-badge"]').text()).toContain('Live')
  })

  it('shows "Offline" when ws is not connected', async () => {
    const { wrapper } = await mountLog({ wsConnected: false })
    expect(wrapper.find('[data-testid="status-badge"]').text()).toContain('Offline')
  })
})

// ─── Filters and controls ────────────────────────────────────────────────────

describe('LogView — filters and controls', () => {
  it('refresh button calls logsApi.list again', async () => {
    const { wrapper, logsList } = await mountLog()
    const countBefore = logsList.mock.calls.length

    const refresh = wrapper.findAll('button').find(b => b.text().includes('↻ Aktualisieren'))
    await refresh.trigger('click')
    await flushPromises()

    expect(logsList.mock.calls.length).toBeGreaterThan(countBefore)
  })

  it('calls logsApi.setLevel with the new value when log level select changes', async () => {
    const { wrapper, logsSetLevel } = await mountLog()
    const [levelSelect] = wrapper.findAll('select')
    await levelSelect.setValue('DEBUG')
    await flushPromises()
    expect(logsSetLevel).toHaveBeenCalledWith('DEBUG')
  })

  it('level filter select triggers logsApi.list', async () => {
    const { wrapper, logsList } = await mountLog()
    const countBefore = logsList.mock.calls.length

    const selects = wrapper.findAll('select')
    await selects[1].setValue('ERROR')
    await flushPromises()

    expect(logsList.mock.calls.length).toBeGreaterThan(countBefore)
  })

  it('applies client-side text filter: only matching entries are shown', async () => {
    const entries = [
      makeEntry({ logger: 'auth.service', message: 'Anmeldung erfolgreich' }),
      makeEntry({ logger: 'app.router',   message: 'Route geladen' }),
    ]
    const { wrapper } = await mountLog({ listData: entries })

    const input = wrapper.find('[data-testid="input-filter"]')
    await input.setValue('auth')

    // Use refresh to call load() directly (no debounce) with updated filters.q
    const refresh = wrapper.findAll('button').find(b => b.text().includes('↻ Aktualisieren'))
    await refresh.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-testid="log-entry"]').length).toBe(1)
    expect(wrapper.text()).toContain('Anmeldung erfolgreich')
    expect(wrapper.text()).not.toContain('Route geladen')
  })
})

// ─── Debounced load ──────────────────────────────────────────────────────────

describe('LogView — debouncedLoad', () => {
  it('fires load only after 350 ms when the filter input receives input', async () => {
    const { wrapper, logsList } = await mountLog()
    vi.useFakeTimers()
    const countBefore = logsList.mock.calls.length

    await wrapper.find('[data-testid="input-filter"]').trigger('input')

    vi.advanceTimersByTime(349)
    expect(logsList.mock.calls.length).toBe(countBefore)

    vi.advanceTimersByTime(1)
    await flushPromises()
    expect(logsList.mock.calls.length).toBeGreaterThan(countBefore)

    vi.useRealTimers()
  })
})

// ─── Live WS entries ─────────────────────────────────────────────────────────

describe('LogView — live ws entries', () => {
  it('prepends a live entry that passes all filters', async () => {
    const { wrapper } = await mountLog({ listData: [] })

    capturedLogCb(makeEntry({ message: 'Live event received' }))
    await nextTick()

    expect(wrapper.findAll('[data-testid="log-entry"]').length).toBe(1)
    expect(wrapper.text()).toContain('Live event received')
  })

  it('skips a live entry whose logger/message does not match text filter', async () => {
    const { wrapper } = await mountLog({ listData: [] })

    await wrapper.find('[data-testid="input-filter"]').setValue('auth')

    capturedLogCb(makeEntry({ logger: 'app.router', message: 'Route geladen' }))
    await nextTick()

    expect(wrapper.findAll('[data-testid="log-entry"]').length).toBe(0)
  })

  it('skips a live entry whose level does not match level filter', async () => {
    const { wrapper } = await mountLog({ listData: [] })

    const selects = wrapper.findAll('select')
    await selects[1].setValue('ERROR')
    await flushPromises()

    capturedLogCb(makeEntry({ level: 'INFO', message: 'Not an error' }))
    await nextTick()

    expect(wrapper.findAll('[data-testid="log-entry"]').length).toBe(0)
  })
})

// ─── Unmount ─────────────────────────────────────────────────────────────────

describe('LogView — unmount', () => {
  it('calls the ws unregister function on unmount', async () => {
    const { wrapper } = await mountLog()
    wrapper.unmount()
    expect(wsUnregMock).toHaveBeenCalled()
  })
})

// ─── levelVariant ────────────────────────────────────────────────────────────

describe('LogView — levelVariant', () => {
  it('assigns correct badge variant for every log level', async () => {
    const entries = [
      makeEntry({ level: 'DEBUG',    message: 'd' }),
      makeEntry({ level: 'INFO',     message: 'i' }),
      makeEntry({ level: 'WARNING',  message: 'w' }),
      makeEntry({ level: 'ERROR',    message: 'e' }),
      makeEntry({ level: 'CRITICAL', message: 'c' }),
    ]
    const { wrapper } = await mountLog({ listData: entries })
    const html = wrapper.html()

    expect(html).toContain('badge-muted')    // DEBUG
    expect(html).toContain('badge-info')     // INFO
    expect(html).toContain('badge-warning')  // WARNING
    expect(html).toContain('badge-danger')   // ERROR + CRITICAL
  })
})
