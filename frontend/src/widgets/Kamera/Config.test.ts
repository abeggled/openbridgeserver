// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import KameraConfig from './Config.vue'

const messages: Record<string, string> = {
  'widgets.common.label': 'Label',
  'widgets.kamera.streamType': 'Stream type',
  'widgets.kamera.streamMjpeg': 'MJPEG',
  'widgets.kamera.streamSnapshot': 'Snapshot',
  'widgets.kamera.streamHls': 'HLS',
  'widgets.kamera.labelPlaceholder': 'e.g. entrance',
  'widgets.kamera.streamUrl': 'Stream URL',
  'widgets.kamera.refreshInterval': 'Refresh interval',
  'widgets.kamera.auth': 'Authentication',
  'widgets.kamera.authNone': 'None',
  'widgets.kamera.authBasic': 'Basic Auth',
  'widgets.kamera.authApiKey': 'API key',
  'widgets.kamera.username': 'Username',
  'widgets.kamera.password': 'Password',
  'widgets.kamera.apiKeyParam': 'Parameter name',
  'widgets.kamera.apiKey': 'API key value',
  'widgets.kamera.credentialWarning': 'Credential warning',
  'widgets.kamera.useProxy': 'Use proxy',
  'widgets.kamera.proxyMixedContentHint': 'proxy hint',
  'widgets.kamera.aspectRatio': 'Aspect ratio',
  'widgets.kamera.aspectSquare': 'Square',
  'widgets.kamera.aspectFree': 'Free',
  'widgets.kamera.objectFit': 'Object fit',
  'widgets.kamera.fitContain': 'Contain',
  'widgets.kamera.fitCover': 'Cover',
  'widgets.kamera.fitFill': 'Fill',
}

function mountConfig(modelValue: Record<string, unknown> | null | undefined = {}) {
  return mount(KameraConfig, {
    props: { modelValue },
    global: {
      mocks: { $t: (key: string) => messages[key] ?? key },
    },
  })
}

describe('Kamera Config.vue', () => {
  it('does not emit on mount', () => {
    const wrapper = mountConfig()
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('shows username/password fields when Basic Auth is selected', async () => {
    const wrapper = mountConfig()
    const selects = wrapper.findAll('select')
    await selects[1].setValue('basic')

    expect(wrapper.text()).toContain('Username')
    expect(wrapper.text()).toContain('Password')
    expect(wrapper.text()).not.toContain('Parameter name')
  })

  it('shows API key fields when API key auth is selected', async () => {
    const wrapper = mountConfig()
    const selects = wrapper.findAll('select')
    await selects[1].setValue('apikey')

    expect(wrapper.text()).toContain('Parameter name')
    expect(wrapper.text()).not.toContain('Username')
  })

  it('emits canonical authType after user selects Basic Auth', async () => {
    const wrapper = mountConfig()
    const selects = wrapper.findAll('select')
    await selects[1].setValue('basic')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toHaveLength(1)
    expect((emitted![0][0] as Record<string, unknown>).authType).toBe('basic')
  })

  it('shows username/password fields when loaded with legacy full-text authType', () => {
    const wrapper = mountConfig({ authType: 'Basic Auth (Benutzername / Passwort)', url: 'http://cam/' })
    expect(wrapper.text()).toContain('Username')
    expect(wrapper.text()).toContain('Password')
  })

  it('normalizes legacy authType and emits canonical value on change', async () => {
    const wrapper = mountConfig({ authType: 'Basic Auth (Benutzername / Passwort)' })
    const inputs = wrapper.findAll('input')
    const usernameInput = inputs.find(i => i.attributes('type') === 'text' && i.attributes('autocomplete') === 'off')
    expect(usernameInput).toBeDefined()
    await usernameInput!.setValue('admin')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const payload = (emitted![emitted!.length - 1][0] as Record<string, unknown>)
    expect(payload.authType).toBe('basic')
  })

  it('handles null modelValue without crashing', () => {
    expect(() => mountConfig(null)).not.toThrow()
  })

  it('handles undefined modelValue without crashing', () => {
    expect(() => mountConfig(undefined)).not.toThrow()
  })

  it('shows refresh interval only for snapshot stream type', async () => {
    const wrapper = mountConfig()
    expect(wrapper.text()).not.toContain('Refresh interval')

    const selects = wrapper.findAll('select')
    await selects[0].setValue('snapshot')
    expect(wrapper.text()).toContain('Refresh interval')
  })

  it('emits full config with all required fields', async () => {
    const wrapper = mountConfig({ url: 'http://cam/', authType: 'none' })
    const urlInput = wrapper.find('input[type="text"]:not([autocomplete])')
    await urlInput.setValue('http://newcam/')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const payload = emitted![emitted!.length - 1][0] as Record<string, unknown>
    expect(payload).toMatchObject({
      authType: 'none',
      streamType: 'mjpeg',
      apiKeyParam: 'token',
      refreshInterval: 5,
      aspectRatio: '16/9',
      objectFit: 'contain',
      useProxy: false,
    })
  })
})
