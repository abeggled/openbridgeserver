// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import LoginView from './LoginView.vue'

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  storeLogin: vi.fn().mockResolvedValue(undefined),
  wsConnect: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  query: {} as Record<string, string>,
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace, back: mocks.back }),
  useRoute: () => ({ query: mocks.query }),
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
    mocks.replace.mockClear()
    mocks.back.mockClear()
    mocks.query = {}
    window.history.replaceState(null, '')
  })

  async function submit(wrapper: ReturnType<typeof mountLogin>) {
    await wrapper.find('input[type="text"]').setValue('admin')
    await wrapper.find('input[type="password"]').setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
  }

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
    // `replace`: the login form must not stay in the history behind the page
    expect(mocks.replace).toHaveBeenCalledWith({ name: 'tree' })
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('returns to the page it was sent from, replacing itself in the history', async () => {
    // The viewer or the router guard replaced the page with the login: the entry behind
    // the login is some other page, so the login entry becomes the page again.
    mocks.login.mockResolvedValue({ access_token: 'jwt-1', refresh_token: 'refresh-1', token_type: 'bearer' })
    mocks.query = { redirect: '/page-1' }
    window.history.replaceState({ back: '/start' }, '')

    await submit(mountLogin())

    expect(mocks.replace).toHaveBeenCalledWith('/page-1')
    expect(mocks.back).not.toHaveBeenCalled()
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('goes back when the page it was sent from is right behind it, so the page is not in the history twice', async () => {
    // The header button and the 401 handler push the login on top of the page.
    mocks.login.mockResolvedValue({ access_token: 'jwt-1', refresh_token: 'refresh-1', token_type: 'bearer' })
    mocks.query = { redirect: '/page-1' }
    window.history.replaceState({ back: '/page-1' }, '')

    await submit(mountLogin())

    expect(mocks.back).toHaveBeenCalledTimes(1)
    expect(mocks.replace).not.toHaveBeenCalled()
    expect(mocks.push).not.toHaveBeenCalled()
  })

  it('leaves the login out of the history when the user goes to the overview without logging in', async () => {
    const wrapper = mountLogin()
    const buttons = wrapper.findAll('button')
    await buttons[buttons.length - 1].trigger('click')

    expect(mocks.replace).toHaveBeenCalledWith({ name: 'tree' })
    expect(mocks.push).not.toHaveBeenCalled()
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
    expect(mocks.replace).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('login.invalidCredentials')
  })
})
