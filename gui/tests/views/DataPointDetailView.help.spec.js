/**
 * Integrated help drawer wiring on the DataPointDetailView (issue #1198):
 * the detail page had no HelpButton anywhere. Each card/section got its own
 * button (matching how every other view in the app places help per section
 * rather than once for the whole page) — see also
 * DataPointHierarchyCard.spec.js for the hierarchy card's button, which
 * lives in a separate component.
 */
import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const apiMocks = vi.hoisted(() => ({
  dpApi: {
    get: vi.fn(),
    listBindings: vi.fn(),
    knxContext: vi.fn(),
    writeValue: vi.fn(),
  },
  logicApi: {
    datapointUsages: vi.fn(),
  },
  systemApi: {
    datatypes: vi.fn(),
  },
  helpApi: {
    index: vi.fn(),
  },
}))

vi.mock('@/api/client', () => apiMocks)

function mountView() {
  return mount(DataPointDetailView, {
    props: { id: 'dp-internal' },
    global: {
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        DataPointHierarchyCard: { template: '<div />' },
        DataPointForm: { template: '<div />' },
        BindingForm: { template: '<div />' },
        Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] },
        ConfirmDialog: { template: '<div />' },
      },
    },
  })
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('DataPointDetailView — per-section help buttons', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.systemApi.datatypes.mockResolvedValue({ data: [{ name: 'FLOAT' }] })
    apiMocks.dpApi.get.mockResolvedValue({
      data: {
        id: 'dp-internal',
        name: 'Internal Temperature',
        data_type: 'FLOAT',
        unit: '°C',
        tags: [],
        mqtt_topic: 'dp/dp-internal/value',
        mqtt_alias: null,
        persist_value: true,
        record_history: true,
        value: null,
        quality: 'uncertain',
        created_at: '2026-06-11T10:00:00+00:00',
        updated_at: '2026-06-11T10:00:00+00:00',
      },
    })
    apiMocks.dpApi.listBindings.mockResolvedValue({ data: [] })
    apiMocks.dpApi.knxContext.mockResolvedValue({ data: { datapoint_id: 'dp-internal', group_addresses: [] } })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
    apiMocks.helpApi.index.mockResolvedValue({ data: { helpIds: {} } })
  })

  it.each([
    'datapoints-detail',
    'datapoints-detail-properties',
    'datapoints-detail-bindings',
    'datapoints-detail-logic',
  ])('renders a help button for %s', async (helpId) => {
    const wrapper = mountView()
    await flushPromises()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each([
    'datapoints-detail',
    'datapoints-detail-properties',
    'datapoints-detail-bindings',
    'datapoints-detail-logic',
  ])('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = mountView()
    await flushPromises()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()
    await helpButton(wrapper, helpId).trigger('click')
    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
