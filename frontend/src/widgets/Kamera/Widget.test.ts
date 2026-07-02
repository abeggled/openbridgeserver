// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import KameraWidget from './Widget.vue'

function mountWidget(config: Record<string, unknown>, editorMode = false) {
  return mount(KameraWidget, {
    props: {
      config,
      datapointId: null,
      value: null,
      statusValue: null,
      editorMode,
    },
    global: {
      mocks: { $t: (key: string) => key },
    },
  })
}

describe('Kamera Widget.vue', () => {
  it('embeds basic auth credentials in URL', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'basic',
      username: 'admin',
      password: 'secret',
      useProxy: false,
    })
    expect(wrapper.find('img').attributes('src')).toBe('http://admin:secret@camera.local/stream.mjpeg')
  })

  it('normalizes legacy full-text authType for Basic Auth', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'Basic Auth (Benutzername / Passwort)',
      username: 'admin',
      password: 'secret',
      useProxy: false,
    })
    expect(wrapper.find('img').attributes('src')).toBe('http://admin:secret@camera.local/stream.mjpeg')
  })

  it('normalizes legacy full-text authType for API key', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'API-Key (Query-Parameter)',
      apiKeyParam: 'token',
      apiKeyValue: 'abc123',
      useProxy: false,
    })
    expect(wrapper.find('img').attributes('src')).toBe('http://camera.local/stream.mjpeg?token=abc123')
  })

  it('appends API key as query parameter', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'apikey',
      apiKeyParam: 'token',
      apiKeyValue: 'abc123',
      useProxy: false,
    })
    expect(wrapper.find('img').attributes('src')).toBe('http://camera.local/stream.mjpeg?token=abc123')
  })

  it('builds proxy URL for basic auth', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'basic',
      username: 'admin',
      password: 'secret',
      useProxy: true,
    })
    const src = wrapper.find('img').attributes('src') ?? ''
    expect(src).toContain('/api/v1/camera/proxy')
    expect(src).toContain('username=admin')
    expect(src).toContain('password=secret')
  })

  it('shows placeholder in editor mode when no URL', () => {
    const wrapper = mountWidget({ url: '', streamType: 'mjpeg' }, true)
    expect(wrapper.text()).toContain('widgets.kamera.configureUrl')
  })

  it('applies aspect ratio and object fit styles', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'none',
      aspectRatio: '4/3',
      objectFit: 'cover',
    })
    const img = wrapper.find('img')
    expect(img.attributes('style')).toContain('aspect-ratio: 4/3')
    expect(img.attributes('style')).toContain('object-fit: cover')
  })
})
