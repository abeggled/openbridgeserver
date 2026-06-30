import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Capture the ws.onValue callback so tests can simulate live events
let capturedOnValueCb = null
const wsSubscribeMock = vi.fn()
const wsUnsubMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  capturedOnValueCb = null
  wsSubscribeMock.mockReset()
  wsUnsubMock.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/websocket')
})

function makeDP(overrides = {}) {
  return {
    id: 'dp-1',
    name: 'Temperature',
    mqtt_topic: 'obs/dp-1',
    value: null,
    quality: null,
    unit: '',
    ...overrides,
  }
}

function makeAdapter(overrides = {}) {
  return {
    id: 1,
    adapter_type: 'KNX',
    name: 'KNX Main',
    running: true,
    connected: true,
    severity: 'ok',
    bindings: 5,
    status_detail: '',
    status_detail_code: null,
    status_detail_params: {},
    ...overrides,
  }
}

async function mountDashboard({
  health     = { status: 'ok', datapoints: 10, adapters_running: 2 },
  dps        = [],
  adapters   = [],
  wsConnected = true,
  liveValues  = {},
} = {}) {
  vi.doMock('@/api/client', () => ({
    systemApi:  { health: vi.fn().mockResolvedValue({ data: health }) },
    searchApi:  { search: vi.fn().mockResolvedValue({ data: { items: dps, total: dps.length, pages: 1 } }) },
    dpApi:      { listAll: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: adapters }),
      list: vi.fn().mockResolvedValue({ data: [] }),
    },
    authApi:    { login: vi.fn(), me: vi.fn() },
    settingsApi: { get: vi.fn().mockResolvedValue({ data: {} }) },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({
      connected: wsConnected,
      liveValues,
      subscribe: wsSubscribeMock,
      onValue: (fn) => { capturedOnValueCb = fn; return wsUnsubMock },
    }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: DashboardView } = await import('@/views/DashboardView.vue')
  const wrapper = mount(DashboardView, {
    global: {
      plugins: [pinia],
      stubs: {
        RouterLink: { template: '<a href="#"><slot /></a>' },
        Spinner:    { template: '<span class="spinner" />' },
        Badge: {
          template: '<span :class="`badge-${variant}`"><slot /></span>',
          props: ['variant', 'size', 'dot'],
        },
        StatCard: {
          template: '<div class="stat-card" :data-label="label" :data-value="String(value)" :data-color="color" />',
          props: ['label', 'value', 'icon', 'color'],
        },
      },
    },
  })
  await flushPromises()
  return { wrapper }
}

// ─── Mount behaviour ────────────────────────────────────────────────────────

describe('DashboardView — mount', () => {
  it('calls ws.subscribe with all datapoint ids', async () => {
    const dps = [makeDP({ id: 'a' }), makeDP({ id: 'b' })]
    await mountDashboard({ dps })
    expect(wsSubscribeMock).toHaveBeenCalledWith(['a', 'b'])
  })

  it('registers an onValue handler', async () => {
    await mountDashboard()
    expect(capturedOnValueCb).toBeTypeOf('function')
  })

  it('calls the unsubscribe fn when unmounted', async () => {
    const { wrapper } = await mountDashboard()
    wrapper.unmount()
    expect(wsUnsubMock).toHaveBeenCalled()
  })
})

// ─── Stat cards ─────────────────────────────────────────────────────────────

describe('DashboardView — stat cards', () => {
  it('shows health.datapoints count in first card', async () => {
    const { wrapper } = await mountDashboard({ health: { status: 'ok', datapoints: 42, adapters_running: 1 } })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[0].attributes('data-value')).toBe('42')
  })

  it('shows health.adapters_running in second card', async () => {
    const { wrapper } = await mountDashboard({ health: { status: 'ok', datapoints: 0, adapters_running: 3 } })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[1].attributes('data-value')).toBe('3')
  })

  it('WS stat card is green and shows live when connected', async () => {
    const { wrapper } = await mountDashboard({ wsConnected: true })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[2].attributes('data-color')).toBe('green')
  })

  it('WS stat card is red when disconnected', async () => {
    const { wrapper } = await mountDashboard({ wsConnected: false })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[2].attributes('data-color')).toBe('red')
  })

  it('server stat card is green when health.status=ok', async () => {
    const { wrapper } = await mountDashboard({ health: { status: 'ok', datapoints: 0, adapters_running: 0 } })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[3].attributes('data-color')).toBe('green')
  })

  it('server stat card is red when health.status!=ok', async () => {
    const { wrapper } = await mountDashboard({ health: { status: 'error', datapoints: 0, adapters_running: 0 } })
    const cards = wrapper.findAll('.stat-card')
    expect(cards[3].attributes('data-color')).toBe('red')
  })
})

// ─── Adapter issues ──────────────────────────────────────────────────────────

