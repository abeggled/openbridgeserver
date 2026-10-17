/**
 * Integrated help drawer wiring on DataPointDetailView's edit modal (#1197):
 * the "Edit data point" dialog had no HelpButton, unlike the rest of the
 * app's forms. This spec checks the button is present in the modal header
 * and that clicking it opens the real help store.
 */
import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
        ConfirmDialog: { template: '<div />' },
      },
    },
    attachTo: document.body,
  })
}

// Modal.vue teleports its content to document.body, outside the wrapper's
// own DOM subtree — vue-test-utils' wrapper.find() doesn't traverse into a
// real (unstubbed) Teleport target, so query the attached document instead.
function helpButton(_wrapper, helpId) {
  const el = document.querySelector(`[data-testid="help-button-${helpId}"]`)
  return {
    exists: () => !!el,
    trigger: (event) => {
      el.dispatchEvent(new MouseEvent(event, { bubbles: true }))
      return flushPromises()
    },
  }
}

describe('DataPointDetailView — edit modal help button', () => {
  let wrapper

  afterEach(() => {
    // Modal.vue teleports to document.body; without unmounting, a stale
    // help button from a previous test's still-mounted instance (and its
    // own pinia) would shadow the one under test via a global querySelector.
    wrapper?.unmount()
    wrapper = undefined
  })

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

  it('renders a help button in the edit modal once opened', async () => {
    wrapper = mountView()
    await flushPromises()
    expect(helpButton(wrapper, 'datapoints-form').exists()).toBe(false)

    const editButton = wrapper.findAll('button').find(button => button.text() === 'Bearbeiten')
    await editButton.trigger('click')
    await flushPromises()

    expect(helpButton(wrapper, 'datapoints-form').exists()).toBe(true)
  })

  it('opens the help store with datapoints-form when its button is clicked', async () => {
    wrapper = mountView()
    await flushPromises()
    const editButton = wrapper.findAll('button').find(button => button.text() === 'Bearbeiten')
    await editButton.trigger('click')
    await flushPromises()

    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()
    await helpButton(wrapper, 'datapoints-form').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('datapoints-form')
  })
})
