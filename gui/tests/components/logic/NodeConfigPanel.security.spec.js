import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let checkUrlTarget
let addUrlTarget

beforeEach(() => {
  vi.resetModules()
  checkUrlTarget = vi.fn()
  addUrlTarget = vi.fn().mockResolvedValue({ data: { target: '10.38.113.23/32' } })
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget, addUrlTarget },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountApiClientPanel() {
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
        data: { url: 'internal.example/api/v1/status', auth_type: 'none' },
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
})