describe('DashboardView — adapter issues', () => {
  it('hides the issues section when all adapters are ok', async () => {
    const { wrapper } = await mountDashboard({ adapters: [makeAdapter({ severity: 'ok' })] })
    expect(wrapper.find('[data-testid="dashboard-adapter-issues"]').exists()).toBe(false)
  })

  it('shows the issues section when an adapter has severity=warning', async () => {
    const { wrapper } = await mountDashboard({
      adapters: [makeAdapter({ severity: 'warning', status_detail: 'overload' })],
    })
    expect(wrapper.find('[data-testid="dashboard-adapter-issues"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('KNX Main')
  })

  it('shows the issues section when an adapter has severity=error', async () => {
    const { wrapper } = await mountDashboard({
      adapters: [makeAdapter({ severity: 'error', status_detail: 'connection refused' })],
    })
    expect(wrapper.find('[data-testid="dashboard-adapter-issues"]').exists()).toBe(true)
  })

  it('uses red left border when any issue is an error', async () => {
    const { wrapper } = await mountDashboard({
      adapters: [makeAdapter({ severity: 'error' })],
    })
    const panel = wrapper.find('[data-testid="dashboard-adapter-issues"]')
    expect(panel.classes()).toContain('border-red-500')
  })

  it('uses amber left border when issues are only warnings', async () => {
    const { wrapper } = await mountDashboard({
      adapters: [makeAdapter({ severity: 'warning' })],
    })
    const panel = wrapper.find('[data-testid="dashboard-adapter-issues"]')
    expect(panel.classes()).toContain('border-amber-500')
  })
})

// ─── Live values section ─────────────────────────────────────────────────────

describe('DashboardView — live values list', () => {
  it('shows empty-state message when no datapoints', async () => {
    const { wrapper } = await mountDashboard({ dps: [] })
    expect(wrapper.text()).toContain('Keine Objekte vorhanden')
  })

  it('renders datapoint names from the store', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ id: 'a', name: 'Temp Wohnzimmer' })] })
    expect(wrapper.text()).toContain('Temp Wohnzimmer')
  })

  it('displays "—" for a datapoint with null value', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ value: null })] })
    expect(wrapper.text()).toContain('—')
  })

  it('displays boolean value as "true" string', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ value: true })] })
    expect(wrapper.text()).toContain('true')
  })

  it('appends unit when dp.unit is set', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ value: 22, unit: '°C' })] })
    expect(wrapper.text()).toContain('22 °C')
  })

  it('shows live value from ws.liveValues when available', async () => {
    const dp = makeDP({ id: 'dp-x', value: 10, unit: '' })
    const { wrapper } = await mountDashboard({
      dps: [dp],
      liveValues: { 'dp-x': { value: 99, quality: 'good', ts: '' } },
    })
    expect(wrapper.text()).toContain('99')
    expect(wrapper.text()).not.toContain('10')
  })

  it('quality=good badge has success variant', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ quality: 'good' })] })
    expect(wrapper.html()).toContain('badge-success')
  })

  it('quality=bad badge has danger variant', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ quality: 'bad' })] })
    expect(wrapper.html()).toContain('badge-danger')
  })

  it('quality=uncertain badge has warning variant', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ quality: 'uncertain' })] })
    expect(wrapper.html()).toContain('badge-warning')
  })

  it('null quality badge has muted variant', async () => {
    const { wrapper } = await mountDashboard({ dps: [makeDP({ quality: null })] })
    expect(wrapper.html()).toContain('badge-muted')
  })
})

// ─── onValue callback ────────────────────────────────────────────────────────

describe('DashboardView — ws.onValue integration', () => {
  it('onValue callback patches the datapoints store', async () => {
    const dp = makeDP({ id: 'dp-1', value: 1 })
    await mountDashboard({ dps: [dp] })

    const { useDatapointStore } = await import('@/stores/datapoints')
    const dpStore = useDatapointStore()
    const spy = vi.spyOn(dpStore, 'patchValue')

    capturedOnValueCb('dp-1', 99, 'good')

    expect(spy).toHaveBeenCalledWith('dp-1', 99, 'good')
  })
})

// ─── Adapter status list ─────────────────────────────────────────────────────

describe('DashboardView — adapter status list', () => {
  it('shows "no adapters" message when adapter list is empty', async () => {
    const { wrapper } = await mountDashboard({ adapters: [] })
    expect(wrapper.text()).toContain('Keine Adapter konfiguriert')
  })

  it('renders adapter_type and bindings count for each adapter', async () => {
    const { wrapper } = await mountDashboard({
      adapters: [makeAdapter({ adapter_type: 'KNX', bindings: 12 })],
    })
    expect(wrapper.text()).toContain('KNX')
    expect(wrapper.text()).toContain('12')
  })
})
