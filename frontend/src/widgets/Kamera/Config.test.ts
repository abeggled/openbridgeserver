// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import KameraConfig from './Config.vue'

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    treeLoaded: true,
    loadTree: vi.fn(),
    nodes: [],
    getNode: () => undefined,
  }),
}))

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

  describe('click action', () => {
    function lastPayload(wrapper: ReturnType<typeof mountConfig>) {
      const emitted = wrapper.emitted('update:modelValue') ?? []
      return emitted[emitted.length - 1][0] as Record<string, unknown>
    }

    it('defaults to no action and hides the page picker', () => {
      const wrapper = mountConfig()
      const select = wrapper.get('[data-testid="kamera-config-click-action"]')
      expect((select.element as HTMLSelectElement).value).toBe('none')
      expect(wrapper.text()).toContain('widgets.kamera.clickAction')
      expect(wrapper.find('[data-testid="kamera-config-click-target"]').exists()).toBe(false)
    })

    it('shows the target page picker for "open page" and emits the action', async () => {
      const wrapper = mountConfig()
      await wrapper.get('[data-testid="kamera-config-click-action"]').setValue('navigate')

      const target = wrapper.get('[data-testid="kamera-config-click-target"]')
      expect(target.text()).toContain('widgets.kamera.clickTargetPage')
      expect(target.text()).not.toContain('widgets.kamera.clickFallbackHint')
      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'navigate', targetNodeId: '' })
    })

    it('labels the picker as optional fallback for "back"', async () => {
      const wrapper = mountConfig({ clickAction: 'back' })
      const target = wrapper.get('[data-testid="kamera-config-click-target"]')
      expect(target.text()).toContain('widgets.kamera.clickFallbackPage')
      expect(target.text()).toContain('widgets.kamera.clickFallbackHint')
    })

    it('loads a stored target page and emits it with the config', async () => {
      const wrapper = mountConfig({ clickAction: 'navigate', targetNodeId: 'big-cam' })
      await wrapper.get('[data-testid="kamera-config-click-action"]').setValue('back')

      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'back', targetNodeId: 'big-cam' })
    })

    it('drops the target page when the action is set to "nothing"', async () => {
      const wrapper = mountConfig({ clickAction: 'navigate', targetNodeId: 'big-cam' })
      const select = wrapper.get('[data-testid="kamera-config-click-action"]')

      await select.setValue('none')
      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'none', targetNodeId: '' })

      // Later switching to "back" must not bring the hidden page back as fallback.
      await select.setValue('back')
      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'back', targetNodeId: '' })
    })

    it('ignores a target page stored together with "nothing"', async () => {
      const wrapper = mountConfig({ clickAction: 'none', targetNodeId: 'big-cam' })
      await wrapper.get('[data-testid="kamera-config-click-action"]').setValue('back')

      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'back', targetNodeId: '' })
    })

    it('emits the page chosen in the picker', async () => {
      const wrapper = mountConfig({ clickAction: 'navigate' })
      wrapper.findComponent({ name: 'PagePicker' }).vm.$emit('update:modelValue', 'big-cam')
      await wrapper.vm.$nextTick()

      expect(lastPayload(wrapper)).toMatchObject({ clickAction: 'navigate', targetNodeId: 'big-cam' })
    })
  })
})
