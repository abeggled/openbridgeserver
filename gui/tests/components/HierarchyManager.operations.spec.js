import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let listTreesMock, createTreeMock, updateTreeMock, deleteTreeMock, getTreeNodesMock
let createNodeMock, updateNodeMock, deleteNodeMock, importFromEtsMock

const TREE_NODE_STUB = {
  name: 'HierarchyNodeTree',
  template: '<div class="node-tree" />',
  props: ['nodes', 'treeId', 'depth', 'selectedNode'],
  emits: ['add-child', 'edit', 'delete', 'reorder'],
}

const TREE = { id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 }
const NODE = { id: 'node-1', name: 'Erdgeschoss', tree_id: 'tree-1', parent_id: null, description: '', children: [] }
const CHILD = { id: 'node-2', name: 'Büro', tree_id: 'tree-1', parent_id: 'node-1', description: '', children: [] }

beforeEach(() => {
  vi.resetModules()
  listTreesMock      = vi.fn().mockResolvedValue({ data: [TREE] })
  createTreeMock     = vi.fn().mockResolvedValue({ data: { id: 'new-1', name: 'New', description: '', display_depth: 0 } })
  updateTreeMock     = vi.fn().mockResolvedValue({})
  deleteTreeMock     = vi.fn().mockResolvedValue({})
  getTreeNodesMock   = vi.fn().mockResolvedValue({ data: [NODE] })
  createNodeMock     = vi.fn().mockResolvedValue({})
  updateNodeMock     = vi.fn().mockResolvedValue({})
  deleteNodeMock     = vi.fn().mockResolvedValue({})
  importFromEtsMock  = vi.fn().mockResolvedValue({ data: { message: '3 Knoten importiert', tree_id: 'new-tree' } })

  vi.doMock('@/api/client.js', () => ({
    hierarchyApi: {
      listTrees:     listTreesMock,
      createTree:    createTreeMock,
      updateTree:    updateTreeMock,
      deleteTree:    deleteTreeMock,
      getTreeNodes:  getTreeNodesMock,
      createNode:    createNodeMock,
      updateNode:    updateNodeMock,
      deleteNode:    deleteNodeMock,
      importFromEts: importFromEtsMock,
    },
  }))
  vi.doMock('@/components/HierarchyNodeTree.vue', () => ({ default: TREE_NODE_STUB }))
  vi.doMock('@/utils/hierarchyDepthOptions.js', () => ({
    buildDepthOptions: () => [{ value: 0, label: 'Alle', disabled: false }],
  }))
})

async function mountHM() {
  const { default: HierarchyManager } = await import('@/components/HierarchyManager.vue')
  const w = mount(HierarchyManager, { attachTo: document.body })
  await flushPromises()
  return w
}

async function mountWithExpandedTree() {
  const w = await mountHM()
  await w.find('[data-testid="btn-expand-tree-1"]').trigger('click')
  await flushPromises()
  return w
}

function getTreeStub(w) {
  return w.findComponent({ name: 'HierarchyNodeTree' })
}

// ─── saveTree — update path ───────────────────────────────────────────────────

describe('HierarchyManager — saveTree update path', () => {
  it('calls updateTree when saving from edit modal', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-edit-tree-tree-1"]').trigger('click')
    await flushPromises()

    const nameInput = w.find('input[type="text"]')
    await nameInput.setValue('Gebäude Umbenannt')
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(updateTreeMock).toHaveBeenCalledWith('tree-1', expect.objectContaining({ name: 'Gebäude Umbenannt' }))
    expect(createTreeMock).not.toHaveBeenCalled()
    w.unmount()
  })

  it('closes modal after successful update', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-edit-tree-tree-1"]').trigger('click')
    await flushPromises()

    await w.find('input[type="text"]').setValue('Neu')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    // Modal closed — no text input visible anymore
    expect(w.find('input[type="text"]').exists()).toBe(false)
    w.unmount()
  })
})

// ─── saveTree — error path ────────────────────────────────────────────────────

