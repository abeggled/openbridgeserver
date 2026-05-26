/**
 * Tests for TagCombobox.vue — a multi-select Combobox over the datapoints
 * tag list (stored in @/stores/datapoints).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/stores/datapoints')
})

async function mountTagCombobox(props = {}, tags = ['indoor', 'outdoor', 'energy']) {
  const loadTags = vi.fn().mockResolvedValue(undefined)
  vi.doMock('@/stores/datapoints', () => ({
    useDatapointStore: () => ({
      allTags: tags,
      loadTags,
    }),
  }))
  const mod = await import('@/components/ui/TagCombobox.vue')
  const wrapper = mount(mod.default, { props, attachTo: document.body })
  await flushPromises()
  return { wrapper, loadTags }
}

describe('TagCombobox', () => {
  it('renders tags from the store on focus', async () => {
    const { wrapper } = await mountTagCombobox({ modelValue: [] })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBe(3)
    expect(items[0].text()).toContain('indoor')
  })

  it('filters tags by query', async () => {
    const { wrapper } = await mountTagCombobox(
      { modelValue: [] },
      ['indoor', 'outdoor', 'energy'],
    )
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    input.element.value = 'door'
    await input.trigger('input')
    // wait > debounce
    await new Promise((r) => setTimeout(r, 250))
    await flushPromises()
    const items = wrapper.findAll('[data-testid^="combobox-item-"]')
    expect(items.length).toBe(2)
  })

  it('emits string[] when a tag is selected', async () => {
    const { wrapper } = await mountTagCombobox({ modelValue: [] })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(Array.isArray(last)).toBe(true)
    expect(last).toEqual(['indoor'])
  })

  it('shows chips for already-selected tags', async () => {
    const { wrapper } = await mountTagCombobox({ modelValue: ['indoor', 'energy'] })
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips.length).toBe(2)
    expect(chips[0].text()).toContain('indoor')
    expect(chips[1].text()).toContain('energy')
  })

  it('loads tags via store.loadTags on mount when store is empty', async () => {
    const loadTags = vi.fn().mockResolvedValue(undefined)
    vi.doMock('@/stores/datapoints', () => ({
      useDatapointStore: () => ({ allTags: [], loadTags }),
    }))
    const mod = await import('@/components/ui/TagCombobox.vue')
    mount(mod.default, { props: { modelValue: [] } })
    await flushPromises()
    expect(loadTags).toHaveBeenCalledTimes(1)
  })
})
