// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useVisuAlarms } from './useVisuAlarms'

class FakeSocket {
  static instances: FakeSocket[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeSocket.instances.push(this)
  }
  close() {
    this.closed = true
    this.onclose?.()
  }
}

function mountComposable() {
  let api!: ReturnType<typeof useVisuAlarms>
  const wrapper = mount(defineComponent({ setup() { api = useVisuAlarms(); return () => h('div') } }))
  return { wrapper, api }
}

const last = () => FakeSocket.instances[FakeSocket.instances.length - 1]

beforeEach(() => {
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useVisuAlarms', () => {
  it('connects to the alarm channel and takes over the pushed list', () => {
    const { api } = mountComposable()

    expect(last().url).toBe(`ws://${location.host}/api/v1/system/visu-alarms/ws`)
    last().onmessage?.({ data: JSON.stringify({ action: 'visu_alarms', alarms: [{ id: 'post' }] }) })
    expect(api.alarms.value).toEqual([{ id: 'post' }])

    last().onmessage?.({ data: JSON.stringify({ action: 'ping' }) })
    last().onmessage?.({ data: JSON.stringify({ action: 'visu_alarms', alarms: 'nope' }) })
    last().onmessage?.({ data: 'not json' })
    expect(api.alarms.value).toEqual([{ id: 'post' }])
  })

  it('uses wss on https pages', () => {
    vi.stubGlobal('location', { protocol: 'https:', host: 'obs.example' })
    mountComposable()
    expect(last().url).toBe('wss://obs.example/api/v1/system/visu-alarms/ws')
  })

  it('reconnects with growing delays and resets them after a successful open', () => {
    mountComposable()
    const delays: number[] = []
    for (let i = 0; i < 7; i++) {
      const before = FakeSocket.instances.length
      last().onclose?.()
      let waited = 0
      while (FakeSocket.instances.length === before) { vi.advanceTimersByTime(500); waited += 500 }
      delays.push(waited)
    }
    expect(delays).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000])

    last().onopen?.()
    const before = FakeSocket.instances.length
    last().onclose?.()
    vi.advanceTimersByTime(1000)
    expect(FakeSocket.instances.length).toBe(before + 1)
  })

  it('stops for good when the component goes away', () => {
    const { wrapper } = mountComposable()
    last().onclose?.()
    wrapper.unmount() // clears the pending reconnect

    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.instances).toHaveLength(1)

    const { wrapper: second } = mountComposable()
    const socket = last()
    second.unmount()
    expect(socket.closed).toBe(true)
    vi.advanceTimersByTime(60_000)
    expect(FakeSocket.instances).toHaveLength(2)
  })
})
