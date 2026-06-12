// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import StufenschalterWidget from './Widget.vue'

vi.mock('@/api/client', () => ({
  datapoints: {
    write: vi.fn().mockResolvedValue(undefined),
  },
}))

vi.mock('@/composables/useIcons', () => ({
  useIcons: () => ({
    getSvg: vi.fn().mockResolvedValue(''),
    isSvgIcon: vi.fn(() => false),
    svgIconName: vi.fn((icon: string) => icon),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'widgets.stufenschalter.defaultOffLabel') return 'Off'
      if (key === 'widgets.stufenschalter.defaultStepLabel') return `Step ${params?.n}`
      return key
    },
  }),
}))

function mountWidget(config: Record<string, unknown>, value: unknown) {
  return mount(StufenschalterWidget, {
    props: {
      config,
      datapointId: 'dp-1',
      value: { id: 'dp-1', v: value, u: null, t: '2026-06-03T00:00:00Z', q: 'good' },
      statusValue: null,
      editorMode: false,
      readonly: false,
    },
  })
}

function legacyStepLabel(n: number): string {
  return `Stufe ${n}`
}

describe('Stufenschalter widget labels', () => {
  it('localizes legacy German default labels without reopening the config', () => {
    const wrapper = mountWidget({
      steps: [
        { label: 'Aus', value: '0', icon: '', color: '#6b7280' },
        { label: legacyStepLabel(1), value: '1', icon: '', color: '#3b82f6' },
        { label: legacyStepLabel(2), value: '2', icon: '', color: '#10b981' },
      ],
    }, 2)

    expect(wrapper.get('[data-testid="stufenschalter-label"]').text()).toBe('Step 2')
  })

  it('derives default step labels from the stored value after reordering', () => {
    const wrapper = mountWidget({
      steps: [
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '1', icon: '', color: '#3b82f6' },
        { label: 'widgets.stufenschalter.defaultOffLabel', value: '0', icon: '', color: '#6b7280' },
      ],
    }, 1)

    expect(wrapper.get('[data-testid="stufenschalter-label"]').text()).toBe('Step 1')
  })
})
