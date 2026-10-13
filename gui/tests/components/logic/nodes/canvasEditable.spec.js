// The delete button on every block card follows the editor's read-only state
// (issue #1333): while a sheet loads, a deletion would land on the outgoing
// canvas.
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { CANVAS_EDITABLE_KEY } from '@/components/logic/nodes/canvasEditable'

vi.mock('@vue-flow/core', () => ({
  Handle: { template: '<span />' },
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useVueFlow: () => ({ removeNodes: vi.fn(), updateNodeData: vi.fn() }),
}))
vi.mock('@vue-flow/node-resizer', () => ({ NodeResizer: { template: '<div />' } }))

const CARDS = [
  ['GenericNode', { type: 'and' }, '.gn-del'],
  ['CommentNode', { type: 'comment' }, '.cn-del'],
  ['DatapointNode', { type: 'datapoint_read' }, '.gn-delete'],
  ['PythonScriptNode', { type: 'python_script' }, '.gn-delete'],
]

describe.each(CARDS)('%s delete button', (name, props, selector) => {
  async function mountCard(editable) {
    const { default: Card } = await import(`@/components/logic/nodes/${name}.vue`)
    const wrapper = mount(Card, {
      props: { id: 'n-1', data: {}, ...props },
      global: { provide: { [CANVAS_EDITABLE_KEY]: ref(editable) } },
    })
    await flushPromises()
    return wrapper
  }

  it('is offered while the canvas is editable', async () => {
    expect((await mountCard(true)).find(selector).exists()).toBe(true)
  })

  it('is hidden while the canvas is read-only', async () => {
    expect((await mountCard(false)).find(selector).exists()).toBe(false)
  })
})

describe('GenericNode delete button visibility', () => {
  it('shows only while the card is hovered', async () => {
    const { default: GenericNode } = await import('@/components/logic/nodes/GenericNode.vue')
    const wrapper = mount(GenericNode, { props: { id: 'n-1', type: 'and', data: {} } })
    await flushPromises()
    expect(wrapper.find('.gn-del').attributes('style')).toContain('visibility: hidden')

    await wrapper.trigger('mouseenter')
    expect(wrapper.find('.gn-del').attributes('style')).toContain('visibility: visible')
  })
})
