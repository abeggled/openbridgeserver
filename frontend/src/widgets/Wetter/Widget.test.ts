// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getJwt, getWriteContext } from '@/api/client'
import WetterWidget from './Widget.vue'

const apiState = vi.hoisted(() => ({
  jwt: 'jwt-1',
  context: {
    pageId: 'page-1',
    sessionToken: 'session-1',
    definingId: 'def-1',
  },
}))

vi.mock('@/api/client', () => ({
  getJwt: vi.fn(() => apiState.jwt),
  getWriteContext: vi.fn(() => apiState.context),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'de' },
  }),
}))

const getJwtMock = vi.mocked(getJwt)
const getWriteContextMock = vi.mocked(getWriteContext)

let wrapper: VueWrapper | null = null

function mountWidget() {
  wrapper = mount(WetterWidget, {
    props: {
      config: {
        url: 'http://example.com/weather',
        refreshInterval: 600,
      },
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
  return wrapper
}

beforeEach(() => {
  apiState.jwt = 'jwt-1'
  apiState.context = {
    pageId: 'page-1',
    sessionToken: 'session-1',
    definingId: 'def-1',
  }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: vi.fn().mockResolvedValue({
      timezone: 'Europe/Zurich',
      current: {
        dt: 1714000000,
        sunrise: 1713930000,
        sunset: 1713980000,
        temp: 14.5,
        feels_like: 12.3,
        humidity: 68,
        pressure: 1015,
        uvi: 3.2,
        visibility: 10000,
        wind_speed: 3.1,
        wind_deg: 200,
        clouds: 40,
        weather: [{ id: 802, main: 'Clouds', description: 'cloudy', icon: '03d' }],
      },
      daily: [],
    }),
  }))
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('Wetter widget fetch auth', () => {
  it('sends JWT and protected-page context headers to the weather proxy', async () => {
    mountWidget()
    await flushPromises()

    const fetchMock = vi.mocked(fetch)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/weather/fetch?url=http%3A%2F%2Fexample.com%2Fweather')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      headers: {
        Authorization: 'Bearer jwt-1',
        'X-Page-Id': 'page-1',
        'X-Session-Token': 'session-1',
      },
    })
    expect(getJwtMock).toHaveBeenCalledTimes(1)
    expect(getWriteContextMock).toHaveBeenCalledTimes(1)
  })
})
