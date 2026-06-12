// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { datapoints, getWriteContext } from '@/api/client'
import ButtonGroupWidget from './Widget.vue'

const writeContext = vi.hoisted(() => ({
  current: {
    pageId: 'page-1',
    sessionToken: 'token-1',
    definingId: 'def-1',
  },
}))

vi.mock('@/api/client', () => ({
  datapoints: {
    write: vi.fn().mockResolvedValue(undefined),
  },
  getWriteContext: vi.fn(() => writeContext.current),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      key === 'widgets.buttongroup.defaultButtonWithNumber' ? `Button ${params?.number}` : key,
  }),
}))

const writeMock = vi.mocked(datapoints.write)
const getWriteContextMock = vi.mocked(getWriteContext)

let wrapper: VueWrapper | null = null

function mountWidget() {
  wrapper = mount(ButtonGroupWidget, {
    props: {
      config: {
        buttons: [
          {
            id: 'button-1',
            label: '',
            value: 'true',
            resetEnabled: true,
            resetValue: 'false',
            resetDelayMs: 1000,
          },
        ],
      },
      datapointId: 'dp-1',
      value: null,
      statusValue: null,
      editorMode: false,
      readonly: false,
    },
    global: {
      mocks: {
        $t: (key: string) => key,
      },
      stubs: {
        VisuIcon: true,
      },
    },
  })
  return wrapper
}

beforeEach(() => {
  vi.useFakeTimers()
  writeContext.current = {
    pageId: 'page-1',
    sessionToken: 'token-1',
    definingId: 'def-1',
  }
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('ButtonGroup widget pulses', () => {
  it('uses the captured write context for delayed resets', async () => {
    mountWidget()

    await wrapper!.get('button').trigger('click')
    await Promise.resolve()

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', true, {
      pageId: 'page-1',
      sessionToken: 'token-1',
      definingId: 'def-1',
    })
    expect(getWriteContextMock).toHaveBeenCalledTimes(1)

    writeContext.current = {
      pageId: 'page-2',
      sessionToken: 'token-2',
      definingId: 'def-2',
    }

    await vi.advanceTimersByTimeAsync(1000)

    expect(writeMock).toHaveBeenCalledTimes(2)
    expect(writeMock).toHaveBeenLastCalledWith('dp-1', false, {
      pageId: 'page-1',
      sessionToken: 'token-1',
      definingId: 'def-1',
    })
  })

  it('writes the delayed reset after unmount with the captured context', async () => {
    mountWidget()

    await wrapper!.get('button').trigger('click')
    await Promise.resolve()
    wrapper!.unmount()
    wrapper = null

    await vi.advanceTimersByTimeAsync(1000)

    expect(writeMock).toHaveBeenCalledTimes(2)
    expect(writeMock).toHaveBeenLastCalledWith('dp-1', false, {
      pageId: 'page-1',
      sessionToken: 'token-1',
      definingId: 'def-1',
    })
  })
})
