// @vitest-environment jsdom
/**
 * Link widget — `history_back` option: go back to the previously visited page,
 * falling back to `target_node_id` when there is no history.
 */
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LinkWidget from './Widget.vue'
import LinkConfig from './Config.vue'

const router = vi.hoisted(() => ({ push: vi.fn(), back: vi.fn() }))

vi.mock('vue-router', () => ({
  useRouter: () => router,
  useRoute: () => ({ params: { id: 'current' } }),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('@/components/VisuIcon.vue', () => ({ default: { template: '<span />' } }))
vi.mock('@/components/IconPicker.vue', () => ({ default: { template: '<span />' } }))
vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    getNode: (id: string) => (id === 'target' ? { id, type: 'PAGE', parent_id: null, access: null } : undefined),
    getChildren: () => [],
    hasSessionToken: () => false,
    isLoggedIn: false,
    nodes: [],
    treeLoaded: true,
    loadTree: vi.fn(),
  }),
}))

function mountLink(config: Record<string, unknown>, editorMode = false) {
  return mount(LinkWidget, {
    props: { config, datapointId: null, value: null, statusValue: null, editorMode },
    global: { mocks: { $t: (key: string) => key } },
  })
}

function setHistoryBack(back: string | null) {
  window.history.replaceState({ back }, '')
}

beforeEach(() => {
  router.push.mockReset()
  router.back.mockReset()
})

afterEach(() => {
  window.history.replaceState(null, '')
})

describe('Link widget — history_back', () => {
  it('navigates to the target page as before without the option, even with history', async () => {
    setHistoryBack('/previous')
    await mountLink({ target_node_id: 'target' }).trigger('click')

    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'target' } })
    expect(router.back).not.toHaveBeenCalled()
  })

  it('goes back in the history when the option is set and there is a previous page', async () => {
    setHistoryBack('/previous')
    await mountLink({ target_node_id: 'target', history_back: true }).trigger('click')

    expect(router.back).toHaveBeenCalledTimes(1)
    expect(router.push).not.toHaveBeenCalled()
  })

  it('falls back to the target page when the option is set but there is no history', async () => {
    setHistoryBack(null)
    await mountLink({ target_node_id: 'target', history_back: true }).trigger('click')

    expect(router.back).not.toHaveBeenCalled()
    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'target' } })
  })

  it('does nothing without history and without a target page', async () => {
    window.history.replaceState(null, '')
    await mountLink({ history_back: true }).trigger('click')

    expect(router.back).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })

  it('never navigates in the editor', async () => {
    setHistoryBack('/previous')
    await mountLink({ target_node_id: 'target', history_back: true }, true).trigger('click')

    expect(router.back).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })

  it('shows no "no target" hint when the option is set', () => {
    const wrapper = mountLink({ history_back: true })

    expect(wrapper.find('[data-testid="link-no-target"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('→')
  })

  it('still shows the "no target" hint without the option and without a target', () => {
    const wrapper = mountLink({})

    expect(wrapper.get('[data-testid="link-no-target"]').text()).toBe('widgets.link.noTarget')
    expect(wrapper.text()).not.toContain('→')
  })
})

describe('Link config — history_back', () => {
  function mountConfig(modelValue: Record<string, unknown>) {
    return mount(LinkConfig, {
      props: { modelValue },
      global: { mocks: { $t: (key: string) => key } },
    })
  }

  it('defaults to off, loads a stored value and emits the toggled one', async () => {
    const off = mountConfig({})
    expect((off.get('[data-testid="link-config-history-back"]').element as HTMLInputElement).checked).toBe(false)
    expect(off.text()).toContain('widgets.link.historyBack')

    const on = mountConfig({ history_back: true })
    const box = on.get('[data-testid="link-config-history-back"]')
    expect((box.element as HTMLInputElement).checked).toBe(true)

    await box.setValue(false)
    const events = on.emitted('update:modelValue') ?? []
    expect(events[events.length - 1][0]).toMatchObject({ history_back: false })
  })

  it('re-syncs the option when another widget is selected', async () => {
    const wrapper = mountConfig({ history_back: true })

    await wrapper.setProps({ modelValue: { label: 'other' } })

    expect((wrapper.get('[data-testid="link-config-history-back"]').element as HTMLInputElement).checked).toBe(false)
  })
})
