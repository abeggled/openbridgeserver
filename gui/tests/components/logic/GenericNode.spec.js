import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import GenericNode from '@/components/logic/nodes/GenericNode.vue'

vi.mock('@vue-flow/core', () => ({
  Handle: {
    props: ['id', 'type', 'position'],
    template: '<span class="handle" :data-id="id" :data-type="type" :data-position="position" />',
  },
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useVueFlow: () => ({
    updateNodeData: vi.fn(),
    removeNodes: vi.fn(),
  }),
}))

function mountNode(data) {
  return mount(GenericNode, {
    props: {
      id: 'node-1',
      type: 'api_client',
      data,
    },
  })
}

describe('GenericNode memory rendering', () => {
  it('renders memory input, reset, and output ports', () => {
    const wrapper = mount(GenericNode, {
      props: {
        id: 'mem',
        type: 'memory',
        data: { initial_value: 'false', data_type: 'bool' },
      },
    })

    expect(wrapper.text()).toContain('Speicher')
    expect(wrapper.text()).toContain('Eingang')
    expect(wrapper.text()).toContain('Reset')
    expect(wrapper.text()).toContain('Ausgang')
    expect(wrapper.find('[data-id="reset"][data-type="target"]').exists()).toBe(true)
    expect(wrapper.find('[data-id="out"][data-type="source"]').exists()).toBe(true)
  })
})

describe('GenericNode debug band', () => {
  it('uses the full debug title when present', () => {
    const wrapper = mountNode({ _dbg: 'short response', _dbg_title: 'full response body' })

    expect(wrapper.find('[data-testid="debug-band"]').attributes('title')).toBe('full response body')
  })

  it('falls back to the visible debug value for the title', () => {
    const wrapper = mountNode({ _dbg: 'visible debug' })

    expect(wrapper.find('[data-testid="debug-band"]').attributes('title')).toBe('visible debug')
  })
})
