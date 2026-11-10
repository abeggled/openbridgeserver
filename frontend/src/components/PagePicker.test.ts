// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PagePicker from './PagePicker.vue'

const store = vi.hoisted(() => ({
  treeLoaded: true,
  loadTree: vi.fn(),
  nodes: [] as Array<Record<string, unknown>>,
  getNode(id: string) { return this.nodes.find(n => n.id === id) },
}))

vi.mock('@/stores/visu', () => ({ useVisuStore: () => store }))
vi.mock('@/components/VisuIcon.vue', () => ({ default: { props: ['icon'], template: '<i>{{ icon }}</i>' } }))

let wrapper: VueWrapper | null = null

function mountPicker(modelValue = '') {
  wrapper = mount(PagePicker, {
    props: { modelValue },
    attachTo: document.body,
    global: { mocks: { $t: (key: string) => key } },
  })
  return wrapper
}

function lastValue(w: VueWrapper) {
  const events = w.emitted('update:modelValue') ?? []
  return events[events.length - 1]?.[0]
}

beforeEach(() => {
  vi.useFakeTimers()
  store.treeLoaded = true
  store.loadTree.mockReset()
  store.nodes = [
    { id: 'home', name: 'Home', type: 'LOCATION', parent_id: null, icon: null },
    { id: 'cams', name: 'Kameras', type: 'PAGE', parent_id: 'home', icon: null },
    { id: 'big', name: 'Einfahrt gross', type: 'PAGE', parent_id: 'home', icon: '🚗' },
  ]
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

describe('PagePicker', () => {
  it('loads the tree once when it is not loaded yet', () => {
    store.treeLoaded = false
    mountPicker()
    expect(store.loadTree).toHaveBeenCalledTimes(1)
  })

  it('does not reload a loaded tree', () => {
    mountPicker()
    expect(store.loadTree).not.toHaveBeenCalled()
  })

  it('shows the placeholder without a selection', () => {
    const w = mountPicker()
    expect(w.get('[data-testid="page-picker-display"]').text()).toContain('widgets.common.selectPage')
    expect(w.find('[data-testid="page-picker-clear"]').exists()).toBe(false)
  })

  it('shows the full path and icon of the selected page', () => {
    const w = mountPicker('big')
    const display = w.get('[data-testid="page-picker-display"]')
    expect(display.text()).toContain('Home / Einfahrt gross')
    expect(display.text()).toContain('🚗')
  })

  it('falls back to a type icon for a selected node without its own icon', () => {
    expect(mountPicker('cams').get('[data-testid="page-picker-display"]').text()).toContain('📄')
    wrapper?.unmount()
    expect(mountPicker('home').get('[data-testid="page-picker-display"]').text()).toContain('📁')
  })

  it('opens, focuses the search, filters and selects a page', async () => {
    const w = mountPicker('cams')
    await w.get('[data-testid="page-picker-display"]').trigger('click')
    vi.advanceTimersByTime(50)
    const search = w.get('input')
    expect(document.activeElement).toBe(search.element)

    // All nodes, sorted by path, current one highlighted, type labels shown.
    expect(w.findAll('button').map(b => b.attributes('data-testid'))).toEqual([
      'page-picker-option-home', 'page-picker-option-big', 'page-picker-option-cams',
    ])
    expect(w.get('[data-testid="page-picker-option-cams"]').classes()).toContain('bg-blue-50')
    expect(w.get('[data-testid="page-picker-option-big"]').classes()).not.toContain('bg-blue-50')
    expect(w.get('[data-testid="page-picker-option-home"]').text()).toContain('widgets.common.nodeTypeArea')
    expect(w.get('[data-testid="page-picker-option-big"]').text()).toContain('widgets.common.nodeTypePage')

    await search.setValue('einfahrt')
    expect(w.findAll('button')).toHaveLength(1)

    await w.get('[data-testid="page-picker-option-big"]').trigger('click')

    expect(lastValue(w)).toBe('big')
    expect(w.find('input').exists()).toBe(false)
  })

  it('shows "no results" for a search without matches', async () => {
    const w = mountPicker()
    await w.get('[data-testid="page-picker-display"]').trigger('click')
    await w.get('input').setValue('xyz')
    expect(w.text()).toContain('widgets.common.noResults')
  })

  it('closes on Escape', async () => {
    const w = mountPicker()
    await w.get('[data-testid="page-picker-display"]').trigger('click')
    await w.get('input').trigger('keydown', { key: 'Escape' })
    expect(w.find('input').exists()).toBe(false)
  })

  it('clears the selection without opening the list', async () => {
    const w = mountPicker('big')
    await w.get('[data-testid="page-picker-clear"]').trigger('click')
    expect(lastValue(w)).toBe('')
    expect(w.find('input').exists()).toBe(false)
  })

  it('closes on a click outside, but not on a click inside', async () => {
    const w = mountPicker()
    await w.get('[data-testid="page-picker-display"]').trigger('click')

    w.get('input').element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await w.vm.$nextTick()
    expect(w.find('input').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await w.vm.$nextTick()
    expect(w.find('input').exists()).toBe(false)
  })

  it('removes its outside-click listener on unmount', () => {
    const remove = vi.spyOn(document, 'removeEventListener')
    mountPicker().unmount()
    wrapper = null
    expect(remove).toHaveBeenCalledWith('mousedown', expect.any(Function))
    remove.mockRestore()
  })
})
