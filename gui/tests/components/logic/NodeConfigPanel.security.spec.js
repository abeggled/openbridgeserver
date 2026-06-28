import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let checkUrlTarget
let addUrlTarget
let searchDatapoints
let listDatapoints

beforeEach(() => {
  vi.resetModules()
  checkUrlTarget = vi.fn()
  addUrlTarget = vi.fn().mockResolvedValue({ data: { target: '10.38.113.23/32' } })
  searchDatapoints = vi.fn().mockResolvedValue({ data: { items: [] } })
  listDatapoints = vi.fn().mockResolvedValue({ data: { items: [] } })
  vi.doMock('@/api/client', () => ({
    dpApi: { list: listDatapoints },
    searchApi: { search: searchDatapoints },
    securityApi: { checkUrlTarget, addUrlTarget },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountApiClientPanel(data = { url: 'internal.example/api/v1/status', auth_type: 'none' }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'ac',
        type: 'api_client',
        data,
      },
      nodeTypes: [{ type: 'api_client', label: 'API Client', description: 'HTTP client' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel api_client URL target policy', () => {
  it('shows a blocked target warning and lets admins allow the suggested target', async () => {
    checkUrlTarget
      .mockResolvedValueOnce({
        data: {
          allowed: false,
          url: 'http://internal.example/api/v1/status',
          host: 'internal.example',
          resolved_ips: ['10.38.113.23'],
          blocked_ips: ['10.38.113.23'],
          reason: 'URL target resolves to an internal address',
          suggested_target: '10.38.113.23/32',
        },
      })
      .mockResolvedValueOnce({
        data: {
          allowed: true,
          url: 'http://internal.example/api/v1/status',
          host: 'internal.example',
          resolved_ips: ['10.38.113.23'],
          blocked_ips: [],
          reason: 'URL target is allowed',
          allowlisted_by: '10.38.113.23/32',
        },
      })

    const wrapper = await mountApiClientPanel()
    await wrapper.find('[data-testid="api-client-check-target"]').trigger('click')
    await flushPromises()

    expect(checkUrlTarget).toHaveBeenCalledWith({
      url: 'http://internal.example/api/v1/status',
    })
    expect(wrapper.emitted('update').at(-1)[0].url).toBe('http://internal.example/api/v1/status')
    expect(wrapper.text()).toContain('Dieses Ziel wird blockiert')
    expect(wrapper.text()).toContain('10.38.113.23/32')

    await wrapper.find('[data-testid="api-client-allow-target"]').trigger('click')
    await flushPromises()

    expect(addUrlTarget).toHaveBeenCalledWith({
      target: '10.38.113.23/32',
      reason: 'Freigabe aus API-Client-Konfiguration',
    })
    expect(checkUrlTarget).toHaveBeenLastCalledWith({
      url: 'http://internal.example/api/v1/status',
    })
    expect(wrapper.text()).toContain('Ziel ist erlaubt')
    wrapper.unmount()
  })

  it('adds api_client variables from datapoint search', async () => {
    searchDatapoints.mockResolvedValueOnce({
      data: {
        items: [{ id: 'dp-1', name: 'Device ID', data_type: 'STRING' }],
      },
    })

    const wrapper = await mountApiClientPanel()
    await wrapper.find('[data-testid="api-client-add-variable"]').trigger('click')
    await wrapper.find('[data-testid="api-client-variable-search-0"]').setValue('Device')
    await flushPromises()

    await wrapper.find('[data-testid="api-client-variable-result-0"]').trigger('click')

    expect(searchDatapoints).toHaveBeenCalledWith({ q: 'Device', size: 50 })
    expect(wrapper.text()).toContain('###OBS1###')
    expect(wrapper.text()).toContain('Device ID')
    expect(wrapper.emitted('update').at(-1)[0].variables).toEqual([
      { slot: 1, datapoint_id: 'dp-1', datapoint_name: 'Device ID' },
    ])
    wrapper.unmount()
  })

  it('keeps api_client variable slots stable when deleting an earlier variable', async () => {
    const wrapper = await mountApiClientPanel({
      url: 'http://example.com/api/###OBS2###',
      auth_type: 'none',
      variables: [
        { slot: 1, datapoint_id: 'dp-1', datapoint_name: 'First' },
        { slot: 2, datapoint_id: 'dp-2', datapoint_name: 'Second' },
      ],
    })

    expect(wrapper.text()).toContain('###OBS1###')
    expect(wrapper.text()).toContain('###OBS2###')

    await wrapper.find('[data-testid="api-client-variable-remove-0"]').trigger('click')

    const remainingVariable = wrapper.find('[data-testid="api-client-variable-0"]')
    expect(remainingVariable.text()).not.toContain('###OBS1###')
    expect(remainingVariable.text()).toContain('###OBS2###')
    expect(wrapper.emitted('update').at(-1)[0].variables).toEqual([
      { slot: 2, datapoint_id: 'dp-2', datapoint_name: 'Second' },
    ])
    wrapper.unmount()
  })

  it('normalises api_client variables from JSON string props', async () => {
    const wrapper = await mountApiClientPanel({
      url: 'http://example.com/api/###OBS4###',
      auth_type: 'none',
      variables: JSON.stringify([
        { slot: '4', datapoint_id: 'dp-4', datapoint_name: 'Fourth' },
      ]),
    })

    const variable = wrapper.find('[data-testid="api-client-variable-0"]')
    expect(variable.text()).toContain('###OBS4###')
    expect(wrapper.find('[data-testid="api-client-variable-search-0"]').element.value).toBe('Fourth')

    await wrapper.find('[data-testid="api-client-add-variable"]').trigger('click')

    expect(wrapper.emitted('update').at(-1)[0].variables).toEqual([
      { slot: 4, datapoint_id: 'dp-4', datapoint_name: 'Fourth' },
      { slot: 5, datapoint_id: '', datapoint_name: '' },
    ])
    wrapper.unmount()
  })

  it('uses the datapoint list endpoint for empty api_client variable searches', async () => {
    listDatapoints.mockResolvedValueOnce({
      data: [{ id: 'dp-list', name: 'Listed Object', data_type: 'BOOL' }],
    })

    const wrapper = await mountApiClientPanel()
    await wrapper.find('[data-testid="api-client-add-variable"]').trigger('click')
    await wrapper.find('[data-testid="api-client-variable-search-0"]').setValue('')
    await flushPromises()

    expect(listDatapoints).toHaveBeenCalledWith(0, 50)
    expect(wrapper.text()).toContain('Listed Object')
    wrapper.unmount()
  })

  it('clears api_client variable search results when search fails', async () => {
    searchDatapoints
      .mockResolvedValueOnce({ data: { items: [{ id: 'dp-ok', name: 'Found', data_type: 'FLOAT' }] } })
      .mockRejectedValueOnce(new Error('search failed'))

    const wrapper = await mountApiClientPanel()
    await wrapper.find('[data-testid="api-client-add-variable"]').trigger('click')
    await wrapper.find('[data-testid="api-client-variable-search-0"]').setValue('Found')
    await flushPromises()
    expect(wrapper.find('[data-testid="api-client-variable-result-0"]').exists()).toBe(true)

    await wrapper.find('[data-testid="api-client-variable-search-0"]').setValue('Broken')
    await flushPromises()

    expect(wrapper.find('[data-testid="api-client-variable-result-0"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
