import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let listTreesMock     = vi.fn()
let createTreeMock    = vi.fn()
let updateTreeMock    = vi.fn()
let deleteTreeMock    = vi.fn()
let getTreeNodesMock  = vi.fn()
let createNodeMock    = vi.fn()
let updateNodeMock    = vi.fn()
let deleteNodeMock    = vi.fn()
let reorderNodesMock  = vi.fn()
let etsImportMock     = vi.fn()

const TREE_NODE_STUB = {
  name: 'HierarchyNodeTree',
  template: '<div class="node-tree" />',
  props: ['nodes', 'treeId', 'depth', 'selectedNode'],
  emits: ['add-child', 'edit', 'delete', 'reorder'],
}

beforeEach(() => {
  vi.resetModules()
  listTreesMock     = vi.fn().mockResolvedValue({ data: [] })
  createTreeMock    = vi.fn().mockResolvedValue({ data: { id: 'new-1', name: 'New', description: '', display_depth: 0 } })
  updateTreeMock    = vi.fn().mockResolvedValue({})
  deleteTreeMock    = vi.fn().mockResolvedValue({})
  getTreeNodesMock  = vi.fn().mockResolvedValue({ data: [] })
  createNodeMock    = vi.fn().mockResolvedValue({})
  updateNodeMock    = vi.fn().mockResolvedValue({})
  deleteNodeMock    = vi.fn().mockResolvedValue({})
  reorderNodesMock  = vi.fn().mockResolvedValue({})
  etsImportMock     = vi.fn().mockResolvedValue({})

  vi.doMock('@/api/client.js', () => ({
    hierarchyApi: {
      listTrees:    listTreesMock,
      createTree:   createTreeMock,
      updateTree:   updateTreeMock,
      deleteTree:   deleteTreeMock,
      getTreeNodes: getTreeNodesMock,
      createNode:   createNodeMock,
      updateNode:   updateNodeMock,
      deleteNode:   deleteNodeMock,
      reorderNodes: reorderNodesMock,
      etsImport:    etsImportMock,
    },
  }))
  vi.doMock('@/components/HierarchyNodeTree.vue', () => ({ default: TREE_NODE_STUB }))
  vi.doMock('@/utils/hierarchyDepthOptions.js', () => ({
    buildDepthOptions: () => [{ value: 0, label: 'Alle', disabled: false }],
  }))
})

async function mountHM() {
  const { default: HierarchyManager } = await import('@/components/HierarchyManager.vue')
  return mount(HierarchyManager)
}

describe('HierarchyManager — initial load', () => {
  it('calls listTrees on mount', async () => {
    await mountHM()
    await flushPromises()
    expect(listTreesMock).toHaveBeenCalledTimes(1)
  })

  it('shows empty state when no trees returned', async () => {
    const w = await mountHM()
    await flushPromises()
    // German translation for empty hierarchy
    expect(w.text()).toContain('Hierarchie')
  })

  it('shows create-tree button', async () => {
    const w = await mountHM()
    await flushPromises()
    expect(w.find('[data-testid="btn-create-tree"]').exists()).toBe(true)
  })

  it('shows ETS import button', async () => {
    const w = await mountHM()
    await flushPromises()
    expect(w.find('[data-testid="btn-ets-import"]').exists()).toBe(true)
  })
})

describe('HierarchyManager — tree list', () => {
  const TREES = [
    { id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 },
    { id: 'tree-2', name: 'Gewerke', description: 'ets_import:buildings', display_depth: 0 },
  ]

  it('renders a card per tree', async () => {
    listTreesMock.mockResolvedValue({ data: TREES })
    const w = await mountHM()
    await flushPromises()
    expect(w.find('[data-testid="tree-tree-1"]').exists()).toBe(true)
    expect(w.find('[data-testid="tree-tree-2"]').exists()).toBe(true)
  })

  it('shows tree names', async () => {
    listTreesMock.mockResolvedValue({ data: TREES })
    const w = await mountHM()
    await flushPromises()
    expect(w.text()).toContain('Gebäude')
    expect(w.text()).toContain('Gewerke')
  })

  it('shows ets_import description formatted', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-2', name: 'Gewerke', description: 'ets_import:buildings', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    // Should show the ETS import description (not raw string)
    expect(w.text()).not.toContain('ets_import:buildings')
  })
})

