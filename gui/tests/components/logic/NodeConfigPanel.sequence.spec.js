import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const search = vi.fn()
const list = vi.fn()
beforeEach(() => {
  vi.resetModules()
  search.mockResolvedValue({ data: { items: [{ id: 'dp-1', name: 'Lamp', data_type: 'bool' }] } })
  list.mockResolvedValue({ data: { items: [] } })
  vi.doMock('@/api/client', () => ({
    dpApi: { list },
    searchApi: { search }, securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
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
    await w.get('[data-testid="sequence-step-0"]').findAll('button')[1].trigger('click')
    await flushPromises()
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

  it('keeps a sequence object search query visible while results update', async () => {
    const w = await mountPanel({ steps: [{ datapoint_id: '', datapoint_name: '', value: '', delay_ms: 0 }] })
    const input = w.get('[data-testid="sequence-step-0"]').find('input')
    await input.setValue('Lamp')
    await flushPromises()
    expect(input.element.value).toBe('Lamp')
    expect(search).toHaveBeenCalledWith({ q: 'Lamp', size: 50 })
    w.unmount()
  })

  it('clears the old target when a selected object search is edited', async () => {
    const w = await mountPanel({ steps: [{ datapoint_id: 'dp-1', datapoint_name: 'Lamp', value: true, delay_ms: 0 }] })
    const input = w.get('[data-testid="sequence-step-0"]').find('input')
    await input.setValue('Other Lamp')
    await flushPromises()
    const update = w.emitted('update').at(-1)[0]
    expect(update.steps[0]).toMatchObject({ datapoint_id: '', datapoint_name: '' })
    await w.setProps({ node: { id: 'n1', type: 'value_sequence', data: update } })
    await flushPromises()
    expect(w.get('[data-testid="sequence-step-0"]').find('input').element.value).toBe('Other Lamp')
    w.unmount()
  })

  it('normalises saved JSON steps and keeps edited step values', async () => {
    const w = await mountPanel()
    await w.setProps({ node: { id: 'n1', type: 'value_sequence', data: { steps: JSON.stringify([{ datapoint_id: 'dp-1', datapoint_name: 'Lamp', value: 'off', delay_ms: 0 }]) } } })
    await flushPromises()
    const inputs = w.get('[data-testid="sequence-step-0"]').findAll('input')
    await inputs[1].setValue('on')
    await inputs[2].setValue(250)
    await flushPromises()

    const update = w.emitted('update').at(-1)[0]
    expect(update.steps[0]).toMatchObject({ datapoint_id: 'dp-1', value: 'on', delay_ms: 250 })
    w.unmount()
  })

  it('handles malformed steps and empty or failed object searches', async () => {
    const w = await mountPanel()
    await w.setProps({ node: { id: 'n1', type: 'value_sequence', data: { steps: 'not valid JSON' } } })
    await flushPromises()
    expect(w.findAll('[data-testid^="sequence-step-"]')).toHaveLength(0)
    await w.get('[data-testid="sequence-add"]').trigger('click')
    const input = w.get('[data-testid="sequence-step-0"]').find('input')
    await input.setValue('')
    await flushPromises()
    expect(list).toHaveBeenCalledWith(0, 50)

    search.mockRejectedValueOnce(new Error('search unavailable'))
    await input.setValue('Lamp')
    await flushPromises()
    expect(w.get('[data-testid="sequence-step-0"]').findAll('button')).toHaveLength(4)
    w.unmount()
  })
})
