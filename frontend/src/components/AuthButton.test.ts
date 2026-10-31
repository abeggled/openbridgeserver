// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import AuthButton from './AuthButton.vue'

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), currentRoute: { value: { fullPath: '/page-1', meta: {}, name: 'viewer' } } }))

vi.mock('vue-router', () => ({ useRouter: () => router }))
vi.mock('@/stores/visu', () => ({ useVisuStore: () => ({ isLoggedIn: false, logout: vi.fn() }) }))
vi.mock('@/composables/useWebSocket', () => ({ useWebSocket: () => ({ disconnect: vi.fn() }) }))

describe('AuthButton', () => {
  it('opens the login on top of the current page, which stays reachable for the way back', async () => {
    const wrapper = mount(AuthButton, { global: { mocks: { $t: (key: string) => key } } })

    await wrapper.get('button').trigger('click')

    // `push`: cancelling with the browser's back button returns to the page; after the
    // login LoginView goes back to it, so it is never in the history twice.
    expect(router.push).toHaveBeenCalledWith({ name: 'login', query: { redirect: '/page-1' } })
    expect(router.replace).not.toHaveBeenCalled()
  })
})
