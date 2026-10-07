// #1260: picking a group address in the KNX binding form takes over the catalog's DPT,
// but a catalog DPT that is only a main type ("DPT5") must not replace a subtype the
// binding already carries ("DPT5.001") – the values would change by a factor of 2.55.
// Same rule as the .knxproj re-import (keepsStoredSubtype, parity table with Python).
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const DPTS = ['DPT1', 'DPT1.001', 'DPT5', 'DPT5.001', 'DPT5.004', 'DPT7', 'DPT9', 'DPT9.001', 'DPT10.001']
  .map(dpt_id => ({ dpt_id, name: dpt_id, data_type: 'INTEGER', unit: '' }))

let createBinding
let updateBinding
let catalog

beforeEach(() => {
  vi.resetModules()
  createBinding = vi.fn().mockResolvedValue({})
  updateBinding = vi.fn().mockResolvedValue({})
  catalog = []
  vi.doMock('@/api/client', () => ({
    dpApi: { createBinding, updateBinding },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: [{ id: 'knx-1', name: 'KNX Test', adapter_type: 'KNX' }] }),
      knxDpts: vi.fn().mockResolvedValue({ data: DPTS }),
    },
    knxprojApi: {
      listGA: vi.fn().mockImplementation(async () => ({ data: { total: catalog.length, items: catalog, group_address_style: 'ThreeLevel' } })),
    },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: { archives: [] } }) },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountForm(initialDpt) {
  const mod = await import('@/components/datapoints/BindingForm.vue')
  const initial = initialDpt === undefined ? null : {
    id: 'binding-1',
    adapter_instance_id: 'knx-1',
    adapter_type: 'KNX',
    direction: 'SOURCE',
    enabled: true,
    config: { group_address: '1/1/1', dpt_id: initialDpt },
  }
  const wrapper = mount(mod.default, {
    props: { dpId: 'dp-1', dpPersistValue: false, dpDataType: 'INTEGER', initial },
    attachTo: document.body,
  })
  await flushPromises()
  if (!initial) {
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('knx-1')
    await flushPromises()
  }
  return wrapper
}

// The user's click path: type into the group address field, wait for the suggestions, click one.
async function pickGroupAddress(wrapper, address, dpt) {
  catalog = [{ address, name: 'Some GA', description: '', dpt }]
  await wrapper.find('input[autocomplete="off"]').setValue(address)
  await new Promise(resolve => setTimeout(resolve, 300))
  await flushPromises()
  await wrapper.find('li').trigger('click')
  await flushPromises()
}

async function savedDpt(wrapper) {
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  const call = updateBinding.mock.calls[0] ?? createBinding.mock.calls[0]
  const body = call[call.length - 1]
  return body.config.dpt_id
}

describe('BindingForm (KNX): DPT taken over from the group address catalog (#1260)', () => {
  it('keeps the stored subtype when the catalog names only its main type', async () => {
    const w = await mountForm('DPT5.001')
    await pickGroupAddress(w, '3/1/1', 'DPT5')
    expect(await savedDpt(w)).toBe('DPT5.001')
    w.unmount()
  })

  it('keeps a subtype the user picked in the form before choosing the address', async () => {
    const w = await mountForm()
    await w.findAll('select').find(s => s.find('option[value="DPT5.004"]').exists()).setValue('DPT5.004')
    await pickGroupAddress(w, '3/1/1', 'DPT5')
    expect(await savedDpt(w)).toBe('DPT5.004')
    w.unmount()
  })

  it('takes the catalog main type when the stored DPT has another main type', async () => {
    const w = await mountForm('DPT10.001')
    await pickGroupAddress(w, '3/1/1', 'DPT1')
    expect(await savedDpt(w)).toBe('DPT1')
    w.unmount()
  })

  it('takes a catalog subtype over the stored one', async () => {
    const w = await mountForm('DPT5.001')
    await pickGroupAddress(w, '3/1/1', 'DPT5.004')
    expect(await savedDpt(w)).toBe('DPT5.004')
    w.unmount()
  })

  it('a new binding takes the catalog main type instead of the form default', async () => {
    const w = await mountForm()
    await pickGroupAddress(w, '2/1/1', 'DPT9')
    expect(await savedDpt(w)).toBe('DPT9')
    w.unmount()
  })
})
