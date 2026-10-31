// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useDatapointsStore } from '@/stores/datapoints'
import { useFormatStore } from '@/stores/format'
import type { DataPointValue } from '@/types'
import TerminlisteWidget from './Widget.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

let wrapper: VueWrapper | null = null

function dpValue(id: string, v: unknown): DataPointValue {
  return { id, v, u: null, t: '2026-10-29T08:00:00Z', q: 'good' }
}

function setValue(id: string, v: unknown): void {
  useDatapointsStore().values[id] = dpValue(id, v)
}

function mountWidget(config: Record<string, unknown>) {
  wrapper = mount(TerminlisteWidget, {
    props: { config, datapointId: null, value: null, editorMode: false },
    global: {
      mocks: { $t: (key: string, params?: Record<string, unknown>) => (params ? `${key}:${JSON.stringify(params)}` : key) },
    },
  })
  return wrapper
}

function dayTexts(w: VueWrapper): string[] {
  return w
    .findAll('[data-testid="terminliste-day"]')
    .map((d) => d.findAll(':scope > span').map((s) => s.text()).join(' | '))
}

beforeEach(() => {
  setActivePinia(createPinia())
  // Thursday 2026-10-29, 08:00 in Zurich.
  vi.useFakeTimers({ now: new Date('2026-10-29T07:00:00Z'), toFake: ['Date', 'setInterval', 'clearInterval'] })
  const format = useFormatStore()
  format.timezone = 'Europe/Zurich'
  format.setUiLanguage('de')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

describe('Terminliste widget', () => {
  it('asks for a source when none is configured', () => {
    const w = mountWidget({ sources: [{ datapoint_id: '', label: '', color: '' }, null] })

    expect(w.find('[data-testid="terminliste-no-sources"]').text()).toBe('widgets.terminliste.noSources')
    expect(w.find('[data-testid="terminliste-day"]').exists()).toBe(false)
  })

  it('works without any stored config keys', () => {
    const w = mountWidget({})

    expect(w.find('[data-testid="terminliste-no-sources"]').exists()).toBe(true)
  })

  it('says so when no source has an event in range', () => {
    setValue('dp-a', [['2026-12-24', '', '', 'Weihnachten', '', '']])
    const w = mountWidget({ sources: [{ datapoint_id: 'dp-a', label: 'A', color: '#f00' }], days: 5 })

    expect(w.find('[data-testid="terminliste-empty"]').text()).toBe('widgets.terminliste.noEvents:{"n":5}')
  })

  it('treats a source without a value yet as empty', () => {
    const w = mountWidget({ sources: [{ datapoint_id: 'dp-missing', label: 'A', color: '#f00' }] })

    expect(w.find('[data-testid="terminliste-empty"]').exists()).toBe(true)
  })

  it('lists today, tomorrow and later days, merging sources with their colours', () => {
    setValue('dp-privat', [
      ['2026-10-29', '18:30', '19:30', 'Training', 'Halle', ''],
      ['2026-10-30', '10:00', '11:00', 'Zahnarzt', 'Bern', ''],
    ])
    setValue('dp-muell', JSON.stringify([['2026-10-30', '', '', 'Papier', '', ''], ['2026-11-04', '', '', 'Grüngut', '', '']]))
    const w = mountWidget({
      label: 'Termine',
      sources: [
        { datapoint_id: 'dp-privat', label: 'Privat', color: '#22c55e' },
        { datapoint_id: 'dp-muell', label: 'Abfuhr', color: '#f97316' },
      ],
    })

    expect(w.text()).toContain('Termine')
    expect(dayTexts(w)).toEqual([
      'widgets.terminliste.today | 18:30 Training',
      'widgets.terminliste.tomorrow | Papier; 10:00 Zahnarzt',
      'Mi. 04.11.26 | Grüngut',
    ])
    const tomorrowEvents = w.findAll('[data-testid="terminliste-events"]')[1].findAll('span[title]')
    expect(tomorrowEvents.map((s) => [s.text(), s.attributes('title'), s.attributes('style')])).toEqual([
      ['Papier', 'Abfuhr', 'color: rgb(249, 115, 22);'],
      ['10:00 Zahnarzt', 'Privat', 'color: rgb(34, 197, 94);'],
    ])
    // Only today is emphasised.
    const labels = w.findAll('[data-testid="terminliste-day-label"]')
    expect(labels[0].classes()).toContain('font-semibold')
    expect(labels[1].classes()).not.toContain('font-semibold')
  })

  it('leaves events uncoloured and untitled without colour or label', () => {
    setValue('dp-a', [['2026-10-29', '', '', 'Ohne', '', '']])
    const w = mountWidget({ sources: [{ datapoint_id: 'dp-a' }] })

    const event = w.get('[data-testid="terminliste-events"] span')
    expect(event.text()).toBe('Ohne')
    expect(event.attributes('style')).toBeUndefined()
    expect(event.attributes('title')).toBeUndefined()
  })

  it('applies the display options', () => {
    setValue('dp-a', [
      ['2026-10-29', '09:00', '', 'Arzt', 'Praxis', ''],
      ['2026-10-29', '', '', 'Feiertag', '', ''],
      ['2026-11-02', '', '', 'Später', '', ''],
    ])
    const w = mountWidget({
      sources: [{ datapoint_id: 'dp-a', label: 'A', color: '#000000' }],
      separator: 'star',
      show_time: false,
      show_location: true,
      show_empty_days: true,
      days: 5,
      date_pattern: 'EEEE d.M.',
    })

    expect(dayTexts(w)).toEqual([
      'widgets.terminliste.today | Feiertag * Arzt (Praxis)',
      'widgets.terminliste.tomorrow | widgets.terminliste.noEventsOnDay',
      'Samstag 31.10. | widgets.terminliste.noEventsOnDay',
      'Sonntag 1.11. | widgets.terminliste.noEventsOnDay',
      'Montag 2.11. | Später',
    ])
  })

  it('stacks events below each other with the line-break separator', () => {
    setValue('dp-a', [['2026-10-29', '', '', 'Eins', '', ''], ['2026-10-29', '12:00', '', 'Zwei', '', '']])
    setValue('dp-b', [['2026-10-29', '13:00', '', 'Drei', '', '']])
    const w = mountWidget({
      sources: [{ datapoint_id: 'dp-a', label: 'A', color: '#123456' }, { datapoint_id: 'dp-b' }],
      separator: 'newline',
    })

    const events = w.get('[data-testid="terminliste-events"]')
    expect(events.classes()).toContain('flex-col')
    const spans = events.findAll('span')
    expect(spans.map((s) => s.text())).toEqual(['Eins', '12:00 Zwei', '13:00 Drei'])
    expect(spans.map((s) => [s.attributes('style'), s.attributes('title')])).toEqual([
      ['color: rgb(18, 52, 86);', 'A'],
      ['color: rgb(18, 52, 86);', 'A'],
      [undefined, undefined],
    ])
    expect(events.text()).not.toContain(';')
  })

  it('falls back to the semicolon for an unknown separator and to the default date pattern', () => {
    setValue('dp-a', [['2026-10-29', '', '', 'Eins', '', ''], ['2026-10-29', '', '', 'Zwei', '', ''], ['2026-11-01', '', '', 'Drei', '', '']])
    const w = mountWidget({ sources: [{ datapoint_id: 'dp-a', label: 'A', color: '' }], separator: 'pipe', date_pattern: '' })

    expect(dayTexts(w)).toEqual(['widgets.terminliste.today | Eins; Zwei', 'So. 01.11.26 | Drei'])
  })

  it('moves on to the next day at midnight', async () => {
    setValue('dp-a', [['2026-10-29', '', '', 'Heute', '', ''], ['2026-10-30', '', '', 'Morgen', '', '']])
    const w = mountWidget({ sources: [{ datapoint_id: 'dp-a', label: 'A', color: '' }] })
    expect(dayTexts(w)).toEqual(['widgets.terminliste.today | Heute', 'widgets.terminliste.tomorrow | Morgen'])

    // 23:59 Zurich → one minute later it is Friday.
    vi.setSystemTime(new Date('2026-10-29T22:59:30Z'))
    vi.advanceTimersByTime(60_000)
    await nextTick()

    expect(dayTexts(w)).toEqual(['widgets.terminliste.today | Morgen'])
  })

  it('stops its timer when unmounted', () => {
    const w = mountWidget({})
    expect(vi.getTimerCount()).toBe(1)
    w.unmount()
    wrapper = null
    expect(vi.getTimerCount()).toBe(0)
  })
})
