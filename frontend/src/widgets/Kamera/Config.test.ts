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
  'widgets.kamera.aspectRatio': 'Aspect ratio',
  'widgets.kamera.aspectSquare': 'Square',
  'widgets.kamera.aspectFree': 'Free',
  'widgets.kamera.objectFit': 'Object fit',
  'widgets.kamera.fitContain': 'Contain',
  'widgets.kamera.fitCover': 'Cover',
  'widgets.kamera.fitFill': 'Fill',
}

function mountConfig(modelValue: Record<string, unknown> = {}) {
  return mount(KameraConfig, {
    props: { modelValue },
    global: {
      mocks: {
        $t: (key: string) => messages[key] ?? key,
      },
    },
  })
}

describe('Kamera widget config', () => {
  it('keeps the full settings form visible after selecting Basic Auth', async () => {
    const wrapper = mountConfig()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    const selects = wrapper.findAll('select')
    expect(selects).toHaveLength(4)

    await selects[1].setValue('basic')

    expect(wrapper.text()).toContain('Stream type')
    expect(wrapper.text()).toContain('Stream URL')
    expect(wrapper.text()).toContain('Authentication')
    expect(wrapper.text()).toContain('Username')
    expect(wrapper.text()).toContain('Password')
    expect(wrapper.text()).toContain('Use proxy')
    expect(wrapper.text()).toContain('Aspect ratio')
    expect(wrapper.text()).toContain('Object fit')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toMatchObject({
      authType: 'basic',
      streamType: 'mjpeg',
      apiKeyParam: 'token',
      refreshInterval: 5,
      aspectRatio: '16/9',
      objectFit: 'contain',
      useProxy: false,
    })
  })

  it('normalizes legacy Basic Auth values without hiding the form', async () => {
    const wrapper = mountConfig({ authType: 'Basic Auth', username: 'admin' })

    expect(wrapper.text()).toContain('Username')
    expect(wrapper.text()).toContain('Password')
    expect(wrapper.text()).toContain('Stream URL')
    expect((wrapper.find('input[type="text"]').element as HTMLInputElement).value).toBe('')

    const usernameInput = wrapper.findAll('input[type="text"]')[2]
    expect((usernameInput.element as HTMLInputElement).value).toBe('admin')
  })
})
