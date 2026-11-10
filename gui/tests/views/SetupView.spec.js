/**
 * First-run setup page (#1229).
 *
 * An installation without an owner serves nothing but this page: no login, no
 * API. The form therefore has to validate locally and translate the two
 * outcomes the backend can still produce (already claimed / failed).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const push = vi.fn()
const markSetupComplete = vi.fn()

beforeEach(() => {
  vi.resetModules()
  push.mockReset()
  markSetupComplete.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/utils/setupStatus')
  vi.doUnmock('vue-router')
})

async function mountSetup({ createOwner = vi.fn().mockResolvedValue({ data: { username: 'admin' } }) } = {}) {
  vi.doMock('@/api/client', () => ({ setupApi: { createOwner, status: vi.fn() } }))
  vi.doMock('@/utils/setupStatus', () => ({ markSetupComplete, fetchSetupRequired: vi.fn(), resetSetupStatusCache: vi.fn() }))
  vi.doMock('vue-router', () => ({ useRouter: () => ({ push }) }))

  const { default: SetupView } = await import('@/views/SetupView.vue')
  const wrapper = mount(SetupView, {
    global: { stubs: { Spinner: { template: '<span class="spinner" />' } } },
  })
  await flushPromises()
  return { wrapper, createOwner }
}

async function fill(wrapper, { username = 'admin', password = 'a-good-password', repeat = password } = {}) {
  await wrapper.find('[data-testid="setup-username"]').setValue(username)
  await wrapper.find('[data-testid="setup-password"]').setValue(password)
  await wrapper.find('[data-testid="setup-password-repeat"]').setValue(repeat)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

describe('SetupView — validation', () => {
  it('refuses an empty username without calling the API', async () => {
    const { wrapper, createOwner } = await mountSetup()
    await fill(wrapper, { username: '   ' })

    expect(createOwner).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="setup-error"]').text()).toBe('Bitte einen Benutzernamen eingeben.')
  })

  it('refuses a password below the backend minimum', async () => {
    const { wrapper, createOwner } = await mountSetup()
    await fill(wrapper, { password: 'short' })

    expect(createOwner).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="setup-error"]').text()).toContain('mindestens 8 Zeichen')
  })

  it('refuses two passwords that do not match', async () => {
    const { wrapper, createOwner } = await mountSetup()
    await fill(wrapper, { password: 'a-good-password', repeat: 'a-different-password' })

    expect(createOwner).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="setup-error"]').text()).toContain('stimmen nicht überein')
  })
})

describe('SetupView — claiming the installation', () => {
  it('creates the owner with a trimmed username and hands over to the login', async () => {
    const { wrapper, createOwner } = await mountSetup()
    await fill(wrapper, { username: '  owner  ' })

    expect(createOwner).toHaveBeenCalledWith('owner', 'a-good-password')
    expect(markSetupComplete).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith({ name: 'Login' })
    expect(wrapper.find('[data-testid="setup-error"]').exists()).toBe(false)
  })

  it('reports an installation that was claimed in the meantime', async () => {
    const createOwner = vi.fn().mockRejectedValue({ response: { status: 409 } })
    const { wrapper } = await mountSetup({ createOwner })
    await fill(wrapper)

    expect(wrapper.find('[data-testid="setup-error"]').text()).toContain('bereits abgeschlossen')
    expect(push).not.toHaveBeenCalled()
    expect(markSetupComplete).not.toHaveBeenCalled()
  })

  it('reports any other failure without leaving setup mode', async () => {
    const createOwner = vi.fn().mockRejectedValue(new Error('network'))
    const { wrapper } = await mountSetup({ createOwner })
    await fill(wrapper)

    expect(wrapper.find('[data-testid="setup-error"]').text()).toContain('konnte nicht angelegt werden')
    expect(push).not.toHaveBeenCalled()
    expect(markSetupComplete).not.toHaveBeenCalled()
  })

  it('re-enables the submit button after a failure', async () => {
    const createOwner = vi.fn().mockRejectedValue(new Error('network'))
    const { wrapper } = await mountSetup({ createOwner })
    await fill(wrapper)

    expect(wrapper.find('[data-testid="setup-submit"]').attributes('disabled')).toBeUndefined()
  })
})
