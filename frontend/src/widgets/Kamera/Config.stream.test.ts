// @vitest-environment jsdom
// Kamera-Konfiguration mit Quelle „Kamera-Server“ (go2rtc / Frigate, #1479).
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listInstances: vi.fn(),
  cameras: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/api/client')>()
  return {
    ...mod,
    adapters: { ...mod.adapters, listInstances: api.listInstances },
    cameraStreams: { ...mod.cameraStreams, cameras: api.cameras },
  }
})
vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({ treeLoaded: true, loadTree: vi.fn(), nodes: [], getNode: () => undefined }),
}))

import KameraConfig from './Config.vue'

const INSTANCES = [
  { id: 'fr', adapter_type: 'FRIGATE', name: 'Frigate', running: true, connected: true },
  { id: 'g2', adapter_type: 'GO2RTC', name: 'go2rtc', running: true, connected: true },
  { id: 'knx', adapter_type: 'KNX', name: 'KNX', running: true, connected: true },
]
const FRIGATE_CAMERAS = [
  { name: 'hof', streams: [{ label: 'cam_hof', src: 'cam_hof' }, { label: 'cam_hof_sub', src: 'cam_hof_sub' }] },
  { name: 'nord', streams: [{ label: 'Main', src: 'n_main' }, { label: 'Sub', src: 'n_sub' }] },
]
const GO2RTC_CAMERAS = [
  { name: 'a', streams: [{ label: 'a', src: 'a' }] },
  { name: 'b', streams: [{ label: 'b', src: 'b' }] },
]

const wrappers: VueWrapper[] = []

function mountConfig(modelValue: Record<string, unknown> | null = { source: 'go2rtc' }) {
  const w = mount(KameraConfig, {
    props: { modelValue },
    global: { mocks: { $t: (key: string) => key } },
  })
  wrappers.push(w)
  return w
}

function lastEmit(w: VueWrapper): Record<string, unknown> {
  const events = w.emitted('update:modelValue') as Array<[Record<string, unknown>]>
  return events[events.length - 1][0]
}

const options = (w: VueWrapper, testid: string) =>
  w.get(`[data-testid="${testid}"]`).findAll('option').map(o => o.attributes('value'))

beforeEach(() => {
  api.listInstances.mockResolvedValue(INSTANCES)
  api.cameras.mockImplementation(async (id: string) => (id === 'fr' ? FRIGATE_CAMERAS : GO2RTC_CAMERAS))
})

afterEach(() => {
  for (const w of wrappers.splice(0)) w.unmount()
  vi.clearAllMocks()
})

