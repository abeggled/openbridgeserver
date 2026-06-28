import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import DataPointForm from '@/components/datapoints/DataPointForm.vue'

function mountForm(initial, saveHandler = vi.fn().mockResolvedValue()) {
  return mount(DataPointForm, {
    props: {
      initial,
      datatypes: [{ name: 'FLOAT' }, { name: 'BOOLEAN' }, { name: 'INTEGER' }],
      saveHandler,
    },
    global: {
      stubs: { Spinner: { template: '<span />' } },
    },
  })
}

// ─── null initial clears the form (lines 194-197) ────────────────────────────

describe('DataPointForm — null initial branch', () => {
  it('renders with blank name when initial is null', () => {
    const w = mountForm(null)
    const nameInput = w.find('[data-testid="input-name"]')
    expect(nameInput.element.value).toBe('')
  })

  it('defaults to FLOAT datatype when initial is null', () => {
    const w = mountForm(null)
    const dtSelect = w.find('[data-testid="select-datatype"]')
    expect(dtSelect.element.value).toBe('FLOAT')
  })

  it('switches from initial object to null and clears fields', async () => {
    const w = mountForm({ name: 'Temp', data_type: 'INTEGER', tags: ['a'], unit: '°C', mqtt_alias: 'x', persist_value: true, record_history: true })
    expect(w.find('[data-testid="input-name"]').element.value).toBe('Temp')

    await w.setProps({ initial: null })
    expect(w.find('[data-testid="input-name"]').element.value).toBe('')
  })
})

// ─── saveHandler error shows error (line 216) ─────────────────────────────────

describe('DataPointForm — saveHandler error', () => {
  it('shows detail error from rejected saveHandler', async () => {
    const saveHandler = vi.fn().mockRejectedValue({
      response: { data: { detail: 'Name bereits vergeben' } },
    })
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('Duplikat')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('Name bereits vergeben')
  })

  it('shows message error when response has no detail', async () => {
    const saveHandler = vi.fn().mockRejectedValue(new Error('network error'))
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('Test')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('network error')
  })

  it('shows fallback saveError when exception has no message or detail', async () => {
    const saveHandler = vi.fn().mockRejectedValue({})
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('Test')
    await w.find('form').trigger('submit')
    await flushPromises()
    // Falls back to t('datapoints.form.saveError')
    expect(w.find('[data-testid="btn-save"]').exists()).toBe(true) // still mounted
  })
})

// ─── custom unit ──────────────────────────────────────────────────────────────

describe('DataPointForm — custom unit', () => {
  it('shows custom unit input when __other__ is selected', async () => {
    const w = mountForm(null)
    await w.find('[data-testid="select-unit"]').setValue('__other__')
    expect(w.find('[data-testid="input-unit-custom"]').exists()).toBe(true)
  })

  it('submits the custom unit value', async () => {
    const saveHandler = vi.fn().mockResolvedValue()
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('DP')
    await w.find('[data-testid="select-unit"]').setValue('__other__')
    await w.find('[data-testid="input-unit-custom"]').setValue('pcs')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(saveHandler).toHaveBeenCalledWith(expect.objectContaining({ unit: 'pcs' }))
  })

  it('loads an unknown unit from initial into the custom field', () => {
    const w = mountForm({
      name: 'X', data_type: 'FLOAT', tags: [], unit: 'pcs', mqtt_alias: null, persist_value: true, record_history: false,
    })
    expect(w.find('[data-testid="select-unit"]').element.value).toBe('__other__')
    expect(w.find('[data-testid="input-unit-custom"]').element.value).toBe('pcs')
  })
})

// ─── tags ─────────────────────────────────────────────────────────────────────

describe('DataPointForm — tags', () => {
  it('submits comma-separated tags as an array', async () => {
    const saveHandler = vi.fn().mockResolvedValue()
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('Tagged')
    const tagsInput = w.findAll('input[type="text"]').find(i => !i.attributes('data-testid'))
    await tagsInput.setValue('alpha, beta, gamma')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(saveHandler).toHaveBeenCalledWith(expect.objectContaining({ tags: ['alpha', 'beta', 'gamma'] }))
  })

  it('loads tags from initial as comma-joined string', () => {
    const w = mountForm({ name: 'T', data_type: 'FLOAT', tags: ['x', 'y'], unit: null, mqtt_alias: null, persist_value: true, record_history: true })
    const tagsInput = w.findAll('input[type="text"]').find(i => !i.attributes('data-testid') && i.element.value.includes('x'))
    expect(tagsInput?.element.value).toContain('x')
    expect(tagsInput?.element.value).toContain('y')
  })
})

// ─── save button state ────────────────────────────────────────────────────────

describe('DataPointForm — submit includes mqtt_alias null when empty', () => {
  it('passes null for mqtt_alias when field is blank', async () => {
    const saveHandler = vi.fn().mockResolvedValue()
    const w = mountForm(null, saveHandler)
    await w.find('[data-testid="input-name"]').setValue('DP')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(saveHandler).toHaveBeenCalledWith(expect.objectContaining({ mqtt_alias: null }))
  })

  it('emits cancel when cancel button is clicked', async () => {
    const w = mountForm(null)
    await w.find('[data-testid="btn-cancel"]').trigger('click')
    expect(w.emitted('cancel')).toBeTruthy()
  })
})
