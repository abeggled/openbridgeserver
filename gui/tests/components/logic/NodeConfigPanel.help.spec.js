/**
 * Integrated help drawer wiring on the Logikmodul block-config panel.
 *
 * The header got a HelpButton pointing at logic-node-config, documented in
 * help/logic/overview.md. This spec checks the button is present and that
 * clicking it opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    helpApi:     { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  const w = mount(mod.default, {
    props: {
      node: { id: 'clamp-1', type: 'clamp', data: { min: 0, max: 100 } },
      nodeTypes: [{
        type: 'clamp',
        label: 'Limiter',
        description: '',
        config_schema: { min: { type: 'number', default: 0 }, max: { type: 'number', default: 100 } },
      }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await flushPromises()
  return w
}

describe('NodeConfigPanel — help button', () => {
  it('renders a help button in the header, next to the close button', async () => {
    const w = await mountPanel()
    expect(w.find('[data-testid="help-button-logic-node-config"]').exists()).toBe(true)
  })

  it('opens the help store with logic-node-config when clicked', async () => {
    const w = await mountPanel()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await w.find('[data-testid="help-button-logic-node-config"]').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('logic-node-config')
  })

  it('still emits close when the close button (not the help button) is clicked', async () => {
    const w = await mountPanel()
    await w.get('button.btn-icon').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })
})
