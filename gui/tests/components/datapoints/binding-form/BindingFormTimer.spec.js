import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import BindingFormTimer from '@/components/datapoints/binding-form/BindingFormTimer.vue'

const WIN_EP = () => ({ type: 'fixed', month: 1, day: 1, sign: '+', offset: 0, name: '' })

function mk(cfgOverrides = {}, propOverrides = {}) {
  return mount(BindingFormTimer, {
    props: {
      cfg: {
        timer_type:          'daily',
        meta_type:           '',
        weekdays:            [0, 1, 2, 3, 4, 5, 6],
        months:              [],
        day_of_month:        0,
        time_ref:            'absolute',
        hour:                7,
        minute:              0,
        offset_minutes:      0,
        solar_altitude_deg:  0,
        sun_direction:       'rising',
        every_minute:        false,
        every_hour:          false,
        holiday_mode:        'ignore',
        vacation_mode:       'ignore',
        date_window_enabled: false,
        selected_holidays:   [],
        value:               '1',
        ...cfgOverrides,
      },
      ztHolidays:       [],
      ztHolidaysLoading: false,
      ztHolidaysError:  null,
      weekdayShorts:    ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'],
      monthShorts:      ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'],
      winMonths:        [{ v: 1, l: 'Januar' }, { v: 2, l: 'Februar' }],
      winFrom:          WIN_EP(),
      winTo:            WIN_EP(),
      buildWinExpr:     () => '',
      describeWinEp:    () => '',
      ...propOverrides,
    },
  })
}

describe('BindingFormTimer — type select', () => {
  it('renders timer_type select with 4 options', () => {
    const options = mk().find('select').findAll('option')
    const values = options.map(o => o.element.value)
    expect(values).toContain('daily')
    expect(values).toContain('annual')
    expect(values).toContain('holiday')
    expect(values).toContain('meta')
  })

  it('shows meta_type select only for meta timer_type', () => {
    const wDaily = mk({ timer_type: 'daily' })
    const wMeta  = mk({ timer_type: 'meta', meta_type: 'holiday_today' })
    // daily has many selects (time_ref, holiday/vacation modes); meta only has type + meta_type
    expect(wDaily.findAll('select').length).toBeGreaterThan(wMeta.findAll('select').length)
  })
})

