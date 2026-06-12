import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import SchemaForm from '@/components/adapters/SchemaForm.vue'

describe('SchemaForm', () => {
  it('renders descriptions for boolean fields', () => {
    const wrapper = mount(SchemaForm, {
      props: {
        schema: {
          type: 'object',
          properties: {
            serialize_reads: {
              type: 'boolean',
              title: 'Serialize reads',
              description: 'Use this when a Modbus TCP device cannot handle parallel reads.',
              default: true,
            },
          },
        },
        modelValue: {},
      },
    })

    expect(wrapper.get('[data-testid="config-field-serialize_reads"]').element.checked).toBe(true)
    expect(wrapper.text()).toContain('Serialize reads')
    expect(wrapper.text()).toContain('Use this when a Modbus TCP device cannot handle parallel reads.')
  })
})
