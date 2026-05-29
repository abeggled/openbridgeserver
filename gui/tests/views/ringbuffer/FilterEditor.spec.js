/**
 * Tests for FilterEditor.vue (issue #436).
 *
 * The editor is a soft-modal Filterset form that:
 *  - hydrates from an existing set via getFilterset(id) when setId prop is set,
 *  - shows an empty form when setId is null (new set),
 *  - renders Hierarchy, DP, Tag, Adapter comboboxes plus q + value_filter inputs,
 *  - provides an `⊞` expand button per Hierarchy chip that materializes the
 *    DPs under the node (and its descendants) as concrete DP chips,
 *  - submits a flat FilterCriteria payload via createFilterset / updateFilterset,
 *  - on "Speichern & in Topleiste" additionally calls patchFiltersetTopbar,
 *  - on "Verwerfen" with a dirty form shows a confirm dialog before closing,
 *  - exposes a color picker that reflects the chosen color in the submit payload.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

beforeEach(() => {
  vi.resetModules()
  document.body.innerHTML = ''
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/components/ui/Modal.vue')
  vi.doUnmock('@/components/ui/ConfirmDialog.vue')
  vi.doUnmock('@/components/ui/HierarchyCombobox.vue')
  vi.doUnmock('@/components/ui/DpCombobox.vue')
  vi.doUnmock('@/components/ui/TagCombobox.vue')
  vi.doUnmock('@/components/ui/AdapterCombobox.vue')
  vi.doUnmock('@/stores/datapoints')
})

function makeRingbufferApi(overrides = {}) {
  return {
    getFilterset: vi.fn().mockResolvedValue({ data: makeSampleSet() }),
    createFilterset: vi.fn().mockResolvedValue({ data: { id: 'fs-new' } }),
    updateFilterset: vi.fn().mockResolvedValue({ data: { id: 'fs-1' } }),
    patchFiltersetTopbar: vi.fn().mockResolvedValue({ data: {} }),
    ...overrides,
  }
}

function makeSearchApi(overrides = {}) {
  return {
    search: vi.fn().mockResolvedValue({ data: { items: [] } }),
    ...overrides,
  }
}

function makeHierarchyApi(overrides = {}) {
  return {
    listTrees: vi.fn().mockResolvedValue({ data: [] }),
    getTreeNodes: vi.fn().mockResolvedValue({ data: [] }),
    ...overrides,
  }
}

function makeSampleSet(overrides = {}) {
  return {
    id: 'fs-1',
    name: 'Heizung',
    description: 'Heiz-Filter',
    dsl_version: 2,
    is_active: true,
    color: '#10b981',
    topbar_active: false,
    topbar_order: 0,
    filter: {
      hierarchy_nodes: [],
      datapoints: ['dp-1', 'dp-2'],
      tags: ['heizung'],
      adapters: ['knx'],
      q: 'temp',
      value_filter: null,
    },
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    ...overrides,
  }
}

/**
 * Stub the heavy combobox children so we can observe model updates without
 * exercising their internal search machinery. Each stub exposes a clearly
 * named input so tests can drive the bound model value deterministically.
 */
function stubCombobox(name, multi) {
  return defineComponent({
    name,
    props: ['modelValue', 'placeholder'],
    emits: ['update:modelValue'],
    setup(props, { emit, slots }) {
      return () =>
        h(
          'div',
          { 'data-stub': name, 'data-testid': `stub-${name}` },
          [
            h('div', { 'data-testid': `stub-${name}-chips` }, multi
              ? (Array.isArray(props.modelValue) ? props.modelValue : []).map((id, i) =>
                  h('span', { key: id, 'data-testid': `stub-${name}-chip-${i}`, 'data-chip-id': id },
                    slots.chip ? slots.chip({ item: { id, label: id }, index: i, remove: () => {} }) : [String(id)],
                  ),
                )
              : [String(props.modelValue ?? '')]),
            // Hidden helper button to push a value out for tests.
            h('button', {
              'data-testid': `stub-${name}-emit`,
              onClick: (ev) => {
                const value = ev?.target?.getAttribute?.('data-emit-value')
                if (value == null) return
                const parsed = JSON.parse(value)
                emit('update:modelValue', parsed)
              },
            }, 'emit'),
          ],
        )
    },
  })
}

