import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { removeNodesMock, updateNodeDataMock } = vi.hoisted(() => ({
  removeNodesMock:    vi.fn(),
  updateNodeDataMock: vi.fn(),
}))

const NODE_RESIZER_STUB = {
  name: 'NodeResizer',
  props: ['minWidth', 'minHeight', 'isVisible', 'lineClassName', 'handleClassName'],
  emits: ['resize', 'resizeStart', 'resizeEnd'],
  template: '<div class="node-resizer-stub" />',
}

vi.mock('@vue-flow/core', () => ({
  useVueFlow: () => ({ removeNodes: removeNodesMock, updateNodeData: updateNodeDataMock }),
}))

vi.mock('@vue-flow/node-resizer', () => ({
  NodeResizer: NODE_RESIZER_STUB,
}))

async function mountCN(data = {}, extraProps = {}) {
  const { default: CommentNode } = await import('@/components/logic/nodes/CommentNode.vue')
  return mount(CommentNode, {
    props: { id: 'cn-1', type: 'comment', data, ...extraProps },
  })
}

describe('CommentNode', () => {
  beforeEach(() => { removeNodesMock.mockClear(); updateNodeDataMock.mockClear() })

  it('shows the localized node label', async () => {
    const w = await mountCN()
    await flushPromises()
    expect(w.find('.cn-title').text()).toBe('Kommentar')
  })

  it('falls back to the raw type string when no translation exists', async () => {
    const w = await mountCN({}, { type: 'mystery_node' })
    await flushPromises()
    expect(w.find('.cn-title').text()).toBe('mystery_node')
  })

  it('hides the delete button until hovered', async () => {
    const w = await mountCN()
    await flushPromises()
    expect(w.find('.cn-del').attributes('style')).toContain('visibility: hidden')
    await w.find('.cn-root').trigger('mouseenter')
    expect(w.find('.cn-del').attributes('style')).toContain('visibility: visible')
    await w.find('.cn-root').trigger('mouseleave')
    expect(w.find('.cn-del').attributes('style')).toContain('visibility: hidden')
  })

  it('renders the note text when present', async () => {
    const w = await mountCN({ text: 'Hysterese-Gating: siehe #1043' })
    await flushPromises()
    expect(w.find('.cn-text').text()).toBe('Hysterese-Gating: siehe #1043')
    expect(w.find('.cn-placeholder').exists()).toBe(false)
  })

  it('shows a placeholder when text is empty', async () => {
    const w = await mountCN({ text: '' })
    await flushPromises()
    expect(w.find('.cn-placeholder').exists()).toBe(true)
    expect(w.find('.cn-text').exists()).toBe(false)
  })

  it('defaults to 220x140 when width/height are not set', async () => {
    const w = await mountCN()
    await flushPromises()
    const style = w.find('.cn-card').attributes('style')
    expect(style).toContain('width: 220px')
    expect(style).toContain('height: 140px')
  })

  it('sizes the card from data.width/data.height', async () => {
    const w = await mountCN({ width: 300, height: 180 })
    await flushPromises()
    const style = w.find('.cn-card').attributes('style')
    expect(style).toContain('width: 300px')
    expect(style).toContain('height: 180px')
  })

  it('renders no port handles', async () => {
    const w = await mountCN()
    await flushPromises()
    expect(w.findAll('.vue-flow__handle').length).toBe(0)
  })

  it('persists resize deltas via updateNodeData, rounded', async () => {
    const w = await mountCN()
    await flushPromises()
    await w.findComponent(NODE_RESIZER_STUB).vm.$emit('resize', { params: { width: 301.6, height: 199.2 } })
    expect(updateNodeDataMock).toHaveBeenCalledWith('cn-1', { width: 302, height: 199 })
  })

  it('removes the node when the delete button is clicked', async () => {
    const w = await mountCN()
    await flushPromises()
    await w.find('.cn-del').trigger('click')
    expect(removeNodesMock).toHaveBeenCalledWith(['cn-1'])
  })
})
