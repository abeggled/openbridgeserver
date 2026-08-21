/**
 * Issue #1157 — every function block on the logic sheet can be renamed inline.
 *
 * The custom name lives in the block's `data.label`, is written through
 * VueFlow's `updateNodeData` (so the generated node id — and with it every
 * edge and reference — stays untouched) and replaces the generated block
 * title on the card. Blocks without a name keep showing their type title.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { useAuthStore } from '@/stores/auth'

const { HANDLE_STUB, NODE_RESIZER_STUB, removeNodesMock, updateNodeDataMock } = vi.hoisted(() => ({
  HANDLE_STUB:        { template: '<div class="handle" />', props: ['type', 'id', 'position', 'style'] },
  NODE_RESIZER_STUB:  { name: 'NodeResizer', props: ['minWidth', 'minHeight', 'isVisible', 'lineClassName', 'handleClassName'], template: '<div />' },
  removeNodesMock:    vi.fn(),
  updateNodeDataMock: vi.fn(),
}))

vi.mock('@vue-flow/core', () => ({
  Handle:     HANDLE_STUB,
  Position:   { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: removeNodesMock, updateNodeData: updateNodeDataMock }),
}))

vi.mock('@vue-flow/node-resizer', () => ({ NodeResizer: NODE_RESIZER_STUB }))

const GLOBAL = { stubs: { Handle: HANDLE_STUB, NodeResizer: NODE_RESIZER_STUB } }

/**
 * One entry per renameable card: how to mount it, and the default title it
 * shows while no custom name is set.
 */
const CARDS = [
  {
    name:         'GenericNode',
    id:           'and-1',
    defaultTitle: 'AND',
    mount: async (data) => {
      const { default: C } = await import('@/components/logic/nodes/GenericNode.vue')
      return mount(C, { props: { id: 'and-1', type: 'and', data }, global: GLOBAL })
    },
  },
  {
    name:         'DatapointNode (read)',
    id:           'dp-1',
    defaultTitle: 'Objekt lesen',
    mount: async (data) => {
      const { default: C } = await import('@/components/logic/nodes/DatapointNode.vue')
      return mount(C, { props: { id: 'dp-1', type: 'datapoint_read', data }, global: GLOBAL })
    },
  },
  {
    name:         'DatapointNode (write)',
    id:           'dp-2',
    defaultTitle: 'Objekt schreiben',
    mount: async (data) => {
      const { default: C } = await import('@/components/logic/nodes/DatapointNode.vue')
      return mount(C, { props: { id: 'dp-2', type: 'datapoint_write', data }, global: GLOBAL })
    },
  },
  {
    // Comment blocks share the problem the issue describes — a sheet full of
    // identical "KOMMENTAR" headers — and the config panel offers the rename
    // field for every block type, so this card must honour it too.
    name:         'CommentNode',
    id:           'comment-1',
    defaultTitle: 'Kommentar',
    mount: async (data) => {
      const { default: C } = await import('@/components/logic/nodes/CommentNode.vue')
      return mount(C, { props: { id: 'comment-1', type: 'comment', data }, global: GLOBAL })
    },
  },
  {
    name:         'PythonScriptNode',
    id:           'py-1',
    defaultTitle: 'Python Script',
    mount: async (data) => {
      const { default: C } = await import('@/components/logic/nodes/PythonScriptNode.vue')
      return mount(C, { props: { id: 'py-1', type: 'python_script', data }, global: GLOBAL })
    },
  },
]

// The global test setup installs a fresh Pinia per test and hands the same
// instance to `mount()`, so the store is set up through that active instance.
function setAdmin(isAdmin) {
  useAuthStore().user = { id: 'u1', username: isAdmin ? 'admin' : 'viewer', is_admin: isAdmin }
}