describe('HierarchyManager — saveTree error handling', () => {
  it('shows API error detail in tree modal when save rejects', async () => {
    createTreeMock.mockRejectedValue({ response: { data: { detail: 'Name bereits vergeben' } } })
    const w = await mountHM()
    await w.find('[data-testid="btn-create-tree"]').trigger('click')
    await w.find('input[type="text"]').setValue('Duplikat')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Name bereits vergeben')
    w.unmount()
  })

  it('shows fallback error when API rejects without detail', async () => {
    createTreeMock.mockRejectedValue(new Error('network'))
    const w = await mountHM()
    await w.find('[data-testid="btn-create-tree"]').trigger('click')
    await w.find('input[type="text"]').setValue('Test')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    // Falls back to t('hierarchy.errorSaving')
    expect(w.find('[class*="red"]').exists()).toBe(true)
    w.unmount()
  })
})

// ─── saveNode — create path ───────────────────────────────────────────────────

describe('HierarchyManager — saveNode create (addRootNode)', () => {
  it('calls createNode when saving from add-root modal', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-add-root-tree-1"]').trigger('click')
    await flushPromises()

    const nameInput = w.find('input[type="text"]')
    await nameInput.setValue('Neuer Knoten')
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(createNodeMock).toHaveBeenCalledWith(expect.objectContaining({
      tree_id: 'tree-1',
      parent_id: null,
      name: 'Neuer Knoten',
    }))
    w.unmount()
  })

  it('does not call createNode when name is empty', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-add-root-tree-1"]').trigger('click')
    await flushPromises()

    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    expect(createNodeMock).not.toHaveBeenCalled()
    w.unmount()
  })

  it('shows error when createNode rejects', async () => {
    createNodeMock.mockRejectedValue({ response: { data: { detail: 'Knoten existiert bereits' } } })
    const w = await mountHM()
    await w.find('[data-testid="btn-add-root-tree-1"]').trigger('click')
    await flushPromises()

    await w.find('input[type="text"]').setValue('Dup')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Knoten existiert bereits')
    w.unmount()
  })
})

// ─── saveNode — update path (via HierarchyNodeTree @edit event) ───────────────

describe('HierarchyManager — saveNode update (openEditNode)', () => {
  it('calls updateNode when saving from edit-node modal', async () => {
    const w = await mountWithExpandedTree()
    const stub = getTreeStub(w)
    expect(stub.exists()).toBe(true)

    // HierarchyNodeTree emits 'edit' with a node object → opens edit modal
    await stub.vm.$emit('edit', NODE)
    await flushPromises()

    // Modal should be open with node's name pre-filled
    expect(w.find('input[type="text"]').element.value).toBe('Erdgeschoss')

    await w.find('input[type="text"]').setValue('Erdgeschoss OG')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    expect(updateNodeMock).toHaveBeenCalledWith('node-1', expect.objectContaining({ name: 'Erdgeschoss OG' }))
    w.unmount()
  })
})

// ─── openAddChildNode (via HierarchyNodeTree @add-child event) ────────────────

describe('HierarchyManager — openAddChildNode', () => {
  it('opens node modal with parentId set when @add-child emitted', async () => {
    const w = await mountWithExpandedTree()
    const stub = getTreeStub(w)

    await stub.vm.$emit('add-child', NODE)
    await flushPromises()

    // Node modal is open (create mode)
    expect(w.find('input[type="text"]').exists()).toBe(true)

    // Save to verify parentId is set
    await w.find('input[type="text"]').setValue('Kind-Knoten')
    await w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern')).trigger('click')
    await flushPromises()

    expect(createNodeMock).toHaveBeenCalledWith(expect.objectContaining({
      parent_id: 'node-1',
      tree_id: 'tree-1',
    }))
    w.unmount()
  })
})

// ─── confirmDeleteNode (via HierarchyNodeTree @delete event) ─────────────────

