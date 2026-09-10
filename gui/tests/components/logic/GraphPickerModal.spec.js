/**
 * Tests for the Logic editor's "open graph" drill-down popup (#1217):
 * forest → tree → node navigation, breadcrumb back-nav, graph selection.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const pushMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  pushMock.mockClear()
  vi.doMock('vue-router', () => ({ useRouter: () => ({ push: pushMock }) }))
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client.js')
  vi.doUnmock('vue-router')
})

const TREES = [{ id: 't1', name: 'Technisch' }, { id: 't2', name: 'Topologisch' }]
const T1_ROOT = [{ id: 'n1', name: 'Beschattung', has_children: false }, { id: 'n2', name: 'Licht', has_children: true }]
const N2_CHILDREN = [{ id: 'n2a', name: 'Wohnzimmer', has_children: false }]
const N2_GRAPHS = [
  { id: 'g1', name: 'Licht WZ', enabled: true, link_id: 'l1' },
  { id: 'g2', name: 'Licht Alt', enabled: false, link_id: 'l2' },
]

async function mountModal({ browseImpl, deleteLogicGraphLinkByIdImpl, deleteGraphImpl } = {}) {
  const browse = browseImpl || vi.fn().mockImplementation((params = {}) => {
    if (!params.tree_id) return Promise.resolve({ data: { trees: TREES, subfolders: [], logic_graphs: [] } })
    if (params.tree_id === 't1' && !params.node_id) return Promise.resolve({ data: { trees: [], subfolders: T1_ROOT, logic_graphs: [] } })
    if (params.tree_id === 't1' && params.node_id === 'n2') return Promise.resolve({ data: { trees: [], subfolders: N2_CHILDREN, logic_graphs: N2_GRAPHS } })
    return Promise.resolve({ data: { trees: [], subfolders: [], logic_graphs: [] } })
  })
  const deleteLogicGraphLinkById = deleteLogicGraphLinkByIdImpl || vi.fn().mockResolvedValue({})
  const deleteGraph = deleteGraphImpl || vi.fn().mockResolvedValue({})
  // stores/logic.js (used via useLogicStore() for the unassigned-folder
  // "Löschen" action) imports logicApi from this same module — mocked here
  // too so that path resolves instead of hitting the real backend.
  vi.doMock('@/api/client.js', () => ({ hierarchyApi: { browse, deleteLogicGraphLinkById }, logicApi: { deleteGraph } }))
  const { default: GraphPickerModal } = await import('@/components/logic/GraphPickerModal.vue')
  const wrapper = mount(GraphPickerModal, {
    props: { modelValue: true },
    global: {
      // Modal.vue renders its content via <Teleport to="body">, which Vue
      // Test Utils' wrapper.find() cannot see even with attachTo — stub it
      // with a plain div exposing the same slots, same pattern already used
      // for LogicView's own modals in tests/views/LogicView.*.spec.js. This
      // also stubs ConfirmDialog's own internal <Modal> use (global stubs
      // apply wherever a component with this name renders, not just at the
      // top level) — the footer slot is forwarded too so its Cancel/Confirm
      // buttons render.
      stubs: {
        Modal: { template: '<div><slot name="header-actions" /><slot /><slot name="footer" /></div>' },
      },
    },
  })
  await flushPromises()
  return { wrapper, browse, deleteLogicGraphLinkById, deleteGraph }
}

describe('GraphPickerModal — navigation', () => {
  it('opening at modelValue=true loads the tree list', async () => {
    const { wrapper, browse } = await mountModal()
    expect(browse).toHaveBeenCalledWith({})
    expect(wrapper.find('[data-testid="picker-tree-t1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="picker-tree-t2"]').exists()).toBe(true)
  })

  it('clicking a tree drills into its top-level nodes', async () => {
    const { wrapper, browse } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({ tree_id: 't1' })
    expect(wrapper.find('[data-testid="picker-folder-n1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="picker-folder-n2"]').exists()).toBe(true)
  })

  it('clicking a subfolder drills one level deeper and shows linked graphs', async () => {
    const { wrapper, browse } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-folder-n2"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({ tree_id: 't1', node_id: 'n2' })
    expect(wrapper.find('[data-testid="picker-folder-n2a"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="picker-graph-g1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Licht WZ')

    // A disabled graph shows the same disabled-suffix convention as the old
    // flat <select> did, and is visually muted.
    const disabled = wrapper.find('[data-testid="picker-graph-g2"]')
    expect(disabled.exists()).toBe(true)
    expect(disabled.text()).toContain('Licht Alt')
    expect(disabled.text()).toContain('(deaktiviert)')
    expect(disabled.find('span').classes()).toContain('text-slate-400')
  })

  it('breadcrumb shows the current path and root/tree crumbs navigate back', async () => {
    const { wrapper, browse } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-folder-n2"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="crumb-tree"]').text()).toBe('Technisch')
    expect(wrapper.find('[data-testid="crumb-node-n2"]').text()).toBe('Licht')

    browse.mockClear()
    await wrapper.find('[data-testid="crumb-tree"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({ tree_id: 't1' })
    expect(wrapper.find('[data-testid="picker-folder-n2a"]').exists()).toBe(false)

    browse.mockClear()
    await wrapper.find('[data-testid="crumb-root"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({})
    expect(wrapper.find('[data-testid="picker-tree-t1"]').exists()).toBe(true)
  })

  it('clicking a logic graph emits select with its id and closes the modal', async () => {
    const { wrapper } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-folder-n2"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="picker-graph-g1"]').trigger('click')

    expect(wrapper.emitted('select')).toEqual([['g1']])
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([false])
  })

  it('shows the empty-hierarchies state when there are no trees', async () => {
    const browse = vi.fn().mockResolvedValue({ data: { trees: [], subfolders: [], logic_graphs: [] } })
    const { wrapper } = await mountModal({ browseImpl: browse })
    expect(wrapper.text()).toContain('Es sind noch keine Hierarchien vorhanden.')
  })

  it('shows the empty-folder state for a folder with neither subfolders nor graphs', async () => {
    const browse = vi.fn().mockImplementation((params = {}) => {
      if (!params.tree_id) return Promise.resolve({ data: { trees: TREES, subfolders: [], logic_graphs: [] } })
      return Promise.resolve({ data: { trees: [], subfolders: [], logic_graphs: [] } })
    })
    const { wrapper } = await mountModal({ browseImpl: browse })
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Dieser Ordner ist leer.')
  })

  it('shows an error message when browse rejects', async () => {
    const browse = vi.fn().mockRejectedValue(new Error('fail'))
    const { wrapper } = await mountModal({ browseImpl: browse })
    expect(wrapper.text()).toContain('Konnte nicht geladen werden.')
  })

  it('re-opening resets navigation back to the root level', async () => {
    const { wrapper, browse } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()

    await wrapper.setProps({ modelValue: false })
    browse.mockClear()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(browse).toHaveBeenCalledWith({})
    expect(wrapper.find('[data-testid="picker-tree-t1"]').exists()).toBe(true)
  })

  it('"Logiken organisieren" navigates to Settings → Hierarchy and closes the modal', async () => {
    const { wrapper } = await mountModal()
    await wrapper.find('[data-testid="btn-organize-graphs"]').trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/settings?tab=hierarchy')
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([false])
  })
})

describe('GraphPickerModal — "hier entfernen" (#1217 hierarchy-position removal)', () => {
  it('shows a remove-here button (not delete) for a graph inside a normal folder', async () => {
    const { wrapper } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-folder-n2"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="picker-graph-remove-g1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="picker-graph-remove-g1"]').text()).toBe('hier entfernen')
    expect(wrapper.find('[data-testid="picker-graph-delete-g1"]').exists()).toBe(false)
  })

  it('clicking remove-here unlinks by link_id and refreshes the current listing', async () => {
    const { wrapper, browse, deleteLogicGraphLinkById } = await mountModal()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-folder-n2"]').trigger('click')
    await flushPromises()
    browse.mockClear()

    await wrapper.find('[data-testid="picker-graph-remove-g1"]').trigger('click')
    await flushPromises()

    expect(deleteLogicGraphLinkById).toHaveBeenCalledWith('l1')
    expect(browse).toHaveBeenCalledWith({ tree_id: 't1', node_id: 'n2' })
    // Removing here is not a selection — the modal stays open.
    expect(wrapper.emitted('select')).toBeFalsy()
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
  })
})

describe('GraphPickerModal — unassigned pseudo-folder (#1217 follow-up)', () => {
  const UNASSIGNED_GRAPHS = [{ id: 'u1', name: 'Streuner', enabled: true }]

  async function mountWithUnassigned(hasUnassigned = true) {
    const browse = vi.fn().mockImplementation((params = {}) => {
      if (params.unassigned) return Promise.resolve({ data: { trees: [], subfolders: [], logic_graphs: UNASSIGNED_GRAPHS } })
      if (!params.tree_id) return Promise.resolve({ data: { trees: TREES, subfolders: [], logic_graphs: [], has_unassigned_logic_graphs: hasUnassigned } })
      return Promise.resolve({ data: { trees: [], subfolders: [], logic_graphs: [] } })
    })
    return { ...(await mountModal({ browseImpl: browse })), browse }
  }

  it('shows the pseudo-folder at the root level when has_unassigned_logic_graphs is true', async () => {
    const { wrapper } = await mountWithUnassigned(true)
    expect(wrapper.find('[data-testid="picker-unassigned"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Nicht zugeordnet')
  })

  it('hides the pseudo-folder when has_unassigned_logic_graphs is false', async () => {
    const { wrapper } = await mountWithUnassigned(false)
    expect(wrapper.find('[data-testid="picker-unassigned"]').exists()).toBe(false)
  })

  it('clicking the pseudo-folder browses with unassigned=true and shows a flat graph list', async () => {
    const { wrapper, browse } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({ unassigned: true })
    expect(wrapper.find('[data-testid="picker-graph-u1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="crumb-unassigned"]').text()).toBe('Nicht zugeordnet')
    // Never a further drill-down step — no tree/folder crumb alongside it.
    expect(wrapper.find('[data-testid="crumb-tree"]').exists()).toBe(false)
  })

  it('picking a graph from the pseudo-folder selects it and closes the modal', async () => {
    const { wrapper } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="picker-graph-u1"]').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['u1']])
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([false])
  })

  it('going back to root from the pseudo-folder leaves unassigned mode', async () => {
    const { wrapper, browse } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()

    browse.mockClear()
    await wrapper.find('[data-testid="crumb-root"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({})
    expect(wrapper.find('[data-testid="crumb-unassigned"]').exists()).toBe(false)

    browse.mockClear()
    await wrapper.find('[data-testid="picker-tree-t1"]').trigger('click')
    await flushPromises()
    expect(browse).toHaveBeenCalledWith({ tree_id: 't1' })  // no stray unassigned=true leaking in
  })

  it('shows a delete button (not remove-here) for graphs in the unassigned folder', async () => {
    const { wrapper } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="picker-graph-delete-u1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="picker-graph-delete-u1"]').text()).toBe('Löschen')
    expect(wrapper.find('[data-testid="picker-graph-remove-u1"]').exists()).toBe(false)
  })

  it('clicking delete asks for confirmation before deleting anything', async () => {
    const { wrapper, deleteGraph } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="picker-graph-delete-u1"]').trigger('click')
    await flushPromises()

    expect(deleteGraph).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Streuner')
  })

  it('confirming deletes the graph, emits graph-deleted, and refreshes the listing', async () => {
    const { wrapper, browse, deleteGraph } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()
    browse.mockClear()

    await wrapper.find('[data-testid="picker-graph-delete-u1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="btn-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteGraph).toHaveBeenCalledWith('u1')
    expect(wrapper.emitted('graph-deleted')).toEqual([['u1']])
    expect(browse).toHaveBeenCalledWith({ unassigned: true })
    // Deleting is not a selection — the modal stays open.
    expect(wrapper.emitted('select')).toBeFalsy()
  })

  it('cancelling the confirmation deletes nothing', async () => {
    const { wrapper, deleteGraph } = await mountWithUnassigned(true)
    await wrapper.find('[data-testid="picker-unassigned"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-testid="picker-graph-delete-u1"]').trigger('click')
    await flushPromises()
    // ConfirmDialog's Cancel button — no dedicated testid, select by text.
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === 'Abbrechen')
    await cancelBtn.trigger('click')
    await flushPromises()

    expect(deleteGraph).not.toHaveBeenCalled()
    expect(wrapper.emitted('graph-deleted')).toBeFalsy()
  })
})
