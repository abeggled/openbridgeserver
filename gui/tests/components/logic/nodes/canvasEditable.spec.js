// Edits on the block cards follow the editor's read-only state (issue #1333):
// while a sheet loads or is being swapped, an edit would land on the outgoing
// canvas. Delete, inline rename, negation toggle and comment resize pause.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { CANVAS_EDITABLE_KEY } from '@/components/logic/nodes/canvasEditable'

const { updateNodeDataMock, NODE_RESIZER_STUB } = vi.hoisted(() => ({
  updateNodeDataMock: vi.fn(),
  NODE_RESIZER_STUB: { name: 'NodeResizer', props: ['minWidth', 'minHeight', 'isVisible', 'lineClassName', 'handleClassName'], template: '<div />' },
}))

vi.mock('@vue-flow/core', () => ({
  Handle: { template: '<span />' },
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useVueFlow: () => ({ removeNodes: vi.fn(), updateNodeData: updateNodeDataMock }),
}))
vi.mock('@vue-flow/node-resizer', () => ({ NodeResizer: NODE_RESIZER_STUB }))

beforeEach(() => {
  updateNodeDataMock.mockClear()
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
})

async function mountCard(name, props, editable) {
  const { default: Card } = await import(`@/components/logic/nodes/${name}.vue`)
  const wrapper = mount(Card, {
    props: { id: 'n-1', data: {}, ...props },
    global: {
      provide: editable === undefined ? {} : { [CANVAS_EDITABLE_KEY]: ref(editable) },
      stubs: { NodeResizer: NODE_RESIZER_STUB },
    },
  })
  await flushPromises()
  return wrapper
}

const CARDS = [
  ['GenericNode', { type: 'and' }, '.gn-del'],
  ['CommentNode', { type: 'comment' }, '.cn-del'],
  ['DatapointNode', { type: 'datapoint_read' }, '.gn-delete'],
  ['PythonScriptNode', { type: 'python_script' }, '.gn-delete'],
]

describe.each(CARDS)('%s', (name, props, deleteSelector) => {
  it('offers delete and inline rename while the canvas is editable', async () => {
    const wrapper = await mountCard(name, props, true)
    expect(wrapper.find(deleteSelector).exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'NodeTitleEditor' }).props('editable')).toBe(true)
  })

  it('offers neither while the canvas is read-only', async () => {
    const wrapper = await mountCard(name, props, false)
    expect(wrapper.find(deleteSelector).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'NodeTitleEditor' }).props('editable')).toBe(false)
  })

  it('counts as editable outside the editor', async () => {
    const wrapper = await mountCard(name, props)
    expect(wrapper.find(deleteSelector).exists()).toBe(true)
  })
})

describe('GenericNode negation toggle', () => {
  it('toggles while editable and ignores clicks while read-only', async () => {
    const readOnly = await mountCard('GenericNode', { type: 'and' }, false)
    await readOnly.find('.gn-port-negate').trigger('click')
    expect(updateNodeDataMock).not.toHaveBeenCalled()

    const editable = await mountCard('GenericNode', { type: 'and' }, true)
    await editable.find('.gn-port-negate').trigger('click')
    expect(updateNodeDataMock).toHaveBeenCalledTimes(1)
  })

  it('shows the delete button only while the card is hovered', async () => {
    const wrapper = await mountCard('GenericNode', { type: 'and' }, true)
    expect(wrapper.find('.gn-del').attributes('style')).toContain('visibility: hidden')
    await wrapper.trigger('mouseenter')
    expect(wrapper.find('.gn-del').attributes('style')).toContain('visibility: visible')
  })
})

describe('CommentNode resizer', () => {
  it('stays hidden on hover while the canvas is read-only', async () => {
    const readOnly = await mountCard('CommentNode', { type: 'comment' }, false)
    await readOnly.trigger('mouseenter')
    expect(readOnly.findComponent({ name: 'NodeResizer' }).props('isVisible')).toBe(false)

    const editable = await mountCard('CommentNode', { type: 'comment' }, true)
    await editable.trigger('mouseenter')
    expect(editable.findComponent({ name: 'NodeResizer' }).props('isVisible')).toBe(true)
  })
})
