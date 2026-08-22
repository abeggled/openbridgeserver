// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LoginView from './LoginView.vue'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  storeLogin: vi.fn().mockResolvedValue(undefined),
  wsConnect: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push }),
  useRoute: () => ({ query: {} }),
}))

vi.mock('@/api/client', () => ({
  auth: { login: mocks.login },
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({ login: mocks.storeLogin }),
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({ connect: mocks.wsConnect }),
}))

function mountLogin() {
  return mount(LoginView, { global: { mocks: { $t: (key: string) => key } } })
}

describe('LoginView', () => {
  beforeEach(() => {
    mocks.login.mockReset()
    mocks.storeLogin.mockClear()
    mocks.push.mockClear()
  })

  it('hands both tokens from the login response to the store', async () => {
    mocks.login.mockResolvedValue({
      access_token: 'jwt-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
    })

    const wrapper = mountLogin()
    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mocks.storeLogin).toHaveBeenCalledWith('jwt-1', 'refresh-1')
    expect(mocks.push).toHaveBeenCalledWith({ name: 'tree' })
  })

  it('shows the API error message and keeps the user on the form', async () => {
    mocks.login.mockRejectedValue(new Error('login.invalidCredentials'))

    const wrapper = mountLogin()
    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mocks.storeLogin).not.toHaveBeenCalled()
    expect(mocks.push).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('login.invalidCredentials')
  })
})
