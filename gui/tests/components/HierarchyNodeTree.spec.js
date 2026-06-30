import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import HierarchyNodeTree from '@/components/HierarchyNodeTree.vue'

function mk(nodes = [], depth = 0) {
  return mount(HierarchyNodeTree, {
    props: { nodes, treeId: 'tree-1', depth, selectedNode: null },
  })
}

const FLAT_NODES = [
  { id: 'a', name: 'Alpha', description: 'First', children: [] },
  { id: 'b', name: 'Beta',  description: '',       children: [] },
]

const TREE_NODES = [
  {
    id: 'root',
    name: 'Root',
    description: '',
    children: [
      { id: 'child', name: 'Child', description: '', children: [] },
    ],
  },
]

describe('HierarchyNodeTree — rendering', () => {
  it('renders a list item for each node', () => {
    const w = mk(FLAT_NODES)
    expect(w.findAll('li').length).toBe(2)
  })

  it('shows node names', () => {
    const w = mk(FLAT_NODES)
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('Beta')
  })

  it('shows node description when present', () => {
    const w = mk(FLAT_NODES)
    expect(w.text()).toContain('First')
  })

  it('renders data-testid for each node row', () => {
    const w = mk(FLAT_NODES)
    expect(w.find('[data-testid="node-a"]').exists()).toBe(true)
    expect(w.find('[data-testid="node-b"]').exists()).toBe(true)
  })

  it('renders no expand button for leaf nodes (shows spacer span instead)', () => {
    const w = mk([{ id: 'leaf', name: 'Leaf', description: '', children: [] }])
    // Expand button has class shrink-0; leaf nodes show a <span> placeholder instead
    expect(w.find('button.shrink-0').exists()).toBe(false)
  })

  it('renders expand button for nodes with children', () => {
    const w = mk(TREE_NODES)
    // The expand button uniquely has the shrink-0 class
    expect(w.find('button.shrink-0').exists()).toBe(true)
  })
})

describe('HierarchyNodeTree — expand / collapse', () => {
  it('children are hidden before expanding', () => {
    const w = mk(TREE_NODES)
    expect(w.find('[data-testid="node-child"]').exists()).toBe(false)
  })

  it('clicking expand shows children', async () => {
    const w = mk(TREE_NODES)
    await w.find('button.shrink-0').trigger('click')
    expect(w.find('[data-testid="node-child"]').exists()).toBe(true)
    expect(w.text()).toContain('Child')
  })

  it('clicking expand again collapses children', async () => {
    const w = mk(TREE_NODES)
    await w.find('button.shrink-0').trigger('click')
    await w.find('button.shrink-0').trigger('click')
    expect(w.find('[data-testid="node-child"]').exists()).toBe(false)
  })
})

describe('HierarchyNodeTree — reorder buttons', () => {
  it('up button is disabled for first node', () => {
    const w = mk(FLAT_NODES)
    const upBtn = w.find('[data-testid="node-a"]').findAll('button').find(b => b.attributes('title') === 'Nach oben')
    expect(upBtn.attributes('disabled')).toBeDefined()
  })

  it('down button is disabled for last node', () => {
    const w = mk(FLAT_NODES)
    const downBtn = w.find('[data-testid="node-b"]').findAll('button').find(b => b.attributes('title') === 'Nach unten')
    expect(downBtn.attributes('disabled')).toBeDefined()
  })

  it('up button emits reorder with direction=up', async () => {
    const w = mk(FLAT_NODES)
    const upBtn = w.find('[data-testid="node-b"]').findAll('button').find(b => b.attributes('title') === 'Nach oben')
    await upBtn.trigger('click')
    const ev = w.emitted('reorder')
    expect(ev).toBeTruthy()
    expect(ev[0][0].direction).toBe('up')
    expect(ev[0][0].node.id).toBe('b')
  })

  it('down button emits reorder with direction=down', async () => {
    const w = mk(FLAT_NODES)
    const downBtn = w.find('[data-testid="node-a"]').findAll('button').find(b => b.attributes('title') === 'Nach unten')
    await downBtn.trigger('click')
    const ev = w.emitted('reorder')
    expect(ev[0][0].direction).toBe('down')
  })
})

describe('HierarchyNodeTree — action buttons', () => {
  it('emits add-child when add-child button is clicked', async () => {
    const w = mk(FLAT_NODES)
    await w.find('[data-testid="btn-add-child-a"]').trigger('click')
    expect(w.emitted('add-child')[0][0].id).toBe('a')
  })

  it('emits edit when edit button is clicked', async () => {
    const w = mk(FLAT_NODES)
    await w.find('[data-testid="btn-edit-node-a"]').trigger('click')
    expect(w.emitted('edit')[0][0].id).toBe('a')
  })

  it('emits delete when delete button is clicked', async () => {
    const w = mk(FLAT_NODES)
    await w.find('[data-testid="btn-delete-node-a"]').trigger('click')
    expect(w.emitted('delete')[0][0].id).toBe('a')
  })
})
