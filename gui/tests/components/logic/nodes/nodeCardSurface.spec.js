/**
 * Issue #1074 — every logic-editor function block renders its category tint on
 * top of the opaque `--node-card-bg` theme surface, so the configurable canvas
 * raster (#1072) cannot show through the block body.
 *
 * The opaque surface itself lives in the global `.logic-node-surface` rule in
 * `style.css`, which jsdom does not load. What the components are responsible
 * for — and what is asserted here — is that every card
 *   1. opts into that shared surface class, and
 *   2. passes its category colour as the translucent `--node-tint` overlay
 *      instead of using the tint alone as its `background`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nodeTint } from '@/utils/logicNodeSurface'

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

// Raw SFC sources, keyed by file name — used by the scoped-style guardrail below.
const NODE_SOURCES = Object.fromEntries(
  Object.entries(
    import.meta.glob('../../../../src/components/logic/nodes/*.vue', {
      query: '?raw',
      import: 'default',
      eager: true,
    }),
  ).map(([path, source]) => [path.split('/').pop(), source]),
)

/**
 * Every entry mounts one node component and points at the card element that
 * carries the surface, plus the category colour it must tint that card with.
 */
const CARDS = [
  {
    name:     'GenericNode (logic category)',
    file:     'GenericNode.vue',
    card:     '.gn-card',
    color:    '#1d4ed8',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/GenericNode.vue')
      return mount(C, { props: { id: 'n1', type: 'or', data: {} }, global: GLOBAL })
    },
  },
  {
    name:     'GenericNode (math category)',
    file:     'GenericNode.vue',
    card:     '.gn-card',
    color:    '#7c3aed',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/GenericNode.vue')
      return mount(C, { props: { id: 'n2', type: 'statistics', data: {} }, global: GLOBAL })
    },
  },
  {
    name:     'DatapointNode',
    file:     'DatapointNode.vue',
    card:     '.gn-card',
    color:    '#0f766e',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/DatapointNode.vue')
      return mount(C, { props: { id: 'n3', type: 'datapoint_read', data: {} }, global: GLOBAL })
    },
  },
  {
    name:     'PythonScriptNode',
    file:     'PythonScriptNode.vue',
    card:     '.gn-card',
    color:    '#be185d',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/PythonScriptNode.vue')
      return mount(C, { props: { id: 'n4', type: 'python_script', data: {} }, global: GLOBAL })
    },
  },
  {
    name:     'CommentNode',
    file:     'CommentNode.vue',
    card:     '.cn-card',
    color:    '#ca8a04',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/CommentNode.vue')
      return mount(C, { props: { id: 'n5', type: 'comment', data: {} }, global: GLOBAL })
    },
  },
  {
    name:     'MissingNode',
    file:     'MissingNode.vue',
    card:     '.missing-node',
    color:    '#ef4444',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/MissingNode.vue')
      return mount(C, { props: { data: { original_type: 'gone' } }, global: GLOBAL })
    },
  },
  {
    name:     'BaseNode',
    file:     'BaseNode.vue',
    card:     '.logic-node',
    color:    '#1d4ed8',
    mount:    async () => {
      const { default: C } = await import('@/components/logic/nodes/BaseNode.vue')
      return mount(C, { props: { label: 'AND', color: '#1d4ed8' }, global: GLOBAL })
    },
  },
]

describe('logic node cards — opaque surface with category tint (#1074)', () => {
  beforeEach(() => { removeNodesMock.mockClear(); updateNodeDataMock.mockClear() })

  for (const spec of CARDS) {
    it(`${spec.name} opts into the shared opaque surface`, async () => {
      const w = await spec.mount()
      await flushPromises()
      expect(w.find(spec.card).classes()).toContain('logic-node-surface')
    })

    it(`${spec.name} passes its category colour as a translucent tint`, async () => {
      const w = await spec.mount()
      await flushPromises()
      const style = w.find(spec.card).attributes('style')
      expect(style).toContain(`--node-tint: ${nodeTint(spec.color)}`)
    })

    it(`${spec.name} does not paint the tint as its own background`, async () => {
      const w = await spec.mount()
      await flushPromises()
      const style = w.find(spec.card).attributes('style') ?? ''
      expect(style).not.toMatch(/background/)
    })
  }
})

/**
 * A component's own scoped rule for the card (`.gn-card[data-v-…]`) outranks the
 * global `.logic-node-surface` class, so re-declaring a `background` there would
 * silently mask the opaque surface and let the raster through again — exactly
 * the regression #1074 fixed. jsdom/happy-dom never applies SFC styles, so no
 * mounted assertion can catch it; this scans the source instead.
 */
const STYLE_BLOCK_RE = /<style[^>]*>([\s\S]*?)<\/style>/g
const CSS_RULE_RE = /([^{}]+)\{([^{}]*)\}/g

/**
 * All declaration blocks in `source` whose selector list contains `selector`
 * verbatim. Flat rules only — none of the node components nest their card rule
 * inside an at-rule, and a rule wrapped in one would be skipped rather than
 * misreported.
 */
function declarationsFor(source, selector) {
  const blocks = []
  for (const [, css] of source.matchAll(STYLE_BLOCK_RE)) {
    for (const [, selectors, declarations] of css.matchAll(CSS_RULE_RE)) {
      const hit = selectors
        .split(',')
        .map(part => part.replace(/\/\*[\s\S]*?\*\//g, '').trim())
        .some(part => part === selector)
      if (hit) blocks.push(declarations)
    }
  }
  return blocks
}

describe('logic node card styles do not mask the shared surface (#1074)', () => {
  // Deduplicated: GenericNode appears twice in CARDS (two categories).
  const sources = [...new Map(CARDS.map(spec => [spec.file, spec])).values()]

  for (const spec of sources) {
    it(`${spec.file} keeps its scoped ${spec.card} rule free of a background`, () => {
      const source = NODE_SOURCES[spec.file]
      expect(source, `no source found for ${spec.file}`).toBeTruthy()
      const declarations = declarationsFor(source, spec.card)

      for (const block of declarations) {
        // Comments explain why the property is absent — strip before matching.
        expect(block.replace(/\/\*[\s\S]*?\*\//g, '')).not.toMatch(/(^|[\s;])background(-color|-image)?\s*:/)
      }
    })
  }

  it('recognises a background that would mask the surface', () => {
    const masked = '<style scoped>\n.gn-card {\n  background: #1d4ed812;\n}\n</style>'
    expect(declarationsFor(masked, '.gn-card')[0]).toMatch(/background\s*:/)
  })

  it('ignores rules for other elements inside the same component', () => {
    const source = NODE_SOURCES['MissingNode.vue']
    // `.missing-node__badge` legitimately has a background and must not be
    // mistaken for the card rule by a prefix match.
    expect(source).toMatch(/\.missing-node__badge\s*\{[^}]*background/)
    expect(declarationsFor(source, '.missing-node').length).toBe(1)
  })
})

describe('GenericNode tint follows the node type (#1074)', () => {
  async function mountGeneric(type) {
    const { default: C } = await import('@/components/logic/nodes/GenericNode.vue')
    const w = mount(C, { props: { id: 'gn', type, data: {} }, global: GLOBAL })
    await flushPromises()
    return w
  }

  it('uses the notification colour for notify_message', async () => {
    const w = await mountGeneric('notify_message')
    expect(w.find('.gn-card').attributes('style')).toContain('--node-tint: #e11d4812')
  })

  it('falls back to the neutral colour for an unknown type', async () => {
    const w = await mountGeneric('mystery_node')
    expect(w.find('.gn-card').attributes('style')).toContain('--node-tint: #47556912')
  })
})
