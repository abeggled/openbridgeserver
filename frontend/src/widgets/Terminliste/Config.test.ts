// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import TerminlisteConfig from './Config.vue'
import { MAX_SOURCES, SOURCE_COLORS } from './config'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

const DataPointPickerStub = {
  name: 'DataPointPicker',
  props: ['modelValue', 'compatibleTypes'],
  emits: ['update:modelValue'],
  template: '<div class="dp-picker" :data-value="modelValue ?? \'\'" />',
}

function mountConfig(modelValue: Record<string, unknown>) {
  return mount(TerminlisteConfig, {
    props: { modelValue },
    global: {
      mocks: { $t: (key: string) => key },
      stubs: { DataPointPicker: DataPointPickerStub },
    },
  })
}

function lastEmitted(w: ReturnType<typeof mountConfig>): Record<string, unknown> {
  const events = w.emitted('update:modelValue') ?? []
  return events[events.length - 1][0] as Record<string, unknown>
}

describe('Terminliste Config.vue', () => {
  it('fills defaults for a widget saved without keys', () => {
    const w = mountConfig({})

    expect(w.findAll('[data-testid="terminliste-config-source"]')).toHaveLength(0)
    expect((w.get('[data-testid="terminliste-config-days"]').element as HTMLInputElement).value).toBe('7')
    expect((w.get('[data-testid="terminliste-config-separator"]').element as HTMLSelectElement).value).toBe('semicolon')
    expect((w.get('[data-testid="terminliste-config-date-pattern"]').element as HTMLInputElement).value).toBe('EEE dd.MM.yy')
    expect((w.get('[data-testid="terminliste-config-show-time"]').element as HTMLInputElement).checked).toBe(true)
    expect((w.get('[data-testid="terminliste-config-show-location"]').element as HTMLInputElement).checked).toBe(false)
    expect((w.get('[data-testid="terminliste-config-show-empty-days"]').element as HTMLInputElement).checked).toBe(false)
    expect(w.text()).toContain('widgets.terminliste.sourcesHint')
    expect(w.findAll('option').map((o) => o.text())).toEqual([
      'widgets.terminliste.separatorSemicolon',
      'widgets.terminliste.separatorStar',
      'widgets.terminliste.separatorNewline',
    ])
  })

  it('loads stored values', () => {
    const w = mountConfig({
      label: 'Termine',
      sources: [{ datapoint_id: 'dp-1', label: 'Abfuhr', color: '#123456' }, { datapoint_id: 'dp-2' }, {}],
      days: 14,
      separator: 'newline',
      show_time: false,
      show_location: true,
      show_empty_days: true,
      date_pattern: 'EEEE d.M.',
    })

    expect((w.get('[data-testid="terminliste-config-label"]').element as HTMLInputElement).value).toBe('Termine')
    const pickers = w.findAll('.dp-picker')
    expect(pickers.map((p) => p.attributes('data-value'))).toEqual(['dp-1', 'dp-2', ''])
    const colors = w.findAll('[data-testid="terminliste-config-source-color"]')
    // A source without colour gets the default for its position.
    expect(colors.map((c) => (c.element as HTMLInputElement).value)).toEqual(['#123456', SOURCE_COLORS[1], SOURCE_COLORS[2]])
    const labels = w.findAll('[data-testid="terminliste-config-source-label"]')
    expect(labels.map((l) => (l.element as HTMLInputElement).value)).toEqual(['Abfuhr', '', ''])
    expect((w.get('[data-testid="terminliste-config-separator"]').element as HTMLSelectElement).value).toBe('newline')
    expect((w.get('[data-testid="terminliste-config-show-time"]').element as HTMLInputElement).checked).toBe(false)
    expect((w.get('[data-testid="terminliste-config-show-location"]').element as HTMLInputElement).checked).toBe(true)
    expect((w.get('[data-testid="terminliste-config-show-empty-days"]').element as HTMLInputElement).checked).toBe(true)
    expect((w.get('[data-testid="terminliste-config-date-pattern"]').element as HTMLInputElement).value).toBe('EEEE d.M.')
  })

  it('replaces an unknown separator and caps stored sources', () => {
    const many = Array.from({ length: MAX_SOURCES + 2 }, (_, i) => ({ datapoint_id: `dp-${i}`, label: '', color: '#000000' }))
    const w = mountConfig({ separator: 'pipe', sources: many })

    expect((w.get('[data-testid="terminliste-config-separator"]').element as HTMLSelectElement).value).toBe('semicolon')
    expect(w.findAll('[data-testid="terminliste-config-source"]')).toHaveLength(MAX_SOURCES)
    expect(w.find('[data-testid="terminliste-config-add-source"]').exists()).toBe(false)
  })

  it('adds a source, picks its datapoint and emits only sources with a datapoint', async () => {
    const w = mountConfig({ sources: [] })

    await w.get('[data-testid="terminliste-config-add-source"]').trigger('click')
    expect(w.findAll('[data-testid="terminliste-config-source"]')).toHaveLength(1)
    expect(lastEmitted(w).sources).toEqual([])

    w.findComponent(DataPointPickerStub).vm.$emit('update:modelValue', 'dp-new')
    await nextTick()
    await w.get('[data-testid="terminliste-config-source-label"]').setValue('Privat')

    expect(lastEmitted(w).sources).toEqual([{ datapoint_id: 'dp-new', label: 'Privat', color: SOURCE_COLORS[0] }])

    // Clearing the picker drops the source from the saved config again.
    w.findComponent(DataPointPickerStub).vm.$emit('update:modelValue', null)
    await nextTick()
    expect(lastEmitted(w).sources).toEqual([])
  })

  it('hides the add button once the maximum number of sources is reached', async () => {
    const full = Array.from({ length: MAX_SOURCES - 1 }, (_, i) => ({ datapoint_id: `dp-${i}`, label: '', color: '#000000' }))
    const w = mountConfig({ sources: full })

    await w.get('[data-testid="terminliste-config-add-source"]').trigger('click')

    expect(w.findAll('[data-testid="terminliste-config-source"]')).toHaveLength(MAX_SOURCES)
    // The eighth source gets the eighth default colour.
    const colors = w.findAll('[data-testid="terminliste-config-source-color"]')
    expect((colors[MAX_SOURCES - 1].element as HTMLInputElement).value).toBe(SOURCE_COLORS[MAX_SOURCES - 1])
    expect(w.find('[data-testid="terminliste-config-add-source"]').exists()).toBe(false)
  })

  it('removes a source', async () => {
    const w = mountConfig({
      sources: [
        { datapoint_id: 'dp-1', label: 'Eins', color: '#111111' },
        { datapoint_id: 'dp-2', label: 'Zwei', color: '#222222' },
      ],
    })

    await w.findAll('[data-testid="terminliste-config-remove-source"]')[0].trigger('click')

    expect(lastEmitted(w).sources).toEqual([{ datapoint_id: 'dp-2', label: 'Zwei', color: '#222222' }])
  })

  it('emits clamped days, the chosen options and a default for an empty date pattern', async () => {
    const w = mountConfig({})

    await w.get('[data-testid="terminliste-config-label"]').setValue('Kalender')
    await w.get('[data-testid="terminliste-config-days"]').setValue('999')
    await w.get('[data-testid="terminliste-config-separator"]').setValue('star')
    await w.get('[data-testid="terminliste-config-show-time"]').setValue(false)
    await w.get('[data-testid="terminliste-config-show-location"]').setValue(true)
    await w.get('[data-testid="terminliste-config-show-empty-days"]').setValue(true)
    await w.get('[data-testid="terminliste-config-date-pattern"]').setValue('   ')

    expect(lastEmitted(w)).toEqual({
      label: 'Kalender',
      sources: [],
      days: 366,
      separator: 'star',
      show_time: false,
      show_location: true,
      show_empty_days: true,
      date_pattern: 'EEE dd.MM.yy',
    })

    await w.get('[data-testid="terminliste-config-date-pattern"]').setValue(' EEEE ')
    expect(lastEmitted(w).date_pattern).toBe('EEEE')
  })
})
