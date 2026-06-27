// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import KameraWidget from './Widget.vue'

function mountWidget(config: Record<string, unknown>) {
  return mount(KameraWidget, {
    props: {
      config,
      datapointId: null,
      value: null,
      statusValue: null,
      editorMode: false,
    },
    global: {
      mocks: {
        $t: (key: string) => key,
      },
    },
  })
}

describe('Kamera widget auth compatibility', () => {
  it('uses credentials for legacy Basic Auth config values', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      authType: 'Basic Auth',
      username: 'admin',
      password: 'secret',
      useProxy: false,
    })

    expect(wrapper.find('img').attributes('src')).toBe('http://admin:secret@camera.local/stream.mjpeg')
  })

  it('uses snake_case proxy and API key config aliases', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/stream.mjpeg',
      streamType: 'mjpeg',
      auth_type: 'api_key',
      api_key_param: 'token',
      api_key_value: 'abc123',
      use_proxy: 'true',
    })

    const src = wrapper.find('img').attributes('src')

    expect(src).toContain('/api/v1/camera/proxy')
    expect(src).toContain('apikey_param=token')
    expect(src).toContain('apikey_value=abc123')
  })

  it('uses snake_case snapshot and layout config aliases', () => {
    const wrapper = mountWidget({
      url: 'http://camera.local/snapshot.jpg',
      streamType: 'snapshot',
      refresh_interval: '30',
      aspect_ratio: '4/3',
      object_fit: 'cover',
    })

    const img = wrapper.find('img')

    expect(img.attributes('src')).toContain('_t=')
    expect(img.attributes('style')).toContain('aspect-ratio: 4/3')
    expect(img.attributes('style')).toContain('object-fit: cover')
  })
})
