import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const pushMock = vi.fn()
const connectMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  pushMock.mockReset()
  connectMock.mockReset()

  vi.doMock('vue-router', () => ({
    useRouter: () => ({ push: pushMock }),
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connect: connectMock }),
  }))
})

afterEach(() => {
  vi.doUnmock('vue-router')
  vi.doUnmock('@/stores/websocket')
  vi.doUnmock('@/api/client')
})

async function mountLogin({ authApiMock = {} } = {}) {
  const defaultAuthApi = {
    login: vi.fn().mockResolvedValue({ data: { access_token: 'at', refresh_token: 'rt' } }),
    me:    vi.fn().mockResolvedValue({ data: { id: 1, username: 'admin', is_admin: true } }),
    ...authApiMock,
  }
  vi.doMock('@/api/client', () => ({ authApi: defaultAuthApi }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: LoginView } = await import('@/views/LoginView.vue')
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia] },
  })

  return { wrapper, authApi: defaultAuthApi }
}

describe('LoginView', () => {
  it('renders the username and password inputs', async () => {
    const { wrapper } = await mountLogin()
    expect(wrapper.find('[data-testid="input-username"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="input-password"]').exists()).toBe(true)
  })

  it('renders the submit button', async () => {
    const { wrapper } = await mountLogin()
    expect(wrapper.find('[data-testid="btn-login"]').exists()).toBe(true)
  })

  it('shows the app version string', async () => {
    const { wrapper } = await mountLogin()
    expect(wrapper.text()).toContain('test')
  })

  it('calls auth.login with entered credentials on submit', async () => {
    const { wrapper, authApi } = await mountLogin()

    await wrapper.find('[data-testid="input-username"]').setValue('admin')
    await wrapper.find('[data-testid="input-password"]').setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(authApi.login).toHaveBeenCalledWith('admin', 'secret')
  })

  it('on successful login calls ws.connect() and redirects to /', async () => {
    const { wrapper } = await mountLogin()

    await wrapper.find('[data-testid="input-username"]').setValue('admin')
    await wrapper.find('[data-testid="input-password"]').setValue('secret')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(connectMock).toHaveBeenCalled()
    expect(pushMock).toHaveBeenCalledWith('/')
  })

  it('shows error message when auth.error is set', async () => {
    const { wrapper } = await mountLogin({
      authApiMock: {
        login: vi.fn().mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } }),
        me:    vi.fn(),
      },
    })

    await wrapper.find('[data-testid="input-username"]').setValue('admin')
    await wrapper.find('[data-testid="input-password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Invalid credentials')
    expect(pushMock).not.toHaveBeenCalled()
  })

  it('does not redirect on failed login', async () => {
    const { wrapper } = await mountLogin({
      authApiMock: {
        login: vi.fn().mockRejectedValue(new Error('network')),
        me:    vi.fn(),
      },
    })

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(pushMock).not.toHaveBeenCalled()
  })
})
