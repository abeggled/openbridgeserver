import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MissingNode from '@/components/logic/nodes/MissingNode.vue'

const HANDLE_STUB = { template: '<div class="handle" />', props: ['type', 'id', 'position'] }

function mk(data = {}) {
  return mount(MissingNode, {
    props: { data: { original_type: null, label: '', ...data } },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
}

describe('MissingNode', () => {
  it('shows data.original_type as node type label', () => {
    const w = mk({ original_type: 'mqtt_trigger', label: 'Fallback' })
    expect(w.text()).toContain('mqtt_trigger')
  })

  it('falls back to data.label when original_type is null', () => {
    const w = mk({ original_type: null, label: 'Unknown Node' })
    expect(w.text()).toContain('Unknown Node')
  })

  it('renders two Handle stubs (input + output)', () => {
    expect(mk().findAll('.handle').length).toBe(2)
  })

  it('has missing-node root class', () => {
    expect(mk().find('.missing-node').exists()).toBe(true)
  })
})
