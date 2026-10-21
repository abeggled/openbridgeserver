// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PinAuth from './PinAuth.vue'

const mocks = vi.hoisted(() => ({
  authenticatePin: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push, replace: mocks.replace }) }))
vi.mock('@/stores/visu', () => ({ useVisuStore: () => ({ authenticatePin: mocks.authenticatePin }) }))

function mountPinAuth() {
  return mount(PinAuth, { props: { id: 'page-1' }, global: { mocks: { $t: (key: string) => key } } })
}

async function submitPin(wrapper: ReturnType<typeof mountPinAuth>, pin: string) {
  await wrapper.find('input[type="password"]').setValue(pin)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

describe('PinAuth', () => {
  beforeEach(() => {
    mocks.authenticatePin.mockReset()
    mocks.push.mockClear()
    mocks.replace.mockClear()
  })

  it('opens the page after the right PIN, replacing itself in the history', async () => {
    mocks.authenticatePin.mockResolvedValue(undefined)
    const wrapper = mountPinAuth()

    await submitPin(wrapper, '1234')

    expect(mocks.authenticatePin).toHaveBeenCalledWith('page-1', '1234')
    // `replace`: "back" from the page must not land on the PIN dialog again
    expect(mocks.replace).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'page-1' } })
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('shows the error and clears the field after a wrong PIN', async () => {
    mocks.authenticatePin.mockRejectedValue(new Error('403'))
    const wrapper = mountPinAuth()

    await submitPin(wrapper, '0000')

    expect(wrapper.text()).toContain('pin.wrongPin')
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('')
    expect(mocks.replace).not.toHaveBeenCalled()
  })

  it('does nothing without a PIN and goes to the overview via the back button', async () => {
    const wrapper = mountPinAuth()
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocks.authenticatePin).not.toHaveBeenCalled()

    const buttons = wrapper.findAll('button')
    await buttons[buttons.length - 1].trigger('click')
    expect(mocks.push).toHaveBeenCalledWith({ name: 'tree' })
  })
})
