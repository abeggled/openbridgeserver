import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let knxprojApi

beforeEach(() => {
  vi.resetModules()
  document.body.innerHTML = ''
  knxprojApi = {
    listDevices: vi.fn().mockResolvedValue({
      data: {
        items: [
          {
            pa: '1.1.10',
            name: 'Kitchen Switch',
            manufacturer: 'Siemens',
            order_number: '5WG1',
          },
        ],
      },
    }),
    getDevice: vi.fn().mockResolvedValue({
      data: {
        pa: '1.1.11',
        name: 'Hall Dimmer',
        manufacturer: 'Gira',
      },
    }),
  }
  vi.doMock('@/api/client', () => ({ knxprojApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountCombobox(props = {}) {
  const { default: KnxDeviceCombobox } = await import('@/components/ui/KnxDeviceCombobox.vue')
  const wrapper = mount(KnxDeviceCombobox, {
    props,
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

describe('KnxDeviceCombobox', () => {
  it('searches imported devices and emits selected physical addresses', async () => {
    const wrapper = await mountCombobox({ modelValue: [] })

    const input = wrapper.find('[data-testid="combobox-input"]')
    await input.trigger('focus')
    await flushPromises()

    expect(knxprojApi.listDevices).toHaveBeenCalledWith({ q: '', page: 0, size: 50 })
    expect(wrapper.text()).toContain('Kitchen Switch')
    expect(wrapper.text()).toContain('Siemens')

    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0][0]).toEqual(['1.1.10'])
  })

  it('hydrates labels for existing device chips', async () => {
    const wrapper = await mountCombobox({ modelValue: ['1.1.11'] })

    expect(knxprojApi.getDevice).toHaveBeenCalledWith('1.1.11')
    expect(wrapper.text()).toContain('Hall Dimmer')
  })

  it('announces selected suggestions with the common selected translation', async () => {
    const wrapper = await mountCombobox({ modelValue: ['1.1.10'] })

    const input = wrapper.find('[data-testid="combobox-input"]')
    await input.trigger('focus')
    await flushPromises()

    expect(wrapper.find('[data-testid="combobox-item-0"]').text()).toContain('Ausgewählt')
  })

  it('normalizes physical_address devices and falls back to manufacturer/order labels', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [
          {
            physical_address: '1.2.3',
            name: '',
            manufacturer: 'Gira',
            order_number: 'X1',
          },
          { pa: '' },
        ],
      },
    })
    const wrapper = await mountCombobox({ modelValue: [] })

    await wrapper.find('[data-testid="combobox-input"]').trigger('focus')
    await flushPromises()

    expect(wrapper.find('[data-testid="combobox-item-0"]').text()).toContain('1.2.3')
    expect(wrapper.find('[data-testid="combobox-item-0"]').text()).toContain('Gira X1')
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0][0]).toEqual(['1.2.3'])
  })

  it('uses the raw physical address as chip label when hydration fails', async () => {
    knxprojApi.getDevice.mockRejectedValueOnce(new Error('missing'))

    const wrapper = await mountCombobox({ modelValue: ['1.3.4'] })

    expect(knxprojApi.getDevice).toHaveBeenCalledWith('1.3.4')
    expect(wrapper.find('[data-testid="combobox-chip-0"]').text()).toContain('1.3.4')
  })

  it('hydrates newly added model values from the watcher', async () => {
    knxprojApi.getDevice.mockResolvedValueOnce({
      data: {
        pa: '1.4.5',
        name: 'Watcher Device',
      },
    })
    const wrapper = await mountCombobox({ modelValue: [] })

    await wrapper.setProps({ modelValue: ['1.4.5'] })
    await flushPromises()

    expect(knxprojApi.getDevice).toHaveBeenCalledWith('1.4.5')
    expect(wrapper.find('[data-testid="combobox-chip-0"]').text()).toContain('Watcher Device')
  })

  it('returns an empty suggestion list when device search fails', async () => {
    knxprojApi.listDevices.mockRejectedValueOnce(new Error('offline'))

    const wrapper = await mountCombobox({ modelValue: [] })
    await wrapper.find('[data-testid="combobox-input"]').trigger('focus')
    await flushPromises()

    expect(wrapper.find('[data-testid="combobox-empty"]').text()).toContain('Keine KNX-Geräte gefunden')
  })

  it('normalizes non-array combobox updates to an empty model array', async () => {
    const wrapper = await mountCombobox({ modelValue: [] })
    const combobox = wrapper.findComponent({ name: 'Combobox' })

    await combobox.vm.$emit('update:modelValue', '1.1.10')

    expect(wrapper.emitted('update:modelValue')?.[0][0]).toEqual([])
  })
})
