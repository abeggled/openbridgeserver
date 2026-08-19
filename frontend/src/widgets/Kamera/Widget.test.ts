// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import KameraWidget from './Widget.vue'

function mountWidget(
  config: Record<string, unknown> = {
    url: 'http://camera.local/stream',
    useProxy: true,
  },
  editorMode = false,
  pageId?: string | null,
  sessionToken?: string | null,
) {
  return mount(KameraWidget, {
    props: {
      config,
      datapointId: null,
      value: null,
      statusValue: null,
      editorMode,
      pageId,
      sessionToken,
    },
    global: {
      mocks: { $t: (key: string) => key },
    },
  })
}

beforeEach(() => {
  localStorage.setItem('visu_jwt', 'jwt-1')
})

afterEach(() => {
  localStorage.clear()
})

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

  it('adds the viewer page id to proxied camera URLs', () => {
    const wrapper = mountWidget(undefined, false, 'page-1')

    const src = wrapper.get('img').attributes('src')
    expect(src).toBeDefined()
    if (!src) return
    const params = new URLSearchParams(src.split('?')[1])

    expect(src.startsWith('/api/v1/camera/proxy?')).toBe(true)
    expect(params.get('url')).toBe('http://camera.local/stream')
    expect(params.get('_token')).toBe('jwt-1')
    expect(params.get('page_id')).toBe('page-1')
  })

  it('adds the protected page session token to proxied camera URLs', () => {
    const wrapper = mountWidget(undefined, false, 'page-1', 'session-1')

    const src = wrapper.get('img').attributes('src')
    expect(src).toBeDefined()
    if (!src) return
    const params = new URLSearchParams(src.split('?')[1])

    expect(params.get('session_token')).toBe('session-1')
  })

  it('marks proxied editor previews so draft camera URLs are not page-config scoped', () => {
    const wrapper = mountWidget(undefined, true, 'page-1', 'session-1')

    const src = wrapper.get('img').attributes('src')
    expect(src).toBeDefined()
    if (!src) return
    const params = new URLSearchParams(src.split('?')[1])

    expect(params.get('editor_preview')).toBe('1')
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