describe('BindingFormTimer — weekday buttons', () => {
  it('renders 7 weekday buttons for daily type', () => {
    const w = mk({ timer_type: 'daily' })
    const wdBtns = w.findAll('button').filter(b => ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'].includes(b.text()))
    expect(wdBtns.length).toBe(7)
  })

  it('weekday buttons not shown for holiday type', () => {
    const w = mk({ timer_type: 'holiday' })
    const wdBtns = w.findAll('button').filter(b => ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'].includes(b.text()))
    expect(wdBtns.length).toBe(0)
  })

  it('emits zt-toggle-weekday on weekday button click', async () => {
    const w = mk({ timer_type: 'daily' })
    const mondayBtn = w.findAll('button').find(b => b.text() === 'Mo')
    await mondayBtn.trigger('click')
    expect(w.emitted('zt-toggle-weekday')).toBeTruthy()
    expect(w.emitted('zt-toggle-weekday')[0][0]).toBe(0)
  })
})

describe('BindingFormTimer — annual type', () => {
  it('shows month buttons for annual type', () => {
    const w = mk({ timer_type: 'annual' })
    const monthBtns = w.findAll('button').filter(b => ['Jan', 'Feb', 'Mär'].includes(b.text()))
    expect(monthBtns.length).toBeGreaterThan(0)
  })

  it('emits zt-toggle-month on month button click', async () => {
    const w = mk({ timer_type: 'annual' })
    const janBtn = w.findAll('button').find(b => b.text() === 'Jan')
    await janBtn.trigger('click')
    expect(w.emitted('zt-toggle-month')).toBeTruthy()
  })

  it('shows day_of_month input for annual type', () => {
    const w = mk({ timer_type: 'annual' })
    const dayInput = w.findAll('input[type="number"]').find(i => i.attributes('max') === '31')
    expect(dayInput).toBeTruthy()
  })
})

describe('BindingFormTimer — time_ref', () => {
  it('shows hour/minute inputs for absolute time_ref', () => {
    const w = mk({ time_ref: 'absolute' })
    const hourInput = w.findAll('input[type="number"]').find(i => i.attributes('max') === '23')
    expect(hourInput).toBeTruthy()
  })

  it('hides hour/minute inputs for sunrise time_ref', () => {
    const w = mk({ time_ref: 'sunrise' })
    const hourInput = w.findAll('input[type="number"]').find(i => i.attributes('max') === '23')
    expect(hourInput).toBeFalsy()
  })

  it('shows offset_minutes input for non-absolute time_ref', () => {
    const w = mk({ time_ref: 'sunrise' })
    const offsetInput = w.findAll('input[type="number"]').find(i => i.attributes('placeholder') === '0')
    expect(offsetInput).toBeTruthy()
  })

  it('shows solar_altitude_deg input for solar_altitude time_ref', () => {
    const w = mk({ time_ref: 'solar_altitude' })
    const altInput = w.findAll('input[type="number"]').find(i => i.attributes('min') === '-18')
    expect(altInput).toBeTruthy()
  })
})

describe('BindingFormTimer — holiday type', () => {
  it('shows loading state when ztHolidaysLoading', () => {
    const w = mk({ timer_type: 'holiday' }, { ztHolidaysLoading: true })
    expect(w.html()).toContain('Lade') // German "Lade Feiertage …"
  })

  it('shows error when ztHolidaysError', () => {
    const w = mk({ timer_type: 'holiday' }, { ztHolidaysError: 'Fehler beim Laden' })
    expect(w.text()).toContain('Fehler beim Laden')
  })

  it('renders holiday checkboxes when ztHolidays provided', () => {
    const w = mk({ timer_type: 'holiday' }, {
      ztHolidays: [{ name: 'Weihnachten', date: '25.12.' }],
    })
    expect(w.text()).toContain('Weihnachten')
    expect(w.find('input[type="checkbox"]').exists()).toBe(true)
  })

  it('emits zt-toggle-holiday on checkbox change', async () => {
    const w = mk({ timer_type: 'holiday' }, {
      ztHolidays: [{ name: 'Weihnachten', date: '25.12.' }],
    })
    await w.find('input[type="checkbox"]').trigger('change')
    expect(w.emitted('zt-toggle-holiday')).toBeTruthy()
  })

  it('emits load-zsu-holidays on reload button click', async () => {
    const w = mk({ timer_type: 'holiday' })
    const reloadBtn = w.findAll('button').find(b => b.text().includes('Neu laden') || b.text().includes('Reload') || b.text().includes('Neulade'))
    if (reloadBtn) {
      await reloadBtn.trigger('click')
      expect(w.emitted('load-zsu-holidays')).toBeTruthy()
    }
  })
})

describe('BindingFormTimer — tick options', () => {
  it('shows every_minute checkbox', () => {
    const w = mk()
    expect(w.find('#zt_every_minute').exists()).toBe(true)
  })

  it('shows at_minute input when every_hour enabled', () => {
    const w = mk({ every_hour: true, every_minute: false })
    const minuteAtHour = w.findAll('input[type="number"]').find(i => i.attributes('max') === '59' && i.attributes('min') === '0')
    expect(minuteAtHour).toBeTruthy()
  })
})

describe('BindingFormTimer — date window', () => {
  it('date window fields hidden by default', () => {
    const w = mk({ date_window_enabled: false })
    expect(w.find('#zt_date_window').element.checked).toBe(false)
    // Fixed type selects (winFrom/winTo) should not be rendered
    const typeSelects = w.findAll('select').filter(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('fixed') && opts.includes('easter')
    })
    expect(typeSelects.length).toBe(0)
  })

  it('date window fields shown when date_window_enabled', () => {
    const w = mk({ date_window_enabled: true })
    const typeSelects = w.findAll('select').filter(s => {
      const opts = s.findAll('option').map(o => o.element.value)
      return opts.includes('fixed') && opts.includes('easter')
    })
    expect(typeSelects.length).toBeGreaterThan(0)
  })
})

describe('BindingFormTimer — meta type hides non-meta controls', () => {
  it('hides weekday buttons for meta type', () => {
    const w = mk({ timer_type: 'meta', meta_type: 'holiday_today' })
    const wdBtns = w.findAll('button').filter(b => ['Mo', 'Di', 'Mi'].includes(b.text()))
    expect(wdBtns.length).toBe(0)
  })

  it('hides output value input for meta type', () => {
    const w = mk({ timer_type: 'meta', meta_type: 'holiday_today' })
    const valueInput = w.findAll('input').find(i => i.attributes('placeholder') === '1')
    expect(valueInput).toBeFalsy()
  })
})

// ---------------------------------------------------------------------------
// Ausgabewert — typgerechtes Eingabefeld (Issue #1008)
// ---------------------------------------------------------------------------

describe('BindingFormTimer — typed output value input', () => {
  it('renders a plain text field for STRING and UNKNOWN', () => {
    for (const dataType of ['STRING', 'UNKNOWN']) {
      const w = mk({ value: 'on' }, { dpDataType: dataType })
      expect(w.find('[data-testid="zt-value-text"]').exists()).toBe(true)
      expect(w.find('[data-testid="zt-value-number"]').exists()).toBe(false)
      expect(w.find('[data-testid="zt-value-error"]').exists()).toBe(false)
    }
  })

  it('renders an Ein/Aus select for BOOLEAN', () => {
    const w = mk({ value: '1' }, { dpDataType: 'BOOLEAN' })
    const select = w.find('[data-testid="zt-value-boolean"]')
    expect(select.exists()).toBe(true)
    expect(select.findAll('option')).toHaveLength(2)
  })

  it('maps stored boolean literals onto the select and writes back true/false', async () => {
    const cfg = {
      timer_type: 'daily', meta_type: '', weekdays: [0], months: [], day_of_month: 0,
      time_ref: 'absolute', hour: 7, minute: 0, offset_minutes: 0, solar_altitude_deg: 0,
      sun_direction: 'rising', every_minute: false, every_hour: false,
      holiday_mode: 'ignore', vacation_mode: 'ignore', date_window_enabled: false,
      selected_holidays: [], value: 'ein',
    }
    const w = mount(BindingFormTimer, {
      props: {
        cfg,
        ztHolidays: [], ztHolidaysLoading: false, ztHolidaysError: null,
        weekdayShorts: ['Mo'], monthShorts: ['Jan'], winMonths: [{ v: 1, l: 'Januar' }],
        winFrom: WIN_EP(), winTo: WIN_EP(), buildWinExpr: () => '', describeWinEp: () => '',
        dpDataType: 'BOOLEAN',
      },
    })
    const select = w.find('[data-testid="zt-value-boolean"]')
    expect(select.element.value).toBe('true')

    await select.setValue('false')
    expect(cfg.value).toBe('false')
  })

  it('renders a number field with step=1 for INTEGER', () => {
    const w = mk({ value: '50' }, { dpDataType: 'INTEGER' })
    const input = w.find('[data-testid="zt-value-number"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('step')).toBe('1')
  })

  it('renders a number field with step=any for FLOAT', () => {
    const input = mk({ value: '21.5' }, { dpDataType: 'FLOAT' }).find('[data-testid="zt-value-number"]')
    expect(input.attributes('step')).toBe('any')
  })

  it.each([
    ['DATE', 'zt-value-date'],
    ['TIME', 'zt-value-time'],
    ['DATETIME', 'zt-value-datetime'],
  ])('renders a picker for %s', (dataType, testid) => {
    const w = mk({ value: '' }, { dpDataType: dataType })
    expect(w.find(`[data-testid="${testid}"]`).exists()).toBe(true)
  })

  // Codex review on PR #1155: a native `time`/`datetime-local` control cannot hold a
  // UTC offset (or fractional seconds, or a bare date used as a datetime). The browser
  // sanitizes it to an empty field, so the stored value would look unset and be wiped
  // on the next save — those legacy literals get a text field instead.
  it.each([
    ['TIME', '08:00:00+02:00', 'zt-value-time'],
    ['TIME', '08:00:00Z', 'zt-value-time'],
    ['TIME', '08:00:00.5', 'zt-value-time'],
    ['DATETIME', '2026-12-24T08:00:00+02:00', 'zt-value-datetime'],
    ['DATETIME', '2026-12-24', 'zt-value-datetime'],
  ])('falls back to a text field for the %s value %s', (dataType, value, pickerTestid) => {
    const w = mk({ value }, { dpDataType: dataType })
    expect(w.find(`[data-testid="${pickerTestid}"]`).exists()).toBe(false)
    const text = w.find('[data-testid="zt-value-text"]')
    expect(text.exists()).toBe(true)
    expect(text.element.value).toBe(value)
  })

  it.each([
    ['TIME', '08:00:00', 'zt-value-time'],
    ['DATETIME', '2026-12-24T08:00:00', 'zt-value-datetime'],
  ])('keeps the native picker for the representable %s value %s', (dataType, value, pickerTestid) => {
    const w = mk({ value }, { dpDataType: dataType })
    expect(w.find(`[data-testid="${pickerTestid}"]`).exists()).toBe(true)
  })

  it('does not swap the control while the value is being edited', async () => {
    // Deleting the offset must not turn the text field into a picker mid-typing.
    const w = mk({ value: '08:00:00+02:00' }, { dpDataType: 'TIME' })
    await w.find('[data-testid="zt-value-text"]').setValue('08:00:00')
    expect(w.find('[data-testid="zt-value-text"]').exists()).toBe(true)
    expect(w.find('[data-testid="zt-value-time"]').exists()).toBe(false)
  })

  it('re-evaluates the control when the target object type changes', async () => {
    const w = mk({ value: '08:00:00+02:00' }, { dpDataType: 'TIME' })
    expect(w.find('[data-testid="zt-value-text"]').exists()).toBe(true)
    await w.setProps({ dpDataType: 'STRING' })
    await w.setProps({ dpDataType: 'TIME' })
    expect(w.find('[data-testid="zt-value-text"]').exists()).toBe(true)
  })

  it('shows the data type next to the label, with the unit when present', () => {
    expect(mk({}, { dpDataType: 'FLOAT' }).find('[data-testid="zt-value-type"]').text()).toBe('FLOAT')
    expect(mk({}, { dpDataType: 'FLOAT', dpUnit: '%' }).find('[data-testid="zt-value-type"]').text()).toBe('FLOAT · %')
  })

  it('defaults to UNKNOWN when no data type is passed', () => {
    expect(mk().find('[data-testid="zt-value-type"]').text()).toBe('UNKNOWN')
  })

  it('shows an inline error when the value does not fit the type', () => {
    // DATE keeps the stored value — only BOOLEAN is auto-repaired, because its
    // select cannot represent an arbitrary literal.
    const w = mk({ value: '1' }, { dpDataType: 'DATE' })
    const err = w.find('[data-testid="zt-value-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text().length).toBeGreaterThan(0)
    expect(err.text()).not.toContain('adapters.bindingForm')
  })

  it('shows no error for a valid value', () => {
    expect(mk({ value: '50' }, { dpDataType: 'FLOAT' }).find('[data-testid="zt-value-error"]').exists()).toBe(false)
  })

  it('renders a type-specific hint', () => {
    const w = mk({ value: '50' }, { dpDataType: 'FLOAT' })
    const hints = w.findAll('.hint').map(h => h.text())
    expect(hints.some(h => h.includes('21.5'))).toBe(true)
  })

  it('hides the output value section for meta bindings', () => {
    const w = mk({ timer_type: 'meta' }, { dpDataType: 'FLOAT' })
    expect(w.find('[data-testid="zt-value-number"]').exists()).toBe(false)
    expect(w.find('[data-testid="zt-value-text"]').exists()).toBe(false)
  })

  // A BOOLEAN select offers two options, so a legacy literal like '50' can never be
  // shown: the select already reads "Aus" while the stored value stays invalid and
  // blocks saving. The watch normalizes it once onto what is displayed.
  it.each([
    ['50', 'false'],
    ['morgens', 'false'],
    ['2026-12-24', 'false'],
  ])('normalizes the stored value %s a BOOLEAN select cannot show to %s', (stored, expected) => {
    const cfg = { ...mk().props('cfg'), value: stored }
    mount(BindingFormTimer, {
      props: {
        cfg,
        ztHolidays: [], ztHolidaysLoading: false, ztHolidaysError: null,
        weekdayShorts: ['Mo'], monthShorts: ['Jan'], winMonths: [{ v: 1, l: 'Januar' }],
        winFrom: WIN_EP(), winTo: WIN_EP(), buildWinExpr: () => '', describeWinEp: () => '',
        dpDataType: 'BOOLEAN',
      },
    })
    expect(cfg.value).toBe(expected)
  })

  it('leaves a value the BOOLEAN select can already show untouched', () => {
    const cfg = { ...mk().props('cfg'), value: 'ein' }
    mount(BindingFormTimer, {
      props: {
        cfg,
        ztHolidays: [], ztHolidaysLoading: false, ztHolidaysError: null,
        weekdayShorts: ['Mo'], monthShorts: ['Jan'], winMonths: [{ v: 1, l: 'Januar' }],
        winFrom: WIN_EP(), winTo: WIN_EP(), buildWinExpr: () => '', describeWinEp: () => '',
        dpDataType: 'BOOLEAN',
      },
    })
    expect(cfg.value).toBe('ein')
  })
})

describe('BindingFormTimer — switching value stays a string', () => {
  function mkCfg(overrides = {}) {
    return {
      timer_type: 'daily', meta_type: '', weekdays: [0], months: [], day_of_month: 0,
      time_ref: 'absolute', hour: 7, minute: 0, offset_minutes: 0, solar_altitude_deg: 0,
      sun_direction: 'rising', every_minute: false, every_hour: false,
      holiday_mode: 'ignore', vacation_mode: 'ignore', date_window_enabled: false,
      selected_holidays: [], value: '1', ...overrides,
    }
  }

  function mkWith(cfg, dpDataType) {
    return mount(BindingFormTimer, {
      props: {
        cfg,
        ztHolidays: [], ztHolidaysLoading: false, ztHolidaysError: null,
        weekdayShorts: ['Mo'], monthShorts: ['Jan'], winMonths: [{ v: 1, l: 'Januar' }],
        winFrom: WIN_EP(), winTo: WIN_EP(), buildWinExpr: () => '', describeWinEp: () => '',
        dpDataType,
      },
    })
  }

  it('writes true when the boolean select is switched on', async () => {
    const cfg = mkCfg({ value: 'aus' })
    const w = mkWith(cfg, 'BOOLEAN')
    expect(w.find('[data-testid="zt-value-boolean"]').element.value).toBe('false')
    await w.find('[data-testid="zt-value-boolean"]').setValue('true')
    expect(cfg.value).toBe('true')
  })

  it('renders an empty field when the stored value is missing', () => {
    const w = mkWith(mkCfg({ value: undefined }), 'FLOAT')
    expect(w.find('[data-testid="zt-value-number"]').element.value).toBe('')
  })

  it.each([
    ['DATE', 'zt-value-date', '2026-12-24'],
    // The pickers hand back what the control itself holds — jsdom drops the
    // seconds an empty `step=1` field never collected.
    ['TIME', 'zt-value-time', '08:00'],
    ['DATETIME', 'zt-value-datetime', '2026-12-24T08:00'],
    ['STRING', 'zt-value-text', 'Guten Morgen'],
  ])('writes what the %s input emits back into the config as a string', async (dataType, testid, typed) => {
    const cfg = mkCfg({ value: '' })
    const w = mkWith(cfg, dataType)
    await w.find(`[data-testid="${testid}"]`).setValue(typed)
    expect(cfg.value).toBe(typed)
    expect(typeof cfg.value).toBe('string')
  })

  it('stores a typed number as a string, never as a Number', async () => {
    const cfg = mkCfg({ value: '1' })
    const w = mkWith(cfg, 'FLOAT')
    await w.find('[data-testid="zt-value-number"]').setValue('0')
    expect(cfg.value).toBe('0')
    expect(typeof cfg.value).toBe('string')
  })

  it('normalises a nullish assignment to an empty string', () => {
    const cfg = mkCfg({ value: '1' })
    const w = mkWith(cfg, 'FLOAT')
    w.vm.textValue = null
    expect(cfg.value).toBe('')
  })

  it('repairs a legacy non-boolean value on a BOOLEAN object', async () => {
    // The select only offers Ein/Aus, so "50" could never be cleared by the
    // user — it must be normalised to what the select already displays.
    const cfg = mkCfg({ value: '50' })
    const w = mkWith(cfg, 'BOOLEAN')
    await nextTick()
    expect(cfg.value).toBe('false')
    expect(w.find('[data-testid="zt-value-error"]').exists()).toBe(false)
  })

  it('leaves a valid boolean literal untouched', async () => {
    const cfg = mkCfg({ value: 'ein' })
    mkWith(cfg, 'BOOLEAN')
    await nextTick()
    expect(cfg.value).toBe('ein')
  })

  it('does not repair values for non-boolean objects', async () => {
    const cfg = mkCfg({ value: '1' })
    const w = mkWith(cfg, 'DATE')
    await nextTick()
    expect(cfg.value).toBe('1')
    expect(w.find('[data-testid="zt-value-error"]').exists()).toBe(true)
  })

  it('repairs the value when the object type is loaded after mount', async () => {
    const cfg = mkCfg({ value: '50' })
    const w = mkWith(cfg, 'UNKNOWN')
    expect(cfg.value).toBe('50')
    await w.setProps({ dpDataType: 'BOOLEAN' })
    await nextTick()
    expect(cfg.value).toBe('false')
  })
})