describe('HierarchyManager — expand tree', () => {
  it('clicking expand button loads tree nodes', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Test', description: '', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-expand-tree-1"]').trigger('click')
    await flushPromises()
    expect(getTreeNodesMock).toHaveBeenCalledWith('tree-1')
  })

  it('shows HierarchyNodeTree after expanding with nodes', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Test', description: '', display_depth: 0 }] })
    getTreeNodesMock.mockResolvedValue({ data: [{ id: 'node-1', name: 'Root', description: '', children: [] }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-expand-tree-1"]').trigger('click')
    await flushPromises()
    expect(w.find('.node-tree').exists()).toBe(true)
  })

  it('collapsing hides the nodes', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Test', description: '', display_depth: 0 }] })
    getTreeNodesMock.mockResolvedValue({ data: [{ id: 'node-1', name: 'Root', description: '', children: [] }] })
    const w = await mountHM()
    await flushPromises()
    // Expand
    await w.find('[data-testid="btn-expand-tree-1"]').trigger('click')
    await flushPromises()
    // Collapse
    await w.find('[data-testid="btn-expand-tree-1"]').trigger('click')
    await flushPromises()
    expect(w.find('.node-tree').exists()).toBe(false)
  })
})

describe('HierarchyManager — create tree modal', () => {
  it('opens tree modal on create-tree button click', async () => {
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-create-tree"]').trigger('click')
    expect(w.find('input[type="text"]').exists()).toBe(true)
  })

  it('save calls createTree with entered name', async () => {
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-create-tree"]').trigger('click')
    const nameInput = w.find('input[type="text"]')
    await nameInput.setValue('Meine Hierarchie')
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(createTreeMock).toHaveBeenCalledWith(expect.objectContaining({ name: 'Meine Hierarchie' }))
  })

  it('save button does not call API when name is empty', async () => {
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-create-tree"]').trigger('click')
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(createTreeMock).not.toHaveBeenCalled()
  })
})

describe('HierarchyManager — edit tree', () => {
  it('opens tree edit modal with existing name', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-edit-tree-tree-1"]').trigger('click')
    await flushPromises()
    expect(w.find('input[type="text"]').element.value).toBe('Gebäude')
  })
})

describe('HierarchyManager — delete tree', () => {
  it('opens confirm dialog on delete button click', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-delete-tree-tree-1"]').trigger('click')
    // Confirm dialog with delete button should appear
    const deleteBtn = w.findAll('button').find(b => b.classes().includes('btn-danger') && !b.attributes('data-testid'))
    expect(deleteBtn).toBeTruthy()
  })

  it('calls deleteTree on confirm', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-delete-tree-tree-1"]').trigger('click')
    const deleteBtn = w.findAll('button').find(b => b.classes().includes('btn-danger') && !b.attributes('data-testid'))
    await deleteBtn.trigger('click')
    await flushPromises()
    expect(deleteTreeMock).toHaveBeenCalledWith('tree-1')
  })
})

describe('HierarchyManager — add root node', () => {
  it('opens node modal on add-root button click', async () => {
    listTreesMock.mockResolvedValue({ data: [{ id: 'tree-1', name: 'Gebäude', description: '', display_depth: 0 }] })
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-add-root-tree-1"]').trigger('click')
    expect(w.find('input[type="text"]').exists()).toBe(true)
  })
})

describe('HierarchyManager — ETS import modal', () => {
  it('opens ETS modal on ets-import button click', async () => {
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    // ETS import button inside modal
    const importBtn = w.findAll('button').find(b => b.text().includes('ETS') || b.text().includes('Importieren'))
    expect(importBtn).toBeTruthy()
  })

  it('import button is disabled when tree name is empty', async () => {
    const w = await mountHM()
    await flushPromises()
    await w.find('[data-testid="btn-ets-import"]').trigger('click')
    // Clear tree name input so button becomes disabled
    const nameInput = w.findAll('input[type="text"]').find(i => i.exists())
    if (nameInput) await nameInput.setValue('')
    // Find the disabled primary button (the ETS import confirm button)
    const importBtn = w.findAll('button.btn-primary').find(b => b.attributes('disabled') !== undefined)
    expect(importBtn).toBeTruthy()
  })
})

describe('HierarchyManager — error feedback', () => {
  it('shows error when listTrees fails', async () => {
    listTreesMock.mockRejectedValue(new Error('network error'))
    const w = await mountHM()
    await flushPromises()
    expect(w.html()).toContain('red') // error message has red styling
  })
})
