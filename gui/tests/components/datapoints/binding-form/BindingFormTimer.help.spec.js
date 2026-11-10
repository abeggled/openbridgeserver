/**
 * Integrated help drawer wiring on the Zeitschaltuhr binding form.
 *
 * The section header and the output-value divider each got a HelpButton pointing
 * at a help_id documented in help/{de,en}/adapters/list.md. This spec checks the
 * buttons are present with the right help_id, that clicking one opens the real
 * help store, and that the output-value button follows its own section — a meta
 * binding has no switching value, so it must not offer help for one.
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The help store fetches help-index.json on open(); without this the click tests
// fire a real request at the dev server and log a connection error.
vi.mock('@/api/client', () => ({
  helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
}))
import BindingFormTimer from '@/components/datapoints/binding-form/BindingFormTimer.vue'
import { useHelpStore } from '@/stores/help'

const WIN_EP = () => ({ type: 'fixed', month: 1, day: 1, sign: '+', offset: 0, name: '' })

function mk(cfgOverrides = {}, propOverrides = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(BindingFormTimer, {
    global: { plugins: [pinia] },
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
      ztHolidays:        [],
      ztHolidaysLoading: false,
      ztHolidaysError:   null,
      weekdayShorts:     ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'],
      monthShorts:       ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'],
      winMonths:         [{ v: 1, l: 'Januar' }, { v: 2, l: 'Februar' }],
      winFrom:           WIN_EP(),
      winTo:             WIN_EP(),
      buildWinExpr:      () => '',
      describeWinEp:     () => '',
      ...propOverrides,
    },
  })
}

const helpButton = (wrapper, helpId) => wrapper.find(`[data-testid="help-button-${helpId}"]`)

describe('BindingFormTimer — help buttons', () => {
  it('renders a help button on the section header', () => {
    expect(helpButton(mk(), 'adapters-zeitschaltuhr').exists()).toBe(true)
  })

  it('renders a help button on the output value divider', () => {
    expect(helpButton(mk(), 'adapters-zeitschaltuhr-value').exists()).toBe(true)
  })

  it.each(['adapters-zeitschaltuhr', 'adapters-zeitschaltuhr-value'])(
    'opens the help store with %s when its button is clicked',
    async (helpId) => {
      const wrapper = mk()
      await helpButton(wrapper, helpId).trigger('click')
      const helpStore = useHelpStore()
      expect(helpStore.isOpen).toBe(true)
      expect(helpStore.currentHelpId).toBe(helpId)
    },
  )

  it('offers no output-value help for a meta binding, which has no switching value', () => {
    const wrapper = mk({ timer_type: 'meta', meta_type: 'holiday_today' })
    expect(helpButton(wrapper, 'adapters-zeitschaltuhr-value').exists()).toBe(false)
    // The section itself still applies to a meta binding.
    expect(helpButton(wrapper, 'adapters-zeitschaltuhr').exists()).toBe(true)
  })
})