describe('logic node cards — inline rename (#1157)', () => {
  beforeEach(() => {
    removeNodesMock.mockClear()
    updateNodeDataMock.mockClear()
    setAdmin(true)
  })

  for (const card of CARDS) {
    it(`${card.name} shows the block type title without a custom name`, async () => {
      const w = await card.mount({})
      await flushPromises()
      expect(w.get('[data-testid="node-title"]').text()).toBe(card.defaultTitle)
    })

    it(`${card.name} shows the custom name instead of the block type title`, async () => {
      const w = await card.mount({ label: 'Treppenhaus' })
      await flushPromises()
      expect(w.get('[data-testid="node-title"]').text()).toBe('Treppenhaus')
    })

    it(`${card.name} still offers the block type title as the placeholder once renamed`, async () => {
      const w = await card.mount({ label: 'Treppenhaus' })
      await flushPromises()
      await w.get('[data-testid="node-title"]').trigger('dblclick')
      expect(w.get('[data-testid="node-title-input"]').attributes('placeholder')).toBe(card.defaultTitle)
    })

    it(`${card.name} marks a custom name so it can drop the uppercase treatment`, async () => {
      const w = await card.mount({ label: 'Treppenhaus' })
      await flushPromises()
      const classes = w.get('[data-testid="node-title"]').classes()
      expect(classes.some(c => c.endsWith('--custom'))).toBe(true)
    })

    it(`${card.name} writes the new name to data.label, leaving the node id alone`, async () => {
      const w = await card.mount({})
      await flushPromises()
      await w.get('[data-testid="node-title"]').trigger('dblclick')
      await w.get('[data-testid="node-title-input"]').setValue('Treppenhaus')
      await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
      expect(updateNodeDataMock).toHaveBeenCalledWith(card.id, { label: 'Treppenhaus' })
    })

    it(`${card.name} clears the custom name back to the default title`, async () => {
      const w = await card.mount({ label: 'Treppenhaus' })
      await flushPromises()
      await w.get('[data-testid="node-title"]').trigger('dblclick')
      await w.get('[data-testid="node-title-input"]').setValue('')
      await w.get('[data-testid="node-title-input"]').trigger('keydown.enter')
      expect(updateNodeDataMock).toHaveBeenCalledWith(card.id, { label: '' })
    })

    it(`${card.name} offers no inline field to a read-only viewer`, async () => {
      setAdmin(false)
      const w = await card.mount({})
      await flushPromises()
      await w.get('[data-testid="node-title"]').trigger('dblclick')
      expect(w.find('[data-testid="node-title-input"]').exists()).toBe(false)
      expect(updateNodeDataMock).not.toHaveBeenCalled()
    })

    it(`${card.name} still shows the block name to a read-only viewer`, async () => {
      setAdmin(false)
      const w = await card.mount({ label: 'Treppenhaus' })
      await flushPromises()
      expect(w.get('[data-testid="node-title"]').text()).toBe('Treppenhaus')
    })

    it(`${card.name} does not persist a cancelled rename`, async () => {
      const w = await card.mount({})
      await flushPromises()
      await w.get('[data-testid="node-title"]').trigger('dblclick')
      await w.get('[data-testid="node-title-input"]').setValue('Verworfen')
      await w.get('[data-testid="node-title-input"]').trigger('keydown.esc')
      expect(updateNodeDataMock).not.toHaveBeenCalled()
    })
  }

  it('GenericNode renders the type title uppercase while no name is set', async () => {
    const w = await CARDS[0].mount({})
    await flushPromises()
    expect(w.get('[data-testid="node-title"]').classes()).not.toContain('gn-title--custom')
  })
})

/**
 * The `--custom` class only helps if the card actually defines the rule that
 * drops the uppercase treatment — and jsdom never applies SFC scoped styles,
 * so no mounted assertion can see it. Scan the sources instead, the same way
 * `nodeCardSurface.spec.js` guards the card background.
 */
describe('custom block names keep their own casing (#1157)', () => {
  const CARD_SOURCES = Object.fromEntries(
    Object.entries(
      import.meta.glob('../../../../src/components/logic/nodes/*.vue', {
        query: '?raw',
        import: 'default',
        eager: true,
      }),
    ).map(([path, source]) => [path.split('/').pop(), source]),
  )

  for (const [file, selector] of [
    ['GenericNode.vue', '.gn-title--custom'],
    ['DatapointNode.vue', '.gn-label--custom'],
    ['PythonScriptNode.vue', '.gn-label--custom'],
    ['CommentNode.vue', '.cn-title--custom'],
  ]) {
    it(`${file} turns off text-transform for ${selector}`, () => {
      const source = CARD_SOURCES[file]
      expect(source, `no source found for ${file}`).toBeTruthy()
      const rule = source.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`))
      expect(rule, `${file} declares no ${selector} rule`).toBeTruthy()
      expect(rule[1]).toMatch(/text-transform\s*:\s*none/)
    })
  }
})

describe('MissingNode shows a carried-over block name (#1157)', () => {
  async function mountMissing(data) {
    const { default: C } = await import('@/components/logic/nodes/MissingNode.vue')
    const w = mount(C, { props: { data }, global: GLOBAL })
    await flushPromises()
    return w
  }

  it('shows the custom name next to the missing type', async () => {
    const w = await mountMissing({ original_type: 'gone_v9', label: 'Treppenhaus' })
    expect(w.text()).toContain('Treppenhaus')
    expect(w.text()).toContain('gone_v9')
  })

  it('shows only the missing type when the block was never renamed', async () => {
    const w = await mountMissing({ original_type: 'gone_v9' })
    expect(w.find('.missing-node__name').exists()).toBe(false)
    expect(w.text()).toContain('gone_v9')
  })

  it('shows no custom name for a placeholder that was never renamed', async () => {
    // Older placeholders reach the editor canonicalized by the API
    // (`_normalize_missing_node_placeholders`): the missing type always lives
    // in `original_type`, so `label` here means one thing only — a user name.
    const w = await mountMissing({ original_type: 'gone_v9' })
    expect(w.find('.missing-node__name').exists()).toBe(false)
    expect(w.get('.missing-node__type').text()).toBe('gone_v9')
  })
})
