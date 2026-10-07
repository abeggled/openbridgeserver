import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountGaCombobox(api = {}) {
  const knxprojApi = {
    listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    ...api,
  }
  vi.doMock('@/api/client', () => ({ knxprojApi }))
  const mod = await import('@/components/ui/GaCombobox.vue')
  const wrapper = mount(mod.default, { attachTo: document.body })
  return { wrapper, knxprojApi }
}

describe('GaCombobox', () => {
  it('uses translated default placeholder and empty-state text', async () => {
    const { wrapper } = await mountGaCombobox()
    const input = wrapper.find('input')

    expect(input.attributes('placeholder')).toBe('z.B. 1/2/3 oder Name suchen …')

    await input.trigger('focus')
    input.element.value = '1/2'
    await input.trigger('input')
    await new Promise((resolve) => setTimeout(resolve, 260))
    await flushPromises()

    expect(wrapper.text()).toContain('Keine Gruppenadressen gefunden')
    expect(wrapper.text()).toContain('(.knxproj noch nicht importiert)')
  })
})

// #1296: the combobox shows and accepts addresses in the project's style.
const SHOWN = { ThreeLevel: '1/0/234', TwoLevel: '1/234', Free: '2282' }

function styledApi(style) {
  return {
    listGA: vi.fn().mockImplementation(async (params) => ({
      data: {
        total: 1,
        group_address_style: style,
        items: params?.q ? [{ address: '1/0/234', name: 'Licht Küche', description: '', dpt: 'DPT1.001' }] : [],
      },
    })),
  }
}

async function mountStyled(style, props = {}) {
  vi.doMock('@/api/client', () => ({ knxprojApi: styledApi(style) }))
  const mod = await import('@/components/ui/GaCombobox.vue')
  const wrapper = mount(mod.default, { attachTo: document.body, props })
  await flushPromises()
  return wrapper
}

async function typeInto(wrapper, text) {
  const input = wrapper.find('input')
  input.element.value = text
  await input.trigger('input')
  await new Promise((resolve) => setTimeout(resolve, 260))
  await flushPromises()
}

describe.each(Object.keys(SHOWN))('GaCombobox in a %s project', (style) => {
  it('shows a stored (internal) address in the project style', async () => {
    const wrapper = await mountStyled(style, { modelValue: '1/0/234' })
    expect(wrapper.find('input').element.value).toBe(SHOWN[style])
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.setProps({ modelValue: '1/0/235' })
    expect(wrapper.find('input').element.value).toBe({ ThreeLevel: '1/0/235', TwoLevel: '1/235', Free: '2283' }[style])
  })

  it('lists suggestions in the project style and selects the address', async () => {
    const wrapper = await mountStyled(style)
    await typeInto(wrapper, 'Licht')
    const suggestion = wrapper.find('li')
    expect(suggestion.text()).toContain(SHOWN[style])
    if (style !== 'ThreeLevel') expect(suggestion.text()).not.toContain('1/0/234')

    await suggestion.trigger('click')
    await flushPromises()
    expect(wrapper.find('input').element.value).toBe(SHOWN[style])
    expect(wrapper.emitted('select')[0][0].address).toBe('1/0/234')
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual(['1/0/234'])
  })

  it('takes an address typed in the project style as it is', async () => {
    const wrapper = await mountStyled(style)
    await typeInto(wrapper, SHOWN[style])
    expect(wrapper.find('input').element.value).toBe(SHOWN[style])
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([SHOWN[style]])
    await wrapper.setProps({ modelValue: SHOWN[style] }) // v-model echo
    expect(wrapper.find('input').element.value).toBe(SHOWN[style])
  })

  it('suggests an example in the project style', async () => {
    const wrapper = await mountStyled(style)
    const example = { ThreeLevel: '1/2/3', TwoLevel: '1/515', Free: '2563' }[style]
    expect(wrapper.find('input').attributes('placeholder')).toBe(`z.B. ${example} oder Name suchen …`)
  })
})

it('GaCombobox keeps a three-level address typed in a two-level project untouched', async () => {
  const wrapper = await mountStyled('TwoLevel')
  await typeInto(wrapper, '1/0/234')
  expect(wrapper.find('input').element.value).toBe('1/0/234')
  await wrapper.setProps({ modelValue: '1/0/234' })
  expect(wrapper.find('input').element.value).toBe('1/0/234')
})

it('GaCombobox shows text that is no group address as it is', async () => {
  const wrapper = await mountStyled('TwoLevel', { modelValue: 'Licht' })
  expect(wrapper.find('input').element.value).toBe('Licht')
})
