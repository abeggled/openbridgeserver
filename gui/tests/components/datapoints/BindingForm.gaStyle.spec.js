// #1296: the KNX binding form shows and accepts group addresses in the project's style.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const SHOWN = {
  ThreeLevel: { stored: '1/0/234', typed: '1/0/235' },
  TwoLevel: { stored: '1/234', typed: '1/235' },
  Free: { stored: '2282', typed: '2283' },
}

let updateBinding

function mockApi(style) {
  updateBinding = vi.fn().mockResolvedValue({})
  vi.doMock('@/api/client', () => ({
    dpApi: { createBinding: vi.fn().mockResolvedValue({}), updateBinding },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: [{ id: 'knx-1', name: 'KNX Test', adapter_type: 'KNX' }] }),
      knxDpts: vi.fn().mockResolvedValue({ data: [] }),
    },
    knxprojApi: { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [], group_address_style: style } }) },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: { archives: [] } }) },
  }))
}

beforeEach(() => { vi.resetModules() })
afterEach(() => { vi.doUnmock('@/api/client') })

async function mountEdit(style) {
  mockApi(style)
  const mod = await import('@/components/datapoints/BindingForm.vue')
  const wrapper = mount(mod.default, {
    props: {
      dpId: 'dp-1',
      dpPersistValue: false,
      dpDataType: 'BOOLEAN',
      initial: {
        id: 'binding-7',
        adapter_instance_id: 'knx-1',
        adapter_type: 'KNX',
        direction: 'SOURCE',
        enabled: true,
        config: { group_address: '1/0/234', dpt_id: 'DPT1.001' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

const gaInput = (wrapper) => wrapper.find('input[autocomplete="off"]')

describe.each(Object.keys(SHOWN))('BindingForm (KNX) in a %s project', (style) => {
  it('shows the stored address in the project style and saves it unchanged', async () => {
    const wrapper = await mountEdit(style)
    expect(gaInput(wrapper).element.value).toBe(SHOWN[style].stored)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(updateBinding.mock.calls[0][2].config.group_address).toBe('1/0/234')
    wrapper.unmount()
  })

  it('accepts an address typed in the project style', async () => {
    const wrapper = await mountEdit(style)
    await gaInput(wrapper).setValue(SHOWN[style].typed)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(updateBinding.mock.calls[0][2].config.group_address).toBe(SHOWN[style].typed)
    wrapper.unmount()
  })
})

// #1296, P4b round 2: a rejected group address is explained in the user's language,
// with an example in the project's style, never as a validator dump.
const EXAMPLE = { ThreeLevel: '1/2/3', TwoLevel: '1/515', Free: '2563' }

async function saveRejected(style, detail) {
  vi.resetModules()
  const wrapper = await mountEdit(style)
  updateBinding.mockRejectedValueOnce({ response: { status: 422, data: { detail } } })
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  return wrapper
}

describe.each(Object.keys(SHOWN))('BindingForm (KNX) rejection in a %s project', (style) => {
  it('explains an invalid command address with an example in the project style', async () => {
    const wrapper = await saveRejected(style, { code: 'knxGroupAddressInvalid', field: 'group_address', value: '1/5000', message: 'Ungültige Gruppenadresse' })
    expect(wrapper.text()).toContain(`„1/5000“ ist keine gültige Gruppenadresse. So sieht eine Adresse in diesem Projekt aus: ${EXAMPLE[style]}`)
    expect(wrapper.text()).not.toContain('[object Object]')
    wrapper.unmount()
  })
})

it('BindingForm names the feedback address and asks for a missing one', async () => {
  let wrapper = await saveRejected('TwoLevel', { code: 'knxGroupAddressInvalid', field: 'state_group_address', value: '40/1', message: 'x' })
  expect(wrapper.text()).toContain('„40/1“ ist keine gültige Rückmelde-Gruppenadresse.')
  wrapper.unmount()
  wrapper = await saveRejected('TwoLevel', { code: 'knxGroupAddressMissing', field: 'group_address', value: '', message: 'x' })
  expect(wrapper.text()).toContain('Bitte eine Gruppenadresse angeben, zum Beispiel 1/515.')
  wrapper.unmount()
})

it('BindingForm keeps showing other errors as before', async () => {
  let wrapper = await saveRejected('TwoLevel', 'Ungültige Formel: x')
  expect(wrapper.text()).toContain('Ungültige Formel: x')
  wrapper.unmount()
  wrapper = await saveRejected('TwoLevel', [{ loc: ['body'], msg: 'bad' }])
  expect(wrapper.text()).toContain('Fehler beim Speichern')
  wrapper.unmount()
  wrapper = await saveRejected('TwoLevel', undefined)
  expect(wrapper.text()).toContain('Fehler beim Speichern')
  wrapper.unmount()
})
