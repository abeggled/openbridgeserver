import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

let api
let axiosDefault

beforeEach(() => {
  vi.resetModules()
  api = Object.assign(vi.fn().mockResolvedValue({ data: {} }), {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  })
  axiosDefault = {
    create: vi.fn(() => api),
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  }
  vi.doMock('axios', () => ({ default: axiosDefault }))
})

afterEach(() => {
  vi.doUnmock('axios')
})

describe('visuDevicesApi client', () => {
  it('reads and replaces the Visu device profiles and lists the Visu pages', async () => {
    const { visuDevicesApi } = await import('@/api/client')
    const devices = [{ slug: 'panel', name: 'Panel', start_node_id: null, return_after_s: 60 }]

    await visuDevicesApi.list()
    await visuDevicesApi.update(devices)
    await visuDevicesApi.pages()

    expect(api.get).toHaveBeenCalledWith('/system/visu-devices')
    expect(api.put).toHaveBeenCalledWith('/system/visu-devices', { devices })
    expect(api.get).toHaveBeenCalledWith('/visu/tree')
  })
})

describe('visuAlarmsApi client', () => {
  it('reads and replaces the Visu alarm definitions', async () => {
    const { visuAlarmsApi } = await import('@/api/client')
    const alarms = [{ id: 'post', name: 'Post' }]

    await visuAlarmsApi.list()
    await visuAlarmsApi.update(alarms)

    expect(api.get).toHaveBeenCalledWith('/system/visu-alarms')
    expect(api.put).toHaveBeenCalledWith('/system/visu-alarms', { alarms })
  })
})
