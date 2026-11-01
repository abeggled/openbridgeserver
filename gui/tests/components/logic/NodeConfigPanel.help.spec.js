/**
 * Integrated help drawer wiring on the Logikmodul block-config panel.
 *
 * The header's HelpButton opens the block's own help (its help_id, the same
 * as the palette entry, #1411). A block type without a help_id falls back to
 * the general logic-node-config section in help/logic/overview.md. This spec
 * checks both cases and that clicking opens the real help store.
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

async function mountPanel({ helpId } = {}) {
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
        ...(helpId ? { help_id: helpId } : {}),
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
  it('falls back to logic-node-config for a block type without a help_id', async () => {
    const w = await mountPanel()
    expect(w.find('[data-testid="help-button-logic-node-config"]').exists()).toBe(true)
  })

  it('opens the help store with logic-node-config when the block has no help_id', async () => {
    const w = await mountPanel()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await w.find('[data-testid="help-button-logic-node-config"]').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('logic-node-config')
  })

  it("opens the block's own help when its type declares a help_id (#1411)", async () => {
    const w = await mountPanel({ helpId: 'logic-block-clamp' })
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    expect(w.find('[data-testid="help-button-logic-node-config"]').exists()).toBe(false)
    await w.find('[data-testid="help-button-logic-block-clamp"]').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('logic-block-clamp')
  })

  it('still emits close when the close button (not the help button) is clicked', async () => {
    const w = await mountPanel()
    await w.get('button.btn-icon').trigger('click')
    expect(w.emitted('close')).toBeTruthy()
  })
})
