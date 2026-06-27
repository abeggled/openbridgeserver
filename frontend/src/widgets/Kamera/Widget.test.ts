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
})
