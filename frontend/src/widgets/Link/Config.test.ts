// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import LinkConfig from './Config.vue'

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    treeLoaded: true,
    loadTree: vi.fn(),
    nodes: [],
    getNode: vi.fn(),
  }),
}))

function mountConfig(modelValue: Record<string, unknown>) {
  return mount(LinkConfig, {
    props: { modelValue },
    global: {
      mocks: { $t: (key: string) => key },
      stubs: { IconPicker: true, VisuIcon: true },
    },
  })
}

function lastEmitted(wrapper: ReturnType<typeof mountConfig>) {
  const events = wrapper.emitted('update:modelValue') ?? []
  return events[events.length - 1]?.[0] as Record<string, unknown>
}

describe('Link config icon size', () => {
  it('shows 0 (default size) when icon_size is missing', () => {
    const wrapper = mountConfig({ label: 'Garage' })
    const input = wrapper.get('[data-testid="link-icon-size"]')
    expect((input.element as HTMLInputElement).value).toBe('0')
    expect(wrapper.text()).toContain('widgets.link.iconSize')
    expect(wrapper.text()).toContain('widgets.link.iconSizeHint')
  })

  it('loads an existing icon size and emits changes as a number', async () => {
    const wrapper = mountConfig({ label: 'Garage', icon_size: 64 })
    const input = wrapper.get('[data-testid="link-icon-size"]')
    expect((input.element as HTMLInputElement).value).toBe('64')

    await input.setValue('120')

    expect(lastEmitted(wrapper).icon_size).toBe(120)
  })

  it('re-syncs icon_size when the edited widget changes', async () => {
    const wrapper = mountConfig({ label: 'A', icon_size: 64 })

    await wrapper.setProps({ modelValue: { label: 'B', icon_size: 32 } })
    expect((wrapper.get('[data-testid="link-icon-size"]').element as HTMLInputElement).value).toBe('32')

    await wrapper.setProps({ modelValue: { label: 'C' } })
    expect((wrapper.get('[data-testid="link-icon-size"]').element as HTMLInputElement).value).toBe('0')
    expect(lastEmitted(wrapper).icon_size).toBe(0)
  })
})
