// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { datapoints } from '@/api/client'
import type { DataPointValue } from '@/types'
import StufenschalterWidget from './Widget.vue'

vi.mock('@/api/client', () => ({
  datapoints: {
    write: vi.fn().mockResolvedValue(undefined),
  },
}))

vi.mock('@/composables/useIcons', () => ({
  useIcons: () => ({
    getSvg: vi.fn().mockResolvedValue(''),
    isSvgIcon: vi.fn().mockReturnValue(false),
    svgIconName: vi.fn(),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, number>) => {
      if (key === 'widgets.stufenschalter.defaultOffLabel') return 'Off'
      if (key === 'widgets.stufenschalter.defaultStepLabel') return `Step ${params?.n ?? ''}`.trim()
      if (key === 'widgets.stufenschalter.writeError') return 'Schreiben fehlgeschlagen'
      return key
    },
  }),
}))

const writeMock = vi.mocked(datapoints.write)

let wrapper: VueWrapper | null = null

function dataPointValue(value: unknown): DataPointValue {
  return {
    id: 'dp-1',
    v: value,
    u: null,
    t: '2026-06-04T00:00:00Z',
    q: 'good',
  }
}

function baseOptions() {
  return [
    { label: 'Off', value: '0', icon: '', color: '#6b7280' },
    { label: 'Eco', value: '1', icon: '', color: '#3b82f6' },
    { label: 'Komfort', value: '2', icon: '', color: '#10b981' },
  ]
}

function mountWidget(config: Record<string, unknown>, value: unknown = 0) {
  wrapper = mount(StufenschalterWidget, {
    props: {
      config: {
        label: 'Warmwasserbetrieb',
        ...config,
      },
      datapointId: 'dp-1',
      value: dataPointValue(value),
      statusValue: null,
      editorMode: false,
      readonly: false,
    },
  })
  return wrapper
}

function legacyStepLabel(n: number): string {
  return `Stufe ${n}`
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  writeMock.mockClear()
})

describe('Stufenschalter widget', () => {
  it('localizes legacy German default labels without reopening the config', () => {
    mountWidget({
      steps: [
        { label: 'Aus', value: '0', icon: '', color: '#6b7280' },
        { label: legacyStepLabel(1), value: '1', icon: '', color: '#3b82f6' },
        { label: legacyStepLabel(2), value: '2', icon: '', color: '#10b981' },
      ],
    }, 2)

    expect(wrapper!.get('[data-testid="stufenschalter-label"]').text()).toBe('Step 2')
  })

  it('derives default step labels from the stored value after reordering', () => {
    mountWidget({
      steps: [
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '1', icon: '', color: '#3b82f6' },
        { label: 'widgets.stufenschalter.defaultOffLabel', value: '0', icon: '', color: '#6b7280' },
      ],
    }, 1)

    expect(wrapper!.get('[data-testid="stufenschalter-label"]').text()).toBe('Step 1')
  })

  it('keeps existing config without mode as sequence and writes the next value', async () => {
    mountWidget({ steps: baseOptions() }, 0)

    await wrapper!.trigger('click')

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 1)
  })

  it('does not write when an option is selected in select-save mode', async () => {
    mountWidget({ mode: 'select-save', options: baseOptions() }, 0)

    await wrapper!.findAll('[data-testid="stufenschalter-option"]')[1].trigger('click')

    expect(writeMock).not.toHaveBeenCalled()
  })

  it('writes the selected value when save is clicked in select-save mode', async () => {
    mountWidget({ mode: 'select-save', options: baseOptions() }, 0)

    await wrapper!.findAll('[data-testid="stufenschalter-option"]')[2].trigger('click')
    await wrapper!.get('[data-testid="stufenschalter-save"]').trigger('click')

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 2)
  })

  it('disables save when selection is unchanged in select-save mode', async () => {
    mountWidget({ mode: 'select-save', options: baseOptions() }, 1)

    const saveButton = wrapper!.get('[data-testid="stufenschalter-save"]')

    expect((saveButton.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('writes immediately when an option is selected in select-direct mode', async () => {
    mountWidget({ mode: 'select-direct', options: baseOptions() }, 0)

    await wrapper!.findAll('[data-testid="stufenschalter-option"]')[2].trigger('click')

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 2)
  })

  it('accepts options for sequence mode', async () => {
    mountWidget({ mode: 'sequence', options: baseOptions() }, 1)

    await wrapper!.trigger('click')

    expect(writeMock).toHaveBeenCalledTimes(1)
    expect(writeMock).toHaveBeenCalledWith('dp-1', 2)
  })
})
