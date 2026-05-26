/**
 * Tests for DpCombobox.vue.
 *
 * After the FE-05 refactor the component is a wrapper around the generic
 * Combobox.vue. Its public API must stay compatible with #429-era callers:
 *   - modelValue: string (DP id)
 *   - displayName: string (label shown when an item is selected)
 *   - emits update:modelValue (string) and select (item | null)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountDpCombobox(props = {}, { searchResult = [] } = {}) {
  const searchApi = {
    search: vi.fn().mockResolvedValue({ data: { items: searchResult } }),
  }
  vi.doMock('@/api/client', () => ({ searchApi }))
  const mod = await import('@/components/ui/DpCombobox.vue')
  const DpCombobox = mod.default
  const wrapper = mount(DpCombobox, { props, attachTo: document.body })
  await flushPromises()
  return { wrapper, searchApi }
}

describe('DpCombobox', () => {
  it('mounts with empty state', async () => {
    const { wrapper } = await mountDpCombobox({ modelValue: '', displayName: '' })
    expect(wrapper.find('input').exists()).toBe(true)
  })

  it('emits a string id on selection (not an array)', async () => {
    const items = [
      { id: 'dp-1', name: 'Temperatur', data_type: 'float', unit: '°C' },
      { id: 'dp-2', name: 'Schalter', data_type: 'bool' },
    ]
    const { wrapper, searchApi } = await mountDpCombobox(
      { modelValue: '', displayName: '' },
      { searchResult: items },
    )
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(searchApi.search).toHaveBeenCalled()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')

    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(typeof last).toBe('string')
    expect(last).toBe('dp-1')

    const selectEvents = wrapper.emitted('select')
    expect(selectEvents).toBeTruthy()
    expect(selectEvents[selectEvents.length - 1][0]).toMatchObject({ id: 'dp-1', name: 'Temperatur' })
  })

  it('shows the displayName when provided', async () => {
    const { wrapper } = await mountDpCombobox({ modelValue: 'dp-1', displayName: 'Vorbelegt' })
    expect(wrapper.find('input').element.value).toBe('Vorbelegt')
  })

  it('renders no chips (single mode)', async () => {
    const { wrapper } = await mountDpCombobox({ modelValue: 'dp-1', displayName: 'X' })
    expect(wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])').length).toBe(0)
  })

  it('emits select(null) on clear', async () => {
    const items = [{ id: 'dp-1', name: 'T', data_type: 'float' }]
    const { wrapper } = await mountDpCombobox(
      { modelValue: 'dp-1', displayName: 'T' },
      { searchResult: items },
    )
    const btn = wrapper.find('[data-testid="combobox-clear"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    await flushPromises()
    const selEvents = wrapper.emitted('select')
    expect(selEvents).toBeTruthy()
    expect(selEvents[selEvents.length - 1][0]).toBeNull()
  })

  it('handles a rejected search call by showing empty state', async () => {
    const searchApi = { search: vi.fn().mockRejectedValue(new Error('boom')) }
    vi.doMock('@/api/client', () => ({ searchApi }))
    const mod = await import('@/components/ui/DpCombobox.vue')
    const wrapper = mount(mod.default, {
      props: { modelValue: '', displayName: '' },
      attachTo: document.body,
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })

  it('updates the displayName watcher when both id+name change', async () => {
    const items = [{ id: 'dp-2', name: 'Two', data_type: 'int' }]
    const { wrapper } = await mountDpCombobox(
      { modelValue: '', displayName: '' },
      { searchResult: items },
    )
    await wrapper.setProps({ modelValue: 'dp-2', displayName: 'Two' })
    await flushPromises()
    expect(wrapper.find('input').element.value).toBe('Two')
  })

  it('passes empty array as items when search returns plain data', async () => {
    const searchApi = { search: vi.fn().mockResolvedValue({ data: [] }) }
    vi.doMock('@/api/client', () => ({ searchApi }))
    const mod = await import('@/components/ui/DpCombobox.vue')
    const wrapper = mount(mod.default, {
      props: { modelValue: '', displayName: '' },
      attachTo: document.body,
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })
})
