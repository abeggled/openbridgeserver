import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import DataPointForm from '@/components/datapoints/DataPointForm.vue'

function mountForm(initial, saveHandler = vi.fn().mockResolvedValue()) {
  return mount(DataPointForm, {
    props: {
      initial,
      datatypes: [{ name: 'FLOAT' }, { name: 'BOOLEAN' }],
      saveHandler,
    },
    global: {
      stubs: {
        Spinner: { template: '<span />' },
      },
    },
  })
}

describe('DataPointForm', () => {
  it('submits unit null when an existing unit is reset to none', async () => {
    const saveHandler = vi.fn().mockResolvedValue()
    const wrapper = mountForm(
      {
        id: 'dp-unit-reset',
        name: 'Unit Reset',
        data_type: 'FLOAT',
        unit: '°C',
        tags: [],
        mqtt_alias: null,
        persist_value: true,
        record_history: true,
      },
      saveHandler
    )

    await wrapper.find('[data-testid="select-unit"]').setValue('')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(saveHandler).toHaveBeenCalledWith(expect.objectContaining({ unit: null }))
  })
})
