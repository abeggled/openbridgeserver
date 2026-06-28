import { mount } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import GenericNode from '@/components/logic/nodes/GenericNode.vue'

vi.mock('@vue-flow/core', () => ({
  Handle: { template: '<span class="handle" />' },
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useVueFlow: () => ({ updateNodeData: vi.fn() }),
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