describe('HierarchyManager — confirmDeleteNode', () => {
  it('shows confirm dialog when @delete emitted', async () => {
    const w = await mountWithExpandedTree()
    await getTreeStub(w).vm.$emit('delete', NODE)
    await flushPromises()

    // Confirm dialog visible — btn-danger is the confirm delete button
    expect(w.findAll('button').some(b => b.classes().includes('btn-danger') && !b.attributes('data-testid'))).toBe(true)
    w.unmount()
  })

  it('calls deleteNode on confirm', async () => {
    const w = await mountWithExpandedTree()
    await getTreeStub(w).vm.$emit('delete', NODE)
    await flushPromises()

    const confirmBtn = w.findAll('button').find(b => b.classes().includes('btn-danger') && !b.attributes('data-testid'))
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(deleteNodeMock).toHaveBeenCalledWith('node-1')
    w.unmount()
  })
})

// ─── reorderNode (via HierarchyNodeTree @reorder event) ──────────────────────

describe('HierarchyManager — reorderNode', () => {
  it('calls updateNode for both nodes when @reorder emitted', async () => {
    getTreeNodesMock.mockResolvedValue({ data: [NODE, CHILD] })
    const w = await mountWithExpandedTree()

    await getTreeStub(w).vm.$emit('reorder', {
      node:      NODE,
      siblings:  [NODE, CHILD],
      index:     0,
      direction: 'down',
    })
    await flushPromises()

    expect(updateNodeMock).toHaveBeenCalledTimes(2)
    expect(updateNodeMock).toHaveBeenCalledWith('node-1', { order: 1 })
    expect(updateNodeMock).toHaveBeenCalledWith('node-2', { order: 0 })
    w.unmount()
  })

  it('does nothing when direction=up at first position', async () => {
    const w = await mountWithExpandedTree()

    await getTreeStub(w).vm.$emit('reorder', {
      node:      NODE,
      siblings:  [NODE],
      index:     0,
      direction: 'up',
    })
    await flushPromises()

    // swapIndex would be -1 → early return
    expect(updateNodeMock).not.toHaveBeenCalled()
    w.unmount()
  })
})

// ─── doEtsImport — happy path ─────────────────────────────────────────────────

// The ETS modal confirm button has no data-testid but its text ends with "ETS importieren".
// The toolbar btn-ets-import HAS data-testid, so we exclude it.
function findEtsConfirmBtn(w) {
  return w.findAll('button').find(b => !b.attributes('data-testid') && b.text().includes('ETS importieren'))
}

describe('HierarchyManager — doEtsImport', () => {
  it('calls importFromEts with the entered tree name and mode', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    await flushPromises()

    const nameInput = w.findAll('input[type="text"]').find(i => i.exists())
    await nameInput.setValue('KNX Import')
    await flushPromises()

    await findEtsConfirmBtn(w).trigger('click')
    await flushPromises()

    expect(importFromEtsMock).toHaveBeenCalledWith(expect.objectContaining({
      tree_name: 'KNX Import',
      mode: 'groups',
    }))
    w.unmount()
  })

  it('shows success message after import', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    await flushPromises()

    await w.findAll('input[type="text"]').find(i => i.exists()).setValue('Test Import')
    await flushPromises()
    await findEtsConfirmBtn(w).trigger('click')
    await flushPromises()

    expect(w.text()).toContain('3 Knoten importiert')
    w.unmount()
  })

  it('shows error message when importFromEts rejects', async () => {
    importFromEtsMock.mockRejectedValue({ response: { data: { detail: 'Datei ungültig' } } })
    const w = await mountHM()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    await flushPromises()

    await w.findAll('input[type="text"]').find(i => i.exists()).setValue('Fehler Import')
    await flushPromises()
    await findEtsConfirmBtn(w).trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Datei ungültig')
    w.unmount()
  })

  it('shows autoLink checkbox when mode is buildings', async () => {
    const w = await mountHM()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    await flushPromises()

    const modeSelect = w.find('select.input.text-sm')
    await modeSelect.setValue('buildings')
    await flushPromises()

    // autoLink checkbox shown for buildings/trades
    expect(w.find('#auto-link').exists()).toBe(true)
    w.unmount()
  })
})
