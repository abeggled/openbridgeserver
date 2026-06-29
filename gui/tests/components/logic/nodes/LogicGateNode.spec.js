import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LogicGateNode from '@/components/logic/nodes/LogicGateNode.vue'

const HANDLE_STUB = { template: '<div class="handle" :data-type="type" />', props: ['type', 'id', 'position'] }
const BASE_NODE_STUB = {
  template: '<div class="base-node" :data-label="label" :data-color="color"><slot /></div>',
  props: ['label', 'color'],
}

function mk(props = {}) {
  return mount(LogicGateNode, {
    props: {
      id:          'gate-1',
      type:        'and',
      data:        { label: '' },
      inputs:      [],
      outputs:     [],
      color:       '#1d4ed8',
      description: '',
      ...props,
    },
    global: { stubs: { Handle: HANDLE_STUB, BaseNode: BASE_NODE_STUB } },
  })
}

describe('LogicGateNode', () => {
  it('passes the type in uppercase as label when data.label is empty', () => {
    const w = mk({ type: 'and', data: { label: '' } })
    expect(w.find('.base-node').attributes('data-label')).toBe('AND')
  })

  it('uses data.label over type when provided', () => {
    const w = mk({ type: 'and', data: { label: 'Custom Label' } })
    expect(w.find('.base-node').attributes('data-label')).toBe('Custom Label')
  })

  it('renders description text', () => {
    const w = mk({ description: 'Logical AND gate' })
    expect(w.text()).toContain('Logical AND gate')
  })

  it('passes color prop to BaseNode', () => {
    const w = mk({ color: '#ef4444' })
    expect(w.find('.base-node').attributes('data-color')).toBe('#ef4444')
  })

  it('renders one Handle stub per input', () => {
    const w = mk({ inputs: [{ id: 'in-0' }, { id: 'in-1' }] })
    const handles = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(handles.length).toBe(2)
  })

  it('renders one Handle stub per output', () => {
    const w = mk({ outputs: [{ id: 'out-0' }] })
    const handles = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(handles.length).toBe(1)
  })
})
