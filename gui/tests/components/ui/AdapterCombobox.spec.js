/**
 * Tests for AdapterCombobox.vue.
 *
 * The combobox lists adapter TYPES that have at least one configured adapter
 * INSTANCE (derived from `adapterApi.listInstances()`, de-duplicated). A
 * filter set that references a no-longer-configured adapter still shows the
 * orphan in its chip strip with a strike-through hint, so users can decide
 * whether to remove or keep the criterion.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountAdapterCombobox(instances, props = {}) {
  const adapterApi = {
    listInstances: vi.fn().mockResolvedValue({ data: instances }),
  }
  vi.doMock('@/api/client', () => ({ adapterApi }))
  const mod = await import('@/components/ui/AdapterCombobox.vue')
  const wrapper = mount(mod.default, { props, attachTo: document.body })
  await flushPromises()
  return { wrapper, adapterApi }
}

describe('AdapterCombobox', () => {
  it('fetches configured instances via adapterApi.listInstances() on mount', async () => {
    const { adapterApi } = await mountAdapterCombobox([
      { id: 'inst-1', adapter_type: 'knx', name: 'Main KNX' },
      { id: 'inst-2', adapter_type: 'mqtt', name: 'Mosquitto' },
    ])
    expect(adapterApi.listInstances).toHaveBeenCalledTimes(1)
  })

  it('renders one item per distinct adapter_type', async () => {
    // Two KNX instances + one MQTT instance → two distinct types in the list.
    const { wrapper } = await mountAdapterCombobox([
      { id: 'inst-1', adapter_type: 'knx' },
      { id: 'inst-2', adapter_type: 'knx' },
      { id: 'inst-3', adapter_type: 'mqtt' },
    ])
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBe(2)
  })

  it('emits string[] when adapter type is selected', async () => {
    const { wrapper } = await mountAdapterCombobox([
      { id: 'inst-1', adapter_type: 'knx' },
      { id: 'inst-2', adapter_type: 'mqtt' },
    ])
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-1"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(Array.isArray(last)).toBe(true)
    expect(last).toEqual(['mqtt'])
  })

  it('renders chips for pre-selected adapter types', async () => {
    const { wrapper } = await mountAdapterCombobox(
      [
        { id: 'inst-1', adapter_type: 'knx' },
        { id: 'inst-2', adapter_type: 'mqtt' },
      ],
      { modelValue: ['knx', 'mqtt'] },
    )
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips.length).toBe(2)
  })

  it('keeps an orphan adapter type visible when its instance was removed', async () => {
    // 'iobroker' is in the filter set but has no live instance anymore.
    const { wrapper } = await mountAdapterCombobox(
      [{ id: 'inst-1', adapter_type: 'knx' }],
      { modelValue: ['knx', 'iobroker'] },
    )
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips.length).toBe(2)
    // The orphan chip carries a visible "no longer configured" cue (line-through + ⚠).
    const html = wrapper.html()
    expect(html).toMatch(/iobroker/)
    expect(html).toMatch(/line-through/)
    expect(html).toMatch(/⚠/)
  })

  it('filters adapters by query', async () => {
    const { wrapper } = await mountAdapterCombobox([
      { id: 'inst-1', adapter_type: 'knx' },
      { id: 'inst-2', adapter_type: 'mqtt' },
      { id: 'inst-3', adapter_type: 'modbus_tcp' },
    ])
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    input.element.value = 'mod'
    await input.trigger('input')
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBe(1)
  })

  it('survives a rejected listInstances call', async () => {
    const adapterApi = { listInstances: vi.fn().mockRejectedValue(new Error('boom')) }
    vi.doMock('@/api/client', () => ({ adapterApi }))
    const mod = await import('@/components/ui/AdapterCombobox.vue')
    const wrapper = mount(mod.default, { props: { modelValue: [] }, attachTo: document.body })
    await flushPromises()
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })
})
