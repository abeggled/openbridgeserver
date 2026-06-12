// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { datapoints } from '@/api/client'
import type { DataPointValue } from '@/types'
import SliderWidget from './Widget.vue'

vi.mock('@/api/client', () => ({
  datapoints: {
    write: vi.fn().mockResolvedValue(undefined),
  },
}))

const writeMock = vi.mocked(datapoints.write)

let wrapper: VueWrapper | null = null

function dataPointValue(value: unknown): DataPointValue {
  return {
    id: 'dp-1',
    v: value,
    u: null,
    t: '2026-05-27T00:00:00Z',
    q: 'good',
  }
}

function mountSlider() {
  wrapper = mount(SliderWidget, {
    props: {
      config: { label: 'Brightness', min: 0, max: 100, step: 1 },
      datapointId: 'dp-1',
      value: dataPointValue(10),
      statusValue: null,
      editorMode: false,
      readonly: false,
    },
  })

  return wrapper
}

async function inputValue(nextValue: string) {
  const input = wrapper!.get('input[type="range"]')
  ;(input.element as HTMLInputElement).value = nextValue
  await input.trigger('input')
  return input
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  writeMock.mockClear()
})

describe('Slider widget commits', () => {
  it('writes the dragged value on window pointerup even when no change event fires', async () => {
    mountSlider()

    await inputValue('42')
    expect(writeMock).not.toHaveBeenCalled()

    window.dispatchEvent(new Event('pointerup'))

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 42)
  })

  it('does not write the same final value twice when pointerup and change both fire', async () => {
    mountSlider()
    const input = await inputValue('37')

    await input.trigger('pointerup')
    await input.trigger('change')

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 37)
  })

  it('writes keyboard commits without requiring a change event', async () => {
    mountSlider()
    const input = await inputValue('55')

    await input.trigger('keyup', { key: 'Enter' })

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 55)
  })
})