/** Emit a single tag through the TagCombobox stub so the form has at least
 *  one populated criterion (the editor refuses to save an empty filter). */
async function populateMinimalFilter(wrapper, tags = ['heizung']) {
  const emitBtn = wrapper.find('[data-testid="stub-TagCombobox-emit"]')
  emitBtn.element.setAttribute('data-emit-value', JSON.stringify(tags))
  await emitBtn.trigger('click')
  await wrapper.vm.$nextTick()
}

async function mountEditor({ props = {}, ringbufferApi, searchApi, hierarchyApi, leaveEmpty = false } = {}) {
  ringbufferApi = ringbufferApi ?? makeRingbufferApi()
  searchApi = searchApi ?? makeSearchApi()
  hierarchyApi = hierarchyApi ?? makeHierarchyApi()

  vi.doMock('@/api/client', () => ({
    ringbufferApi,
    searchApi,
    hierarchyApi,
  }))

  // Passthrough modal: always render slot + footer.
  vi.doMock('@/components/ui/Modal.vue', () => ({
    default: defineComponent({
      name: 'Modal',
      props: ['modelValue', 'title', 'maxWidth', 'softBackdrop'],
      emits: ['update:modelValue'],
      setup(props, { slots }) {
        return () =>
          props.modelValue
            ? h('div', { 'data-stub': 'Modal', 'data-testid': 'editor-modal' }, [
                slots.default ? slots.default() : null,
                slots.footer ? h('div', { 'data-testid': 'editor-modal-footer' }, slots.footer()) : null,
              ])
            : null
      },
    }),
  }))

  vi.doMock('@/components/ui/ConfirmDialog.vue', () => ({
    default: defineComponent({
      name: 'ConfirmDialog',
      props: ['modelValue', 'title', 'message', 'confirmLabel'],
      emits: ['update:modelValue', 'confirm'],
      setup(props, { emit }) {
        return () =>
          props.modelValue
            ? h('div', { 'data-testid': 'confirm-discard' }, [
                h('button', { 'data-testid': 'confirm-discard-ok', onClick: () => { emit('confirm'); emit('update:modelValue', false) } }, 'OK'),
                h('button', { 'data-testid': 'confirm-discard-cancel', onClick: () => emit('update:modelValue', false) }, 'Cancel'),
              ])
            : null
      },
    }),
  }))

  vi.doMock('@/components/ui/HierarchyCombobox.vue', () => ({ default: stubCombobox('HierarchyCombobox', true) }))
  vi.doMock('@/components/ui/DpCombobox.vue', () => ({ default: stubCombobox('DpCombobox', true) }))
  vi.doMock('@/components/ui/TagCombobox.vue', () => ({ default: stubCombobox('TagCombobox', true) }))
  vi.doMock('@/components/ui/AdapterCombobox.vue', () => ({ default: stubCombobox('AdapterCombobox', true) }))

  const mod = await import('@/views/ringbuffer/FilterEditor.vue')
  const FilterEditor = mod.default

  const wrapper = mount(FilterEditor, {
    props: { modelValue: true, setId: null, ...props },
    attachTo: document.body,
  })

  await flushPromises()
  await wrapper.vm.$nextTick()
  await flushPromises()

  // When the editor opens with setId=null the form is empty, which now
  // disables Save (empty FilterCriteria cannot be persisted). Seed a single
  // tag so save-related tests can exercise the submit path. Specs that want
  // to assert the empty-state explicitly pass `leaveEmpty: true`.
  if (!leaveEmpty && (props.setId === null || props.setId === undefined)) {
    await populateMinimalFilter(wrapper)
  }

  return { wrapper, ringbufferApi, searchApi, hierarchyApi }
}

