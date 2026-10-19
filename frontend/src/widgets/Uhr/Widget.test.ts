// @vitest-environment jsdom
/**
 * Uhr widget date line (issue #1073).
 *
 * The date follows the administrator-configured `date_format` pattern, renders
 * in the widget's own timezone so it matches the clock hands, and takes its
 * weekday/month names from the UI language rather than the regional format.
 * The *time* stays widget-owned — its `showSeconds` option governs it.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFormatStore } from '@/stores/format'
import UhrWidget from './Widget.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ locale: { value: 'de' }, t: (key: string) => key }) }))

let wrapper: VueWrapper | null = null

function mountClock(config: Record<string, unknown>) {
  wrapper = mount(UhrWidget, {
    props: { config: { mode: 'digital', showDate: true, ...config }, datapointId: null, value: null, statusValue: null, editorMode: false },
  })
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-08-20T12:00:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
})

describe('Uhr Widget.vue — date line (#1073)', () => {
  it('applies the administrator-configured date pattern', () => {
    const store = useFormatStore()
    store.dateFormat = 'yyyy/MM/dd'
    store.timezone = 'UTC'

    expect(mountClock({}).text()).toContain('2026/08/20')
  })

  it('renders the default pattern when nothing is configured', () => {
    useFormatStore().timezone = 'UTC'

    expect(mountClock({}).text()).toContain('20.08.2026')
  })

  it('keeps weekday and month names in the UI language, not the region', () => {
    const store = useFormatStore()
    store.dateFormat = 'EEEE, d. MMMM yyyy'
    store.timezone = 'UTC'
    store.regionFormatSetting = 'en-US'

    expect(mountClock({}).text()).toContain('Donnerstag, 20. August 2026')
  })

  it("uses the widget's own timezone so the date matches the hands", () => {
    const store = useFormatStore()
    store.dateFormat = 'dd.MM.yyyy'
    store.timezone = 'UTC'

    // 02:00 UTC on the 20th is still 19:00 on the 19th in Los Angeles (UTC-7).
    vi.setSystemTime(new Date('2026-08-20T02:00:00Z'))

    const text = mountClock({ timezone: 'America/Los_Angeles' }).text()

    // Date *and* time must come from the same zone — showing the widget's date
    // next to browser-local time would be worse than either alone.
    expect(text).toContain('19.08.2026')
    expect(text).toContain('19:00')
  })

  it('falls back to the installation timezone when the widget sets none', () => {
    const store = useFormatStore()
    store.dateFormat = 'dd.MM.yyyy'
    store.timezone = 'Asia/Tokyo'
    vi.setSystemTime(new Date('2026-08-20T02:00:00Z'))

    // 02:00Z is 11:00 on the 20th in Tokyo — date and time must agree.
    const text = mountClock({}).text()
    expect(text).toContain('11:00')
    expect(text).toContain('20.08.2026')
  })

  it('falls back to the browser zone when neither widget nor server sets one', () => {
    const store = useFormatStore()
    store.dateFormat = 'dd.MM.yyyy'
    store.timezone = null
    vi.setSystemTime(new Date('2026-08-20T02:00:00Z'))

    const local = new Date('2026-08-20T02:00:00Z')
    const text = mountClock({}).text()
    expect(text).toContain(`${String(local.getHours()).padStart(2, '0')}:00`)
    expect(text).toContain(
      `${String(local.getDate()).padStart(2, '0')}.${String(local.getMonth() + 1).padStart(2, '0')}.${local.getFullYear()}`,
    )
  })

  it.each([
    ['widget zone wins over the server zone', 'America/Los_Angeles', 'Asia/Tokyo'],
    ['server zone when the widget sets none', '', 'America/Los_Angeles'],
    ['browser zone when neither sets one', '', null],
  ])('renders date and time from one and the same zone — %s', (_name, widgetZone, serverZone) => {
    const store = useFormatStore()
    store.dateFormat = 'dd.MM.yyyy'
    store.timezone = serverZone
    const instant = new Date('2026-08-20T02:00:00Z')
    vi.setSystemTime(instant)

    const text = mountClock({ timezone: widgetZone, showSeconds: false }).text()

    // Derive the expectation independently from the zone that should win, so a
    // date/time split cannot slip through.
    const zone = widgetZone || serverZone || undefined
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(instant).reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {})

    expect(text).toContain(`${parts.hour}:${parts.minute}`)
    expect(text).toContain(`${parts.day}.${parts.month}.${parts.year}`)
  })

  it('omits the date entirely when showDate is off', () => {
    useFormatStore().timezone = 'UTC'

    expect(mountClock({ showDate: false }).text()).not.toContain('2026')
  })
})

describe('Uhr Widget.vue — Wortuhr minute dots', () => {
  const COLOR = 'rgb(255, 0, 0)'

  function mountWordClock(hours: number, minutes: number, config: Record<string, unknown> = {}) {
    // The word clock reads local time; build the instant in local time too.
    vi.setSystemTime(new Date(2026, 7, 20, hours, minutes, 0))
    return mountClock({ mode: 'wortuhr', showDate: false, color: COLOR, ...config })
  }

  /** Lit letters, row by row, joined with spaces between the rows that have any. */
  function litLetters(w: VueWrapper): string {
    const cells = w.findAll('[data-testid="uhr-wortuhr-cell"]')
    expect(cells).toHaveLength(110)
    const rows: string[] = []
    for (let r = 0; r < 10; r++) {
      const row = cells.slice(r * 11, r * 11 + 11)
        .filter(c => (c.attributes('style') ?? '').includes(`color: ${COLOR}`))
        .map(c => c.text())
        .join('')
      if (row) rows.push(row)
    }
    return rows.join(' ')
  }

  function activeDots(w: VueWrapper): boolean[] {
    return [1, 2, 3, 4].map(n => w.get(`[data-testid="uhr-wortuhr-dot-${n}"]`).attributes('data-active') === 'true')
  }

  it('renders no dots and keeps rounding to the nearest five minutes by default', () => {
    const w = mountWordClock(17, 48)

    expect(w.find('[data-testid="uhr-wortuhr-dot-1"]').exists()).toBe(false)
    // 17:48 rounds up to 17:50 → "es ist zehn vor sechs"
    expect(litLetters(w)).toBe('ESIST ZEHN VOR SECHS')
  })

  it('rounds the words down and lights two dots at 17:47', () => {
    const w = mountWordClock(17, 47, { minuteDots: true })

    expect(litLetters(w)).toBe('ESIST DREIVIERTEL SECHS')
    expect(activeDots(w)).toEqual([true, true, false, false])
  })

  it('lights three dots at 17:48 instead of rounding up to "zehn vor"', () => {
    const w = mountWordClock(17, 48, { minuteDots: true })

    expect(litLetters(w)).toBe('ESIST DREIVIERTEL SECHS')
    expect(activeDots(w)).toEqual([true, true, true, false])
  })

  it('lights all four dots at 17:59 without jumping to the next full hour', () => {
    const w = mountWordClock(17, 59, { minuteDots: true })

    expect(litLetters(w)).toBe('ESISTFÜNF VOR SECHS')
    expect(activeDots(w)).toEqual([true, true, true, true])
  })

  it('shows the full hour with no dot lit at 18:00', () => {
    const w = mountWordClock(18, 0, { minuteDots: true })

    expect(litLetters(w)).toBe('ESIST SECHSUHR')
    expect(activeDots(w)).toEqual([false, false, false, false])
  })

  it('paints lit dots in the accent colour and unlit dots grey', () => {
    const w = mountWordClock(17, 46, { minuteDots: true })

    const lit = w.get('[data-testid="uhr-wortuhr-dot-1"]')
    const unlit = w.get('[data-testid="uhr-wortuhr-dot-2"]')
    expect(lit.attributes('style')).toContain(`background-color: ${COLOR}`)
    expect(lit.classes()).not.toContain('bg-gray-300')
    expect(unlit.attributes('style') ?? '').not.toContain('background-color')
    expect(unlit.classes()).toContain('bg-gray-300')
  })

  it('places the dots clockwise from the top-left corner', () => {
    const w = mountWordClock(17, 49, { minuteDots: true })

    expect(w.get('[data-testid="uhr-wortuhr-dot-1"]').classes()).toEqual(expect.arrayContaining(['top-0', 'left-0']))
    expect(w.get('[data-testid="uhr-wortuhr-dot-2"]').classes()).toEqual(expect.arrayContaining(['top-0', 'right-0']))
    expect(w.get('[data-testid="uhr-wortuhr-dot-3"]').classes()).toEqual(expect.arrayContaining(['bottom-0', 'right-0']))
    expect(w.get('[data-testid="uhr-wortuhr-dot-4"]').classes()).toEqual(expect.arrayContaining(['bottom-0', 'left-0']))
  })
})
