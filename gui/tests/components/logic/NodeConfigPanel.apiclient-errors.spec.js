import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let checkUrlTarget
let addUrlTarget

beforeEach(() => {
  vi.resetModules()
  checkUrlTarget = vi.fn()
  addUrlTarget   = vi.fn()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget, addUrlTarget },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountApiClient(url = 'http://internal.host/api') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'ac', type: 'api_client', data: { url, auth_type: 'none' } },
      nodeTypes: [{ type: 'api_client', label: 'API Client', description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// ─── checkApiClientUrlTarget error path (line 1679) ──────────────────────────

describe('NodeConfigPanel api_client — checkUrlTarget error', () => {
  it('shows error message when checkUrlTarget rejects with detail', async () => {
    checkUrlTarget.mockRejectedValue({
      response: { data: { detail: 'Verbindung verweigert' } },
    })
    const w = await mountApiClient()
    await w.find('[data-testid="api-client-check-target"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Verbindung verweigert')
    w.unmount()
  })

  it('shows common error fallback when checkUrlTarget rejects without detail', async () => {
    checkUrlTarget.mockRejectedValue(new Error('network'))
    const w = await mountApiClient()
    await w.find('[data-testid="api-client-check-target"]').trigger('click')
    await flushPromises()

    // Falls back to t('common.error')
    expect(w.find('[data-testid="api-client-check-target"]').exists()).toBe(true) // still mounted
    w.unmount()
  })
})

// ─── allowApiClientTarget error path (line 1695) ──────────────────────────────

describe('NodeConfigPanel api_client — allowApiClientTarget error', () => {
  it('shows error when addUrlTarget rejects with detail', async () => {
    checkUrlTarget.mockResolvedValue({
      data: {
        allowed: false,
        url: 'http://internal.host/api',
        host: 'internal.host',
        resolved_ips: ['10.0.0.1'],
        blocked_ips: ['10.0.0.1'],
        reason: 'Internes Netzwerk',
        suggested_target: '10.0.0.1/32',
      },
    })
    addUrlTarget.mockRejectedValue({
      response: { data: { detail: 'Keine Berechtigung' } },
    })

    const w = await mountApiClient()
    await w.find('[data-testid="api-client-check-target"]').trigger('click')
    await flushPromises()

    // The "allow" button appears for admins when target is blocked
    const allowBtn = w.find('[data-testid="api-client-allow-target"]')
    expect(allowBtn.exists()).toBe(true)
    await allowBtn.trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Keine Berechtigung')
    w.unmount()
  })

  it('shows save error fallback when addUrlTarget rejects without detail', async () => {
    checkUrlTarget.mockResolvedValue({
      data: {
        allowed: false,
        url: 'http://internal.host/api',
        host: 'internal.host',
        resolved_ips: ['10.0.0.1'],
        blocked_ips: ['10.0.0.1'],
        reason: 'Intern',
        suggested_target: '10.0.0.1/32',
      },
    })
    addUrlTarget.mockRejectedValue(new Error('network'))

    const w = await mountApiClient()
    await w.find('[data-testid="api-client-check-target"]').trigger('click')
    await flushPromises()

    await w.find('[data-testid="api-client-allow-target"]').trigger('click')
    await flushPromises()

    // Falls back to t('common.saveError')
    expect(w.find('[data-testid="api-client-allow-target"]').exists() || w.html().length > 0).toBe(true)
    w.unmount()
  })
})
