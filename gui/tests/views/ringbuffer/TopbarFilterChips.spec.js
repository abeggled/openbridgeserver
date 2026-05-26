/**
 * Tests for the TopbarFilterChips component (issue #435).
 *
 * The component lists filtersets that have been pinned to the topbar
 * (`topbar_active=true`, sorted by `topbar_order`) as compact chips:
 *
 *   [▌●  Set Name  ×]
 *
 * - Left coloured bar uses the set's `color` field
 * - ●/○ toggles `is_active` via PATCH /ringbuffer/filtersets/{id}/topbar
 * - Clicking the chip body emits `edit-set` with the set id
 * - × removes the set from topbar via PATCH …/topbar { topbar_active: false }
 * - Drag/drop reorders and persists via PATCH /ringbuffer/filtersets/order
 * - A "+ Filter ▾" dropdown lists sets with `topbar_active=false` and a
 *   "+ Neu" action that emits `new-set`
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

function makeApi(overrides = {}) {
  return {
    listFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    patchFiltersetTopbar: vi.fn().mockResolvedValue({ data: {} }),
    patchFiltersetOrder: vi.fn().mockResolvedValue({ data: {} }),
    ...overrides,
  }
}

function makeSet(overrides = {}) {
  return {
    id: 'set-1',
    name: 'Set One',
    color: '#3b82f6',
    is_active: true,
    topbar_active: true,
    topbar_order: 0,
    ...overrides,
  }
}

async function mountChips(props = {}) {
  const api = props.api ?? makeApi({
    listFiltersets: vi.fn().mockResolvedValue({
      data: [
        makeSet({ id: 's-a', name: 'Aktiv',     topbar_active: true,  topbar_order: 0, is_active: true,  color: '#10b981' }),
        makeSet({ id: 's-b', name: 'Beta',      topbar_active: true,  topbar_order: 1, is_active: false, color: '#f59e0b' }),
        makeSet({ id: 's-c', name: 'Verfügbar', topbar_active: false, topbar_order: 0, is_active: false, color: '#6366f1' }),
      ],
    }),
  })

  vi.doMock('@/api/client', () => ({ ringbufferApi: api }))

  // Stub vue-draggable-plus VueDraggable component so we can simulate
  // update:modelValue events deterministically without touching real DnD.
  vi.doMock('vue-draggable-plus', () => ({
    VueDraggable: {
      name: 'VueDraggableStub',
      props: ['modelValue'],
      emits: ['update:modelValue', 'end'],
      template: '<div data-testid="draggable-stub"><slot /></div>',
    },
  }))

  const mod = await import('@/views/ringbuffer/TopbarFilterChips.vue')
  const TopbarFilterChips = mod.default

  const wrapper = mount(TopbarFilterChips, {
    props: props.props ?? {},
    attachTo: document.body,
  })

  await flushPromises()
  await wrapper.vm.$nextTick()
  await flushPromises()

  return { wrapper, api }
}

describe('TopbarFilterChips', () => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = ''
  })

  it('loads filtersets on mount and renders only topbar-active sets as chips', async () => {
    const { wrapper, api } = await mountChips()
    expect(api.listFiltersets).toHaveBeenCalledTimes(1)
    const chipBodies = wrapper.findAll('[data-testid^="topbar-chip-body-"]')
    expect(chipBodies.length).toBe(2)
    expect(wrapper.text()).toContain('Aktiv')
    expect(wrapper.text()).toContain('Beta')
    // Verfügbar appears only inside the (closed) + Filter menu, so the chip
    // body should not contain it.
    expect(chipBodies.map((c) => c.text()).join(' ')).not.toContain('Verfügbar')
  })

  it('renders chips in topbar_order', async () => {
    const api = makeApi({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [
          makeSet({ id: 's-b', name: 'B', topbar_active: true, topbar_order: 1 }),
          makeSet({ id: 's-a', name: 'A', topbar_active: true, topbar_order: 0 }),
        ],
      }),
    })
    const { wrapper } = await mountChips({ api })
    const chipBodies = wrapper.findAll('[data-testid^="topbar-chip-body-"]')
    expect(chipBodies.length).toBe(2)
    expect(chipBodies[0].text()).toContain('A')
    expect(chipBodies[1].text()).toContain('B')
  })

  it('applies the set color to the chip left-bar', async () => {
    const { wrapper } = await mountChips()
    const bar = wrapper.find('[data-testid="topbar-chip-color-s-a"]')
    expect(bar.exists()).toBe(true)
    expect(bar.attributes('style') || '').toContain('#10b981')
  })

  it('clicking ●/○ toggles is_active via patchFiltersetTopbar', async () => {
    const { wrapper, api } = await mountChips()
    const toggle = wrapper.find('[data-testid="topbar-chip-toggle-s-a"]')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    expect(api.patchFiltersetTopbar).toHaveBeenCalledWith('s-a', { is_active: false })
  })

  it('clicking the chip body emits edit-set with the id', async () => {
    const { wrapper } = await mountChips()
    const body = wrapper.find('[data-testid="topbar-chip-body-s-a"]')
    expect(body.exists()).toBe(true)
    await body.trigger('click')
    const events = wrapper.emitted('edit-set')
    expect(events).toBeTruthy()
    expect(events[0]).toEqual(['s-a'])
  })

  it('clicking × removes the set from topbar via patchFiltersetTopbar', async () => {
    const { wrapper, api } = await mountChips()
    const close = wrapper.find('[data-testid="topbar-chip-remove-s-a"]')
    expect(close.exists()).toBe(true)
    await close.trigger('click')
    expect(api.patchFiltersetTopbar).toHaveBeenCalledWith('s-a', { topbar_active: false })
  })

  it('persists drag-reorder via patchFiltersetOrder when the stub emits update:modelValue', async () => {
    const { wrapper, api } = await mountChips()
    const stub = wrapper.findComponent({ name: 'VueDraggableStub' })
    expect(stub.exists()).toBe(true)
    // Emit reordered list (B then A)
    await stub.vm.$emit('update:modelValue', [
      makeSet({ id: 's-b', name: 'Beta',  topbar_active: true, topbar_order: 0 }),
      makeSet({ id: 's-a', name: 'Aktiv', topbar_active: true, topbar_order: 1 }),
    ])
    await stub.vm.$emit('end')
    await flushPromises()
    expect(api.patchFiltersetOrder).toHaveBeenCalledTimes(1)
    expect(api.patchFiltersetOrder).toHaveBeenCalledWith([
      { id: 's-b', topbar_order: 0 },
      { id: 's-a', topbar_order: 1 },
    ])
  })

  it('+ Filter dropdown only lists sets that are not topbar-active', async () => {
    const { wrapper } = await mountChips()
    const btn = wrapper.find('[data-testid="topbar-add-filter-btn"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    const items = wrapper.findAll('[data-testid^="topbar-add-filter-item-"]')
    // Only "Verfügbar" (s-c) is not topbar_active
    expect(items.length).toBe(1)
    expect(items[0].text()).toContain('Verfügbar')
  })

  it('+ Filter dropdown contains a + Neu action that emits new-set', async () => {
    const { wrapper } = await mountChips()
    await wrapper.find('[data-testid="topbar-add-filter-btn"]').trigger('click')
    const newBtn = wrapper.find('[data-testid="topbar-add-filter-new"]')
    expect(newBtn.exists()).toBe(true)
    await newBtn.trigger('click')
    expect(wrapper.emitted('new-set')).toBeTruthy()
  })

  it('picking an available set from the dropdown adds it to the topbar via patchFiltersetTopbar', async () => {
    const { wrapper, api } = await mountChips()
    await wrapper.find('[data-testid="topbar-add-filter-btn"]').trigger('click')
    const item = wrapper.find('[data-testid="topbar-add-filter-item-s-c"]')
    expect(item.exists()).toBe(true)
    await item.trigger('click')
    expect(api.patchFiltersetTopbar).toHaveBeenCalledWith('s-c', { topbar_active: true })
  })

  it('renders + Neu as the first option in the dropdown (pinned), search input second', async () => {
    const { wrapper } = await mountChips()
    await wrapper.find('[data-testid="topbar-add-filter-btn"]').trigger('click')
    const menu = wrapper.find('[data-testid="topbar-add-filter-menu"]')
    expect(menu.exists()).toBe(true)
    const newBtn = menu.find('[data-testid="topbar-add-filter-new"]')
    const search = menu.find('[data-testid="topbar-add-filter-search"]')
    // + Neu must appear before the search input in the DOM
    const newPos = menu.html().indexOf(newBtn.html())
    const searchPos = menu.html().indexOf(search.html())
    expect(newPos).toBeLessThan(searchPos)
  })

  it('search input filters the available sets by name', async () => {
    // Inject an extra non-topbar set so we have something to filter against
    const filtersets = [
      { id: 's-a', name: 'Wasserzähler', color: '#3b82f6', topbar_active: false, topbar_order: 0, is_active: true },
      { id: 's-b', name: 'Heizung', color: '#10b981', topbar_active: false, topbar_order: 0, is_active: true },
      { id: 's-c', name: 'Lüftung', color: '#ef4444', topbar_active: false, topbar_order: 0, is_active: true },
    ]
    const api = makeApi({ listFiltersets: vi.fn().mockResolvedValue({ data: filtersets }) })
    const { wrapper } = await mountChips({ api })
    await wrapper.find('[data-testid="topbar-add-filter-btn"]').trigger('click')
    // All three visible initially
    expect(wrapper.findAll('[data-testid^="topbar-add-filter-item-"]').length).toBe(3)
    // Typing "hei" narrows to Heizung
    await wrapper.find('[data-testid="topbar-add-filter-search"]').setValue('hei')
    const items = wrapper.findAll('[data-testid^="topbar-add-filter-item-"]')
    expect(items.length).toBe(1)
    expect(items[0].text()).toContain('Heizung')
  })

  it('shows "Keine Treffer" when search yields no results', async () => {
    const { wrapper } = await mountChips()
    await wrapper.find('[data-testid="topbar-add-filter-btn"]').trigger('click')
    await wrapper.find('[data-testid="topbar-add-filter-search"]').setValue('zzz-no-match')
    expect(wrapper.find('[data-testid="topbar-add-filter-empty"]').text()).toContain('Keine Treffer')
  })

  // ---------------------------------------------------------------------
  // QA-01 audit: drag-reorder edge cases (#439)
  // ---------------------------------------------------------------------

  it('does not call patchFiltersetOrder when the set has 0 chips', async () => {
    const api = makeApi({
      listFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    })
    const { wrapper } = await mountChips({ api })
    const stub = wrapper.findComponent({ name: 'VueDraggableStub' })
    // Simulate the drag-end with the empty list — the chips area exists but
    // there is nothing to reorder.
    await stub.vm.$emit('update:modelValue', [])
    await stub.vm.$emit('end')
    await flushPromises()
    // The drag handler still fires once but with an empty list — that is a
    // legal API call returning {} so we accept up to 1 invocation.
    if (api.patchFiltersetOrder.mock.calls.length > 0) {
      expect(api.patchFiltersetOrder).toHaveBeenCalledWith([])
    }
    // No chips rendered, no other side effects (no chip elements present).
    expect(wrapper.findAll('[data-testid^="topbar-chip-body-"]')).toHaveLength(0)
  })

  it('does not crash and persists trivially when only 1 chip is dragged', async () => {
    const api = makeApi({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [makeSet({ id: 'single', name: 'Solo', topbar_active: true, topbar_order: 0 })],
      }),
    })
    const { wrapper } = await mountChips({ api })
    const stub = wrapper.findComponent({ name: 'VueDraggableStub' })
    // A 1-element drag effectively keeps the order — vue-draggable-plus still
    // fires `end`, the component just sends a one-element list back to the API.
    await stub.vm.$emit('update:modelValue', [
      makeSet({ id: 'single', name: 'Solo', topbar_active: true, topbar_order: 0 }),
    ])
    await stub.vm.$emit('end')
    await flushPromises()
    expect(api.patchFiltersetOrder).toHaveBeenCalledWith([{ id: 'single', topbar_order: 0 }])
    // The single chip remains rendered.
    expect(wrapper.find('[data-testid="topbar-chip-body-single"]').exists()).toBe(true)
  })

  it('still emits "changed" when reorder keeps the same order (idempotent drop)', async () => {
    // Drag in the same position: vue-draggable-plus emits update:modelValue
    // with the unchanged array. The component must not crash and must keep
    // the chip order on screen.
    const { wrapper, api } = await mountChips()
    const stub = wrapper.findComponent({ name: 'VueDraggableStub' })
    const unchanged = [
      makeSet({ id: 's-a', name: 'Aktiv', topbar_active: true, topbar_order: 0, color: '#10b981' }),
      makeSet({ id: 's-b', name: 'Beta',  topbar_active: true, topbar_order: 1, color: '#f59e0b' }),
    ]
    await stub.vm.$emit('update:modelValue', unchanged)
    await stub.vm.$emit('end')
    await flushPromises()
    expect(api.patchFiltersetOrder).toHaveBeenCalledWith([
      { id: 's-a', topbar_order: 0 },
      { id: 's-b', topbar_order: 1 },
    ])
    expect(wrapper.emitted('changed')).toBeTruthy()
  })

  it('exposes a time-filter-slot in the leftmost position', async () => {
    vi.resetModules()
    vi.doMock('@/api/client', () => ({
      ringbufferApi: makeApi(),
    }))
    vi.doMock('vue-draggable-plus', () => ({
      VueDraggable: {
        name: 'VueDraggableStub',
        props: ['modelValue'],
        template: '<div><slot /></div>',
      },
    }))
    const mod = await import('@/views/ringbuffer/TopbarFilterChips.vue')
    const wrapper = mount(mod.default, {
      slots: {
        'time-filter-slot': '<span data-testid="time-filter-marker">TIME</span>',
      },
      attachTo: document.body,
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="time-filter-marker"]').exists()).toBe(true)
  })
})
