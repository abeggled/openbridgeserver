import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let searchMock = vi.fn().mockResolvedValue({ data: { items: [] } })
let _dpStubEmit = null

beforeEach(() => {
  vi.resetModules()
  searchMock = vi.fn().mockResolvedValue({ data: { items: [] } })
  _dpStubEmit = null
  vi.doMock('@/api/client', () => ({
    searchApi: { search: searchMock },
  }))
  vi.doMock('@/components/ui/DpCombobox.vue', () => ({
    default: {
      name: 'DpCombobox',
      template: '<div class="dp-combobox" />',
      props: ['modelValue', 'displayName', 'placeholder'],
      emits: ['select', 'update:modelValue'],
      setup(_, { emit }) { _dpStubEmit = (item) => emit('select', item) },
    },
  }))
})

async function mountForm(modelValue = {}) {
  const { default: AnwesenheitConfigForm } = await import('@/components/adapters/AnwesenheitConfigForm.vue')
  return mount(AnwesenheitConfigForm, {
    props: {
      modelValue: {
        offset_days: 7,
        control_dp_id: null,
        control_invert: false,
        on_presence: 'behalten',
        on_presence_value: '',
        ...modelValue,
      },
    },
  })
}

describe('AnwesenheitConfigForm — offset select', () => {
  it('renders offset select with 4 options', async () => {
    const w = await mountForm()
    await flushPromises()
    const select = w.find('select')
    expect(select.findAll('option').length).toBe(4)
  })

  it('selects "7" when offset_days is 7', async () => {
    const w = await mountForm({ offset_days: 7 })
    await flushPromises()
    expect(w.find('select').element.value).toBe('7')
  })

  it('selects "custom" and shows number input when offset_days is non-preset', async () => {
    const w = await mountForm({ offset_days: 5 })
    await flushPromises()
    expect(w.find('select').element.value).toBe('custom')
    expect(w.find('input[type="number"]').exists()).toBe(true)
  })

  it('emits update:modelValue with offset_days on select change', async () => {
    const w = await mountForm({ offset_days: 7 })
    await flushPromises()
    await w.find('select').setValue('14')
    await w.find('select').trigger('change')
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[emitted.length - 1][0].offset_days).toBe(14)
  })
})

describe('AnwesenheitConfigForm — DpCombobox (control_dp_id)', () => {
  it('renders DpCombobox', async () => {
    const w = await mountForm()
    await flushPromises()
    expect(w.find('.dp-combobox').exists()).toBe(true)
  })

  it('emits update:modelValue with control_dp_id on select', async () => {
    const w = await mountForm()
    await flushPromises()
    _dpStubEmit({ id: 'dp-42', name: 'Sensor' })
    await flushPromises()
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[emitted.length - 1][0].control_dp_id).toBe('dp-42')
  })
})

describe('AnwesenheitConfigForm — control_invert checkbox', () => {
  it('renders invert checkbox', async () => {
    const w = await mountForm()
    await flushPromises()
    expect(w.find('input[type="checkbox"]').exists()).toBe(true)
  })

  it('emits update:modelValue with control_invert on checkbox change', async () => {
    const w = await mountForm({ control_invert: false })
    await flushPromises()
    const cb = w.find('input[type="checkbox"]')
    await cb.setChecked(true)
    const emitted = w.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[emitted.length - 1][0].control_invert).toBe(true)
  })
})

describe('AnwesenheitConfigForm — on_presence select', () => {
  it('renders on_presence select with 3 options', async () => {
    const w = await mountForm()
    await flushPromises()
    const selects = w.findAll('select')
    const onPresenceSelect = selects[1]
    expect(onPresenceSelect.findAll('option').length).toBe(3)
  })

  it('hides on_presence_value input when on_presence=behalten', async () => {
    const wKeep = await mountForm({ on_presence: 'behalten' })
    await flushPromises()
    // No text inputs except checkbox (invert) — the on_presence_value v-if is false
    const textInputs = wKeep.findAll('input').filter(i => !i.attributes('type') || i.attributes('type') === 'text')
    expect(textInputs.length).toBe(0)
  })

  it('shows on_presence_value input when on_presence=setzen', async () => {
    const w = await mountForm({ on_presence: 'setzen' })
    await flushPromises()
    const textInput = w.findAll('input').find(i => !i.attributes('type') || i.attributes('type') === 'text')
    expect(textInput).toBeTruthy()
  })
})
