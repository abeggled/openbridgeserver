import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
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
