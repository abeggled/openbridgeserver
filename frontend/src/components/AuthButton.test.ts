// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AuthButton from './AuthButton.vue'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), currentRoute: { value: { fullPath: '/page-1', meta: {}, name: 'viewer' } } }))

vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('@/stores/visu', () => ({ useVisuStore: () => ({ isLoggedIn: false, logout: vi.fn() }) }))
vi.mock('@/composables/useWebSocket', () => ({ useWebSocket: () => ({ disconnect: vi.fn() }) }))

describe('AuthButton', () => {
  it('opens the login in place of the current page, which comes back after the login', async () => {
    const wrapper = mount(AuthButton, { global: { mocks: { $t: (key: string) => key } } })

    await wrapper.get('button').trigger('click')

    // `replace`: LoginView replaces the login entry with the page again, so the history
    // holds the page once — a back link on it goes to the page before, not to itself.
    expect(router.replace).toHaveBeenCalledWith({ name: 'login', query: { redirect: '/page-1' } })
    expect(router.push).not.toHaveBeenCalled()
  })
})