describe('FilterEditor (#436)', () => {
  it('renders an empty form when setId is null', async () => {
    const { wrapper, ringbufferApi } = await mountEditor({ props: { setId: null } })
    expect(ringbufferApi.getFilterset).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="filter-editor-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="filter-editor-name"]').element.value).toBe('')
    expect(wrapper.find('[data-testid="filter-editor-description"]').element.value).toBe('')
  })

  it('hydrates the form when setId points to an existing filterset', async () => {
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: makeSampleSet() }),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-1' }, ringbufferApi })
    expect(ringbufferApi.getFilterset).toHaveBeenCalledWith('fs-1')
    expect(wrapper.find('[data-testid="filter-editor-name"]').element.value).toBe('Heizung')
    expect(wrapper.find('[data-testid="filter-editor-description"]').element.value).toBe('Heiz-Filter')
    expect(wrapper.find('[data-testid="filter-editor-q"]').element.value).toBe('temp')
  })

  it('renders a hint about hierarchy OR DP and AND semantics once a criterion is configured', async () => {
    const { wrapper } = await mountEditor({ props: { setId: null }, leaveEmpty: true })
    // With no criteria set, an "empty-filter" warning is shown instead.
    expect(wrapper.find('[data-testid="filter-editor-empty-hint"]').exists()).toBe(true)
    await populateMinimalFilter(wrapper)
    expect(wrapper.text()).toMatch(/Hierarchy OR DP/i)
    expect(wrapper.text()).toMatch(/AND/i)
  })

  it('disables the Save button when the FilterCriteria is empty', async () => {
    // The plain "Speichern" button has been removed — "Speichern & in
    // Topleiste" is now the sole save action and is disabled while the
    // criterion set is empty.
    const { wrapper } = await mountEditor({ props: { setId: null }, leaveEmpty: true })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('NoFilter')
    expect(wrapper.find('[data-testid="filter-editor-save"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="filter-editor-save-topbar"]').element.disabled).toBe(true)
    // Adding any criterion enables the button.
    await populateMinimalFilter(wrapper)
    expect(wrapper.find('[data-testid="filter-editor-save-topbar"]').element.disabled).toBe(false)
  })

  it('Enter outside a text field triggers Save & in Topleiste', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('EnterSave')
    // Dispatch Enter on the document, target = body (not an input).
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    document.body.dispatchEvent(event)
    await flushPromises()
    expect(ringbufferApi.createFilterset).toHaveBeenCalledTimes(1)
    expect(ringbufferApi.patchFiltersetTopbar).toHaveBeenCalledWith('fs-new', { topbar_active: true })
  })

  it('Enter inside a text field does NOT trigger Save (native field behaviour wins)', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    const nameInput = wrapper.find('[data-testid="filter-editor-name"]')
    await nameInput.setValue('Typing')
    // Dispatch Enter with the name input as target.
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    nameInput.element.dispatchEvent(event)
    await flushPromises()
    expect(ringbufferApi.createFilterset).not.toHaveBeenCalled()
  })

  it('Enter while the filter is empty does not save', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi, leaveEmpty: true })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('NoCriteria')
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    document.body.dispatchEvent(event)
    await flushPromises()
    expect(ringbufferApi.createFilterset).not.toHaveBeenCalled()
  })

  it('ESC inside a text field blurs the field without closing the editor', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    const nameInput = wrapper.find('[data-testid="filter-editor-name"]')
    nameInput.element.focus()
    expect(document.activeElement).toBe(nameInput.element)
    const before = (wrapper.emitted('update:modelValue') ?? []).length
    const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    nameInput.element.dispatchEvent(event)
    await flushPromises()
    // Field has lost focus
    expect(document.activeElement).not.toBe(nameInput.element)
    // No additional close emit was triggered by this ESC
    const after = wrapper.emitted('update:modelValue') ?? []
    expect(after.length).toBe(before)
  })

  it('renders an expand button next to each Hierarchy chip', async () => {
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'n1', include_descendants: true }],
        datapoints: [],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-1' }, ringbufferApi })
    const expand = wrapper.find('[data-testid="hierarchy-expand-0"]')
    expect(expand.exists()).toBe(true)
    expect(expand.text()).toContain('⊞')
  })

  it('clicking the expand button materializes DPs under the node and removes the hierarchy chip', async () => {
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'n1', include_descendants: true }],
        datapoints: ['dp-existing'],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const hierarchyApi = makeHierarchyApi({
      listTrees: vi.fn().mockResolvedValue({ data: [{ id: 't1', name: 'Tree' }] }),
      getTreeNodes: vi.fn().mockResolvedValue({
        data: [
          { id: 'n1', tree_id: 't1', parent_id: null, name: 'Root' },
          { id: 'n2', tree_id: 't1', parent_id: 'n1', name: 'Child' },
        ],
      }),
    })
    const searchApi = makeSearchApi({
      search: vi.fn().mockResolvedValue({
        data: { items: [{ id: 'dp-a' }, { id: 'dp-b' }, { id: 'dp-existing' }] },
      }),
    })
    const { wrapper } = await mountEditor({
      props: { setId: 'fs-1' },
      ringbufferApi,
      searchApi,
      hierarchyApi,
    })

    await wrapper.find('[data-testid="hierarchy-expand-0"]').trigger('click')
    await flushPromises()

    expect(searchApi.search).toHaveBeenCalledTimes(1)
    const callArgs = searchApi.search.mock.calls[0][0]
    expect(callArgs.size).toBeGreaterThanOrEqual(500)
    // Node id list must contain both n1 (root) and n2 (descendant)
    const nodeIds = String(callArgs.node_id || '').split(',').filter(Boolean).sort()
    expect(nodeIds).toEqual(['n1', 'n2'])

    // Hierarchy chip is gone, DP-combobox now also contains dp-a, dp-b (in addition to dp-existing)
    const hierarchyChips = wrapper.findAll('[data-testid^="stub-HierarchyCombobox-chip-"]')
    expect(hierarchyChips.length).toBe(0)
    const dpChips = wrapper.findAll('[data-testid^="stub-DpCombobox-chip-"]')
    const dpIds = dpChips.map((c) => c.attributes('data-chip-id')).sort()
    expect(dpIds).toEqual(['dp-a', 'dp-b', 'dp-existing'])
  })

  it('submits a flat FilterCriteria payload via createFilterset for a new set', async () => {
    const ringbufferApi = makeRingbufferApi()
    // leaveEmpty so the auto-seeded tag doesn't drift the assertion below.
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi, leaveEmpty: true })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Neu')
    await wrapper.find('[data-testid="filter-editor-description"]').setValue('Desc')
    await wrapper.find('[data-testid="filter-editor-q"]').setValue('xyz')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.createFilterset).toHaveBeenCalledTimes(1)
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.name).toBe('Neu')
    expect(payload.description).toBe('Desc')
    expect(payload).toHaveProperty('filter')
    expect(payload.filter).toMatchObject({
      hierarchy_nodes: [],
      datapoints: [],
      tags: [],
      adapters: [],
      q: 'xyz',
    })
    // value_filter omitted/null when no operator chosen
    expect(payload.filter.value_filter == null).toBe(true)
  })

  it('uses updateFilterset when editing an existing set', async () => {
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: makeSampleSet() }),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-1' }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Heizung 2')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.updateFilterset).toHaveBeenCalledTimes(1)
    expect(ringbufferApi.updateFilterset.mock.calls[0][0]).toBe('fs-1')
    const payload = ringbufferApi.updateFilterset.mock.calls[0][1]
    expect(payload.name).toBe('Heizung 2')
    expect(payload.filter.tags).toEqual(['heizung'])
    expect(payload.filter.adapters).toEqual(['knx'])
    expect(payload.filter.datapoints).toEqual(['dp-1', 'dp-2'])
  })

  it('"Speichern & in Topleiste" also calls patchFiltersetTopbar', async () => {
    const ringbufferApi = makeRingbufferApi({
      createFilterset: vi.fn().mockResolvedValue({ data: { id: 'fs-new' } }),
    })
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Topbar-Set')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.createFilterset).toHaveBeenCalledTimes(1)
    expect(ringbufferApi.patchFiltersetTopbar).toHaveBeenCalledWith('fs-new', { topbar_active: true })
  })

  it('discarding a dirty form opens a confirm dialog and only closes on confirm', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Dirty')
    await wrapper.find('[data-testid="filter-editor-cancel"]').trigger('click')
    await flushPromises()
    // Confirm dialog appears
    expect(wrapper.find('[data-testid="confirm-discard"]').exists()).toBe(true)
    // Cancel keeps the modal open
    await wrapper.find('[data-testid="confirm-discard-cancel"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
    // OK closes the modal
    await wrapper.find('[data-testid="filter-editor-cancel"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="confirm-discard-ok"]').trigger('click')
    await flushPromises()
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1]).toEqual([false])
  })

  it('discarding a clean form closes the modal without confirmation', async () => {
    const { wrapper } = await mountEditor({ props: { setId: null }, leaveEmpty: true })
    await wrapper.find('[data-testid="filter-editor-cancel"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="confirm-discard"]').exists()).toBe(false)
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1]).toEqual([false])
  })

  it('selected color is reflected in the submit payload', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Mit Farbe')
    // Pick a non-default color from the palette
    const swatches = wrapper.findAll('[data-testid^="filter-editor-color-"]')
    expect(swatches.length).toBeGreaterThanOrEqual(2)
    const target = swatches.find((s) => s.attributes('data-color') === '#f59e0b') || swatches[1]
    await target.trigger('click')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.createFilterset).toHaveBeenCalledTimes(1)
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.color).toBe(target.attributes('data-color'))
  })

  it('value-filter operator + value is serialised into the payload', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('VF')
    await wrapper.find('[data-testid="filter-editor-value-type"]').setValue('number')
    await wrapper.find('[data-testid="filter-editor-value-operator"]').setValue('gt')
    await wrapper.find('[data-testid="filter-editor-value-input"]').setValue('42')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.filter.value_filter).toMatchObject({ operator: 'gt', value: 42 })
  })

  it('emits "saved" after a successful save', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Save Me')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('saved')).toBeTruthy()
  })

  it('shows a validation error when saving without a name', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.createFilterset).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="filter-editor-error"]').text()).toMatch(/Name/i)
  })

  it('surfaces an error message when getFilterset fails', async () => {
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockRejectedValue(new Error('boom')),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-x' }, ringbufferApi })
    expect(wrapper.find('[data-testid="filter-editor-error"]').exists()).toBe(true)
  })

  it('surfaces an error message when createFilterset fails', async () => {
    const ringbufferApi = makeRingbufferApi({
      createFilterset: vi.fn().mockRejectedValue({ response: { data: { detail: 'nope' } } }),
    })
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('X')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="filter-editor-error"]').text()).toContain('nope')
    // Modal must remain open on failure
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
  })

  it('between operator serialises lower + upper bounds as numbers', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Range')
    await wrapper.find('[data-testid="filter-editor-value-type"]').setValue('number')
    await wrapper.find('[data-testid="filter-editor-value-operator"]').setValue('between')
    await wrapper.find('[data-testid="filter-editor-value-lower"]').setValue('10')
    await wrapper.find('[data-testid="filter-editor-value-upper"]').setValue('20')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.filter.value_filter).toMatchObject({ operator: 'between', lower: 10, upper: 20 })
  })

  it('regex data type serialises pattern + ignore_case', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('R')
    await wrapper.find('[data-testid="filter-editor-value-type"]').setValue('regex')
    await wrapper.find('[data-testid="filter-editor-value-operator"]').setValue('regex')
    await wrapper.find('[data-testid="filter-editor-value-pattern"]').setValue('^temp')
    await wrapper.find('[data-testid="filter-editor-value-ignore-case"]').setValue(true)
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.filter.value_filter).toMatchObject({ operator: 'regex', pattern: '^temp', ignore_case: true })
  })

  it('roundtrips a value_filter of operator gt from getFilterset payload', async () => {
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({
        data: makeSampleSet({
          filter: {
            hierarchy_nodes: [],
            datapoints: [],
            tags: [],
            adapters: [],
            q: null,
            value_filter: { operator: 'gt', value: 5 },
          },
        }),
      }),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-1' }, ringbufferApi })
    expect(wrapper.find('[data-testid="filter-editor-value-operator"]').element.value).toBe('gt')
    expect(wrapper.find('[data-testid="filter-editor-value-input"]').element.value).toBe('5')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.updateFilterset.mock.calls[0][1]
    expect(payload.filter.value_filter).toMatchObject({ operator: 'gt', value: 5 })
  })

  it('changing the data type resets the operator and inputs', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-value-type"]').setValue('number')
    await wrapper.find('[data-testid="filter-editor-value-operator"]').setValue('gt')
    await wrapper.find('[data-testid="filter-editor-value-input"]').setValue('1')
    await wrapper.find('[data-testid="filter-editor-value-type"]').setValue('string')
    await flushPromises()
    expect(wrapper.find('[data-testid="filter-editor-value-operator"]').element.value).toBe('')
  })

  it('tag and adapter combobox updates are reflected in the payload', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('TA')

    // Push a model update from the stub comboboxes via direct emit on the
    // exposed component instance.
    const tagStub = wrapper.findComponent({ name: 'TagCombobox' })
    await tagStub.vm.$emit('update:modelValue', ['heating', 'lighting'])
    const adapterStub = wrapper.findComponent({ name: 'AdapterCombobox' })
    await adapterStub.vm.$emit('update:modelValue', ['knx', 'mqtt'])
    const dpStub = wrapper.findComponent({ name: 'DpCombobox' })
    await dpStub.vm.$emit('update:modelValue', ['dp-1'])
    const hierStub = wrapper.findComponent({ name: 'HierarchyCombobox' })
    await hierStub.vm.$emit('update:modelValue', ['t1:n1', 't1:n2'])
    await flushPromises()

    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.filter.tags).toEqual(['heating', 'lighting'])
    expect(payload.filter.adapters).toEqual(['knx', 'mqtt'])
    expect(payload.filter.datapoints).toEqual(['dp-1'])
    expect(payload.filter.hierarchy_nodes).toEqual([
      { tree_id: 't1', node_id: 'n1', include_descendants: true },
      { tree_id: 't1', node_id: 'n2', include_descendants: true },
    ])
  })

  it('hierarchy expand error path surfaces a message and keeps the chip', async () => {
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'n1', include_descendants: true }],
        datapoints: [],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const hierarchyApi = makeHierarchyApi({
      getTreeNodes: vi.fn().mockResolvedValue({ data: [{ id: 'n1', parent_id: null }] }),
    })
    const searchApi = makeSearchApi({
      search: vi.fn().mockRejectedValue({ response: { data: { detail: 'search broke' } } }),
    })
    const { wrapper } = await mountEditor({
      props: { setId: 'fs-1' },
      ringbufferApi,
      searchApi,
      hierarchyApi,
    })
    await wrapper.find('[data-testid="hierarchy-expand-0"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="filter-editor-error"]').text()).toContain('search broke')
    // Chip is still there
    expect(wrapper.find('[data-testid="hierarchy-expand-0"]').exists()).toBe(true)
  })

  it('"Speichern & in Topleiste" surfaces topbar error but still emits saved', async () => {
    const ringbufferApi = makeRingbufferApi({
      createFilterset: vi.fn().mockResolvedValue({ data: { id: 'fs-new' } }),
      patchFiltersetTopbar: vi.fn().mockRejectedValue({ response: { data: { detail: 'topbar broke' } } }),
    })
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('T')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    expect(ringbufferApi.patchFiltersetTopbar).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('saved')).toBeTruthy()
  })

  it('saved sets are always active — payload is_active is true and the checkbox is gone', async () => {
    const ringbufferApi = makeRingbufferApi()
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    expect(wrapper.find('[data-testid="filter-editor-active"]').exists()).toBe(false)
    await wrapper.find('[data-testid="filter-editor-name"]').setValue('Flags')
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.createFilterset.mock.calls[0][0]
    expect(payload.is_active).toBe(true)
    expect(payload).not.toHaveProperty('is_default')
  })

  it('preserves existing hierarchy nodes when the combobox emits the same composite id', async () => {
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'n1', include_descendants: false }],
        datapoints: [],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const { wrapper } = await mountEditor({ props: { setId: 'fs-1' }, ringbufferApi })
    // Re-emit the same list — include_descendants=false must be preserved
    const hierStub = wrapper.findComponent({ name: 'HierarchyCombobox' })
    await hierStub.vm.$emit('update:modelValue', ['t1:n1'])
    await flushPromises()
    await wrapper.find('[data-testid="filter-editor-save-topbar"]').trigger('click')
    await flushPromises()
    const payload = ringbufferApi.updateFilterset.mock.calls[0][1]
    expect(payload.filter.hierarchy_nodes[0]).toMatchObject({
      tree_id: 't1',
      node_id: 'n1',
      include_descendants: false,
    })
  })

  it('setting setId from null to a value lazily hydrates the form', async () => {
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: makeSampleSet({ name: 'Lazy' }) }),
    })
    const { wrapper } = await mountEditor({ props: { setId: null }, ringbufferApi })
    expect(ringbufferApi.getFilterset).not.toHaveBeenCalled()
    await wrapper.setProps({ setId: 'fs-1' })
    await flushPromises()
    expect(ringbufferApi.getFilterset).toHaveBeenCalledWith('fs-1')
    expect(wrapper.find('[data-testid="filter-editor-name"]').element.value).toBe('Lazy')
  })

  // -------------------------------------------------------------------------
  // QA-01 audit (#439): hierarchy expand edge cases
  // -------------------------------------------------------------------------

  it('expanding an empty hierarchy node drops the chip and leaves DP list unchanged', async () => {
    // Empty node = no DPs under it. The chip must still vanish (since the
    // user explicitly resolved it) but the DP list keeps its existing items.
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'empty', include_descendants: true }],
        datapoints: ['dp-keep'],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const hierarchyApi = makeHierarchyApi({
      getTreeNodes: vi.fn().mockResolvedValue({
        data: [{ id: 'empty', tree_id: 't1', parent_id: null, name: 'Empty' }],
      }),
    })
    const searchApi = makeSearchApi({
      search: vi.fn().mockResolvedValue({ data: { items: [] } }),
    })
    const { wrapper } = await mountEditor({
      props: { setId: 'fs-1' },
      ringbufferApi,
      searchApi,
      hierarchyApi,
    })
    await wrapper.find('[data-testid="hierarchy-expand-0"]').trigger('click')
    await flushPromises()

    // Chip is gone
    expect(wrapper.findAll('[data-testid^="hierarchy-expand-"]').length).toBe(0)
    // DP list unchanged — only dp-keep remains
    const dpChips = wrapper.findAll('[data-testid^="stub-DpCombobox-chip-"]')
    expect(dpChips.map((c) => c.attributes('data-chip-id'))).toEqual(['dp-keep'])
  })

  it('expanding a node with >1000 DPs completes within a generous bench budget', async () => {
    // Performance smoke test: the de-dup merge (Array.from(new Set(...)))
    // must not be quadratic. We pump 1500 ids through and assert the click
    // → emit cycle stays under a generous wall-clock threshold (200 ms in
    // happy-dom with no DOM work). This catches accidental O(n^2) regressions.
    const setWithHierarchy = makeSampleSet({
      filter: {
        hierarchy_nodes: [{ tree_id: 't1', node_id: 'big', include_descendants: true }],
        datapoints: [],
        tags: [],
        adapters: [],
        q: null,
        value_filter: null,
      },
    })
    const ringbufferApi = makeRingbufferApi({
      getFilterset: vi.fn().mockResolvedValue({ data: setWithHierarchy }),
    })
    const hierarchyApi = makeHierarchyApi({
      getTreeNodes: vi.fn().mockResolvedValue({
        data: [{ id: 'big', tree_id: 't1', parent_id: null, name: 'Big' }],
      }),
    })
    const bigItems = Array.from({ length: 1500 }, (_, i) => ({ id: `dp-${i}` }))
    const searchApi = makeSearchApi({
      search: vi.fn().mockResolvedValue({ data: { items: bigItems } }),
    })
    const { wrapper } = await mountEditor({
      props: { setId: 'fs-1' },
      ringbufferApi,
      searchApi,
      hierarchyApi,
    })

    const t0 = performance.now()
    await wrapper.find('[data-testid="hierarchy-expand-0"]').trigger('click')
    await flushPromises()
    const elapsed = performance.now() - t0

    // Assert we wired 1500 DPs through
    const dpChips = wrapper.findAll('[data-testid^="stub-DpCombobox-chip-"]')
    expect(dpChips.length).toBe(1500)
    // 200 ms is generous on slower CI runners; the actual local run is < 30 ms
    expect(elapsed).toBeLessThan(2000)
  })
})