describe('Kamera config — camera server source', () => {
  it('URL source hides the server fields and loads nothing', async () => {
    const w = mountConfig(null)
    await flushPromises()
    expect(w.find('[data-testid="kamera-config-instance"]').exists()).toBe(false)
    expect(api.listInstances).not.toHaveBeenCalled()
  })

  it('switching to the camera server loads only stream-source instances', async () => {
    const w = mountConfig({})
    await w.get('[data-testid="kamera-config-source"]').setValue('go2rtc')
    await flushPromises()
    expect(options(w, 'kamera-config-instance')).toEqual(['', 'fr', 'g2'])
    expect(api.cameras).not.toHaveBeenCalled()  // noch keine Instanz
    expect(w.text()).not.toContain('widgets.kamera.streamUrl')
    expect(lastEmit(w).source).toBe('go2rtc')

    // Zurück und wieder hin: Instanzen werden nicht erneut geladen.
    await w.get('[data-testid="kamera-config-source"]').setValue('url')
    await w.get('[data-testid="kamera-config-source"]').setValue('go2rtc')
    await flushPromises()
    expect(api.listInstances).toHaveBeenCalledTimes(1)
  })

  it('Frigate: camera choice proposes sub for the tile and main for full screen', async () => {
    const w = mountConfig()
    await flushPromises()
    await w.get('[data-testid="kamera-config-instance"]').setValue('fr')
    await flushPromises()
    expect(api.cameras).toHaveBeenCalledWith('fr')
    expect(options(w, 'kamera-config-camera')).toEqual(['', 'hof', 'nord'])

    await w.get('[data-testid="kamera-config-camera"]').setValue('nord')
    await flushPromises()
    expect(options(w, 'kamera-config-tile-src')).toEqual(['', 'n_main', 'n_sub'])
    expect(lastEmit(w)).toMatchObject({
      source: 'go2rtc',
      streamInstanceId: 'fr',
      streamCamera: 'nord',
      tileSrc: 'n_sub',
      fullscreenSrc: 'n_main',
      preferMode: 'auto',
    })
  })

  it('go2rtc: no camera step, every stream is offered', async () => {
    const w = mountConfig()
    await flushPromises()
    await w.get('[data-testid="kamera-config-instance"]').setValue('g2')
    await flushPromises()
    expect(w.find('[data-testid="kamera-config-camera"]').exists()).toBe(false)
    expect(options(w, 'kamera-config-tile-src')).toEqual(['', 'a', 'b'])

    await w.get('[data-testid="kamera-config-tile-src"]').setValue('a')
    expect(lastEmit(w)).toMatchObject({ tileSrc: 'a', fullscreenSrc: 'a' })
    await w.get('[data-testid="kamera-config-fullscreen-src"]').setValue('b')
    await w.get('[data-testid="kamera-config-tile-src"]').setValue('b')
    expect(lastEmit(w)).toMatchObject({ tileSrc: 'b', fullscreenSrc: 'b' })
    await w.get('[data-testid="kamera-config-tile-src"]').setValue('')
    expect(lastEmit(w)).toMatchObject({ tileSrc: '', fullscreenSrc: 'b' })
    await w.get('[data-testid="kamera-config-prefer-mode"]').setValue('mse')
    expect(lastEmit(w).preferMode).toBe('mse')
  })

  it('an existing configuration is kept on mount', async () => {
    const w = mountConfig({
      source: 'go2rtc', streamInstanceId: 'fr', streamCamera: 'hof', tileSrc: 'cam_hof_sub', fullscreenSrc: 'cam_hof', preferMode: 'mse',
    })
    await flushPromises()
    expect(api.cameras).toHaveBeenCalledWith('fr')
    expect((w.get('[data-testid="kamera-config-camera"]').element as HTMLSelectElement).value).toBe('hof')
    expect((w.get('[data-testid="kamera-config-tile-src"]').element as HTMLSelectElement).value).toBe('cam_hof_sub')
    expect((w.get('[data-testid="kamera-config-prefer-mode"]').element as HTMLSelectElement).value).toBe('mse')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('changing the instance resets camera and streams', async () => {
    const w = mountConfig({ source: 'go2rtc', streamInstanceId: 'fr', streamCamera: 'hof', tileSrc: 'cam_hof_sub', fullscreenSrc: 'cam_hof' })
    await flushPromises()
    await w.get('[data-testid="kamera-config-instance"]').setValue('g2')
    await flushPromises()
    expect(lastEmit(w)).toMatchObject({ streamInstanceId: 'g2', streamCamera: '', tileSrc: '', fullscreenSrc: '' })
    await w.get('[data-testid="kamera-config-instance"]').setValue('')
    await flushPromises()
    expect(api.cameras).toHaveBeenCalledTimes(2)  // ohne Instanz kein Abruf
  })

  it('reports a missing camera list', async () => {
    api.cameras.mockRejectedValue(new Error('502'))
    const w = mountConfig({ source: 'go2rtc', streamInstanceId: 'fr' })
    await flushPromises()
    expect(w.find('[data-testid="kamera-config-cameras-error"]').exists()).toBe(true)
  })

  it('shows a loading hint while the camera list is on its way', async () => {
    let release!: (v: unknown) => void
    api.cameras.mockReturnValue(new Promise((r) => { release = r }))
    const w = mountConfig({ source: 'go2rtc', streamInstanceId: 'fr' })
    await flushPromises()
    expect(w.text()).toContain('widgets.kamera.loadingCameras')
    release(FRIGATE_CAMERAS)
    await flushPromises()
    expect(w.text()).not.toContain('widgets.kamera.loadingCameras')
  })

  it('hints when no camera server exists, also when the list fails', async () => {
    api.listInstances.mockResolvedValue([INSTANCES[2]])
    const w = mountConfig()
    await flushPromises()
    expect(w.find('[data-testid="kamera-config-no-instances"]').exists()).toBe(true)

    api.listInstances.mockRejectedValue(new Error('403'))
    const w2 = mountConfig()
    await flushPromises()
    expect(w2.find('[data-testid="kamera-config-no-instances"]').exists()).toBe(true)
  })

  it('unknown camera yields no stream options', async () => {
    const w = mountConfig({ source: 'go2rtc', streamInstanceId: 'fr', streamCamera: 'gone' })
    await flushPromises()
    expect(options(w, 'kamera-config-tile-src')).toEqual([''])
  })
})
