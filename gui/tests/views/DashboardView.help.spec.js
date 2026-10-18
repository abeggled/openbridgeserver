/**
 * Integrated help drawer wiring on the Dashboard/"Übersicht" view (#896).
 *
 * Each of the four stat cards, the active-warnings card, the adapter-status
 * card, and the live-values card got a HelpButton pointing at a help_id
 * documented in help/dashboard/overview.md. This spec checks the buttons are
 * present with the right help_id and that clicking one opens the real help
 * store. The stat cards' own HelpButton wiring (the helpId prop on
 * StatCard.vue) is unit-tested directly in StatCard.spec.js — this file only
 * checks DashboardView passes the right id to each instance.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const wsSubscribeMock = vi.fn()
const wsUnsubMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  wsSubscribeMock.mockReset()
  wsUnsubMock.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/websocket')
})

function makeAdapter(overrides = {}) {
  return {
    id: 1,
    adapter_type: 'KNX',
    name: 'KNX Main',
    running: true,
    connected: true,
    severity: 'warning',
    bindings: 5,
    status_detail: '',
    status_detail_code: null,
    status_detail_params: {},
    ...overrides,
  }
}

async function mountDashboard({ adapters = [] } = {}) {
  vi.doMock('@/api/client', () => ({
    systemApi:  { health: vi.fn().mockResolvedValue({ data: { status: 'ok', datapoints: 1, adapters_running: 1 } }) },
    searchApi:  { search: vi.fn().mockResolvedValue({ data: { items: [], total: 0, pages: 1 } }) },
    dpApi:      { listAll: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    adapterApi: { listInstances: vi.fn().mockResolvedValue({ data: adapters }), list: vi.fn().mockResolvedValue({ data: [] }) },
    authApi:    { login: vi.fn(), me: vi.fn() },
    settingsApi: { get: vi.fn().mockResolvedValue({ data: {} }) },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    ringbufferApi: { stats: vi.fn().mockResolvedValue({ data: { enabled: false } }) },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({
      connected: true,
      liveValues: {},
      subscribe: wsSubscribeMock,
      onValue: (fn) => wsUnsubMock,
    }),
  }))

  const { default: DashboardView } = await import('@/views/DashboardView.vue')
  const wrapper = mount(DashboardView, {
    global: {
      stubs: {
        RouterLink: { template: '<a href="#"><slot /></a>' },
        Spinner:    { template: '<span class="spinner" />' },
        Badge:      { template: '<span><slot /></span>' },
        RingBufferCard: { template: '<div />' },
      },
    },
  })
  await flushPromises()
  return wrapper
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('DashboardView — help buttons (#896)', () => {
  it.each([
    'dashboard-stats-datapoints',
    'dashboard-stats-adapters',
    'dashboard-stats-wsstatus',
    'dashboard-stats-server',
  ])('renders a help button for the %s stat card', async (helpId) => {
    const wrapper = await mountDashboard()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it('renders a help button for the active-warnings card when it is visible', async () => {
    const wrapper = await mountDashboard({ adapters: [makeAdapter()] })
    expect(helpButton(wrapper, 'dashboard-warnings').exists()).toBe(true)
  })

  it('renders a help button for the adapter-status card', async () => {
    const wrapper = await mountDashboard({ adapters: [makeAdapter({ severity: 'ok' })] })
    expect(helpButton(wrapper, 'dashboard-adapters').exists()).toBe(true)
  })

  it('renders a help button for the live-values card', async () => {
    const wrapper = await mountDashboard()
    expect(helpButton(wrapper, 'dashboard-values').exists()).toBe(true)
  })

  it.each([
    'dashboard-stats-datapoints',
    'dashboard-stats-adapters',
    'dashboard-stats-wsstatus',
    'dashboard-stats-server',
    'dashboard-adapters',
    'dashboard-values',
  ])('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = await mountDashboard()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })

  it('opens the help store with dashboard-warnings when its button is clicked', async () => {
    const wrapper = await mountDashboard({ adapters: [makeAdapter()] })
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, 'dashboard-warnings').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('dashboard-warnings')
  })
})
