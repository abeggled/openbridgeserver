import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const search = vi.fn()
beforeEach(() => {
  vi.resetModules()
  search.mockResolvedValue({ data: { items: [{ id: 'dp-1', name: 'Lamp', data_type: 'bool' }] } })
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search }, securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})
afterEach(() => vi.doUnmock('@/api/client'))

async function mountPanel(data = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth'); useAuthStore().user = { id: 'u', is_admin: true }
  const { default: Panel } = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(Panel, { props: { node: { id: 'n1', type: 'value_sequence', data }, nodeTypes: [{ type: 'value_sequence', label: 'Sequence' }], nodeOutputs: {} }, global: { plugins: [pinia] } })
}

describe('NodeConfigPanel value sequence', () => {
  it('adds, duplicates, reorders, and removes GUI sequence steps', async () => {
    const w = await mountPanel()
    await w.get('[data-testid="sequence-add"]').trigger('click')
    await flushPromises()
    expect(w.findAll('[data-testid^="sequence-step-"]')).toHaveLength(1)
    const buttons = w.get('[data-testid="sequence-step-0"]').findAll('button')
    await buttons[2].trigger('click')
    await flushPromises()
    expect(w.findAll('[data-testid^="sequence-step-"]')).toHaveLength(2)
    await w.get('[data-testid="sequence-step-0"]').findAll('button')[3].trigger('click')
    expect(w.findAll('[data-testid^="sequence-step-"]')).toHaveLength(1)
    w.unmount()
  })

  it('applies the blink preset and picks a target object', async () => {
    const w = await mountPanel()
    await w.get('[data-testid="sequence-blink"]').trigger('click')
    await flushPromises()
    expect(w.findAll('[data-testid^="sequence-step-"]')).toHaveLength(2)
    const input = w.get('[data-testid="sequence-step-0"]').find('input')
    await input.setValue('Lamp')
    await flushPromises()
    await w.get('[data-testid="sequence-step-0"]').findAll('button').at(-1).trigger('click')
    const update = w.emitted('update').at(-1)[0]
    expect(update.steps[0]).toMatchObject({ datapoint_id: 'dp-1', datapoint_name: 'Lamp', value: true, delay_ms: 500 })
    w.unmount()
  })
})
