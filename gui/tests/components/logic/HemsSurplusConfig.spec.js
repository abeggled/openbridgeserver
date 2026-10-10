import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The real VueDraggable needs a browser drag session; the stub lets a test
// deliver the reordered list exactly like vue-draggable-plus does.
vi.mock('vue-draggable-plus', () => ({
  VueDraggable: {
    name: 'VueDraggableStub',
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: '<div data-testid="draggable-stub"><slot /></div>',
  },
}))

import HemsSurplusConfig from '@/components/logic/HemsSurplusConfig.vue'

const C = (id, extra = {}) => ({ id, name: id.toUpperCase(), active: true, mode: 'percent', power_w: 1000, ...extra })

function mountCfg(data = {}, description = 'Beschreibung') {
  return mount(HemsSurplusConfig, { props: { data, description } })
}

function lastData(w) {
  return w.emitted('update').at(-1)[0]
}

function lastConsumers(w) {
  return JSON.parse(lastData(w).consumers)
}

async function setValue(w, testid, value, event = 'change') {
  const el = w.find(`[data-testid="${testid}"]`)
  el.element.value = value
  await el.trigger(event)
}

describe('HemsSurplusConfig — grid section', () => {
  it('shows the description and the hint of the selected measurement mode', () => {
    const w = mountCfg({})
    expect(w.text()).toContain('Beschreibung')
    expect(w.find('[data-testid="hems-grid-mode"]').element.value).toBe('bidirectional')
    expect(w.find('[data-testid="hems-grid-mode-hint"]').text()).toContain('negativer Wert')
    expect(w.find('[data-testid="hems-feed-in-warning"]').exists()).toBe(false)
  })

  it('treats an unknown grid mode as bidirectional', () => {
    const w = mountCfg({ grid_mode: 'bogus' })
    expect(w.find('[data-testid="hems-grid-mode"]').element.value).toBe('bidirectional')
  })

  it('documents the 0 W limitation when only the feed-in power is measured', () => {
    const w = mountCfg({ grid_mode: 'feed_in_only' })
    expect(w.find('[data-testid="hems-feed-in-warning"]').text()).toContain('0 W')
  })

  it('switches the measurement mode and keeps the other settings', async () => {
    const w = mountCfg({ interval_s: 45, consumers: '[]' })
    await setValue(w, 'hems-grid-mode', 'split')
    expect(lastData(w)).toMatchObject({ grid_mode: 'split', interval_s: 45 })
  })

  it('shows the issue defaults and the stored values', () => {
    const defaults = mountCfg({})
    expect(defaults.find('[data-testid="hems-global-interval_s"]').element.value).toBe('30')
    expect(defaults.find('[data-testid="hems-global-target_w"]').element.value).toBe('0')
    expect(defaults.find('[data-testid="hems-global-on_delay_s"]').element.value).toBe('60')
    expect(defaults.find('[data-testid="hems-global-off_delay_s"]').element.value).toBe('180')
    expect(defaults.find('[data-testid="hems-global-only_on_change"]').element.checked).toBe(true)
    expect(defaults.find('[data-testid="hems-global-invalid_behavior"]').element.value).toBe('off')

    const stored = mountCfg({ interval_s: 120, only_on_change: false })
    expect(stored.find('[data-testid="hems-global-interval_s"]').element.value).toBe('120')
    expect(stored.find('[data-testid="hems-global-only_on_change"]').element.checked).toBe(false)
    expect(mountCfg({ only_on_change: 'false' }).find('[data-testid="hems-global-only_on_change"]').element.checked).toBe(false)
  })

  it('clamps the control interval to 5 s … 60 min', async () => {
    const w = mountCfg({})
    await setValue(w, 'hems-global-interval_s', '1')
    expect(lastData(w).interval_s).toBe(5)
    await setValue(w, 'hems-global-interval_s', '99999')
    expect(lastData(w).interval_s).toBe(3600)
    await setValue(w, 'hems-global-interval_s', '')
    expect(lastData(w).interval_s).toBe(30)
    await setValue(w, 'hems-global-on_delay_s', '-5')
    expect(lastData(w).on_delay_s).toBe(0)
    await setValue(w, 'hems-global-target_w', '250')
    expect(lastData(w).target_w).toBe(250)
  })

  it('stores the select and checkbox settings', async () => {
    const w = mountCfg({})
    await setValue(w, 'hems-global-invalid_behavior', 'hold')
    expect(lastData(w).invalid_behavior).toBe('hold')
    await w.find('[data-testid="hems-global-only_on_change"]').setValue(false)
    expect(lastData(w).only_on_change).toBe(false)
  })
})

describe('HemsSurplusConfig — consumer list', () => {
  beforeEach(() => vi.spyOn(Math, 'random').mockRestore())

  it('shows an empty state and appends new consumers at the end (lowest priority)', async () => {
    const w = mountCfg({})
    expect(w.find('[data-testid="hems-consumers-empty"]').exists()).toBe(true)
    await w.find('[data-testid="hems-consumer-add"]').trigger('click')
    const first = lastConsumers(w)
    expect(first).toHaveLength(1)
    expect(first[0]).toMatchObject({ name: '', active: true, mode: 'percent', power_w: 1000 })
    expect(first[0].id).toMatch(/^[a-z][a-z0-9]*$/)

    const w2 = mountCfg({ consumers: JSON.stringify([C('a'), C('b')]) })
    await w2.find('[data-testid="hems-consumer-add"]').trigger('click')
    expect(lastConsumers(w2).map(c => c.id).slice(0, 2)).toEqual(['a', 'b'])
    expect(lastConsumers(w2)).toHaveLength(3)
  })

  it('never reuses an existing id or one that starts with a digit', async () => {
    const random = vi.spyOn(Math, 'random')
    random.mockReturnValueOnce(0.123456789) // digit first → rejected
    random.mockReturnValueOnce(0.5)         // "i" … → taken below
    const taken = (0.5).toString(36).slice(2, 8)
    random.mockReturnValue(0.75)
    const w = mountCfg({ consumers: JSON.stringify([C(taken)]) })
    await w.find('[data-testid="hems-consumer-add"]').trigger('click')
    const ids = lastConsumers(w).map(c => c.id)
    expect(new Set(ids).size).toBe(2)
    expect(ids[1]).toBe((0.75).toString(36).slice(2, 8))
    random.mockRestore()
  })

  it('numbers the consumers by position and offers no priority input', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a'), C('b'), C('c')]) })
    expect([0, 1, 2].map(i => w.find(`[data-testid="hems-consumer-position-${i}"]`).text())).toEqual(['1', '2', '3'])
    expect(w.find('[data-testid="hems-consumer-priority-0"]').exists()).toBe(false)
    expect(w.find('[data-testid="hems-consumer-handle-0"]').exists()).toBe(true)
  })

  it('reorders by drag and drop and keeps every consumer id', async () => {
    const a = C('a')
    const b = C('b')
    const w = mountCfg({ consumers: JSON.stringify([a, b]) })
    await w.findComponent({ name: 'VueDraggableStub' }).vm.$emit('update:modelValue', [b, a])
    expect(lastConsumers(w).map(c => c.id)).toEqual(['b', 'a'])
  })

  it('removes a consumer', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a'), C('b')]) })
    await w.find('[data-testid="hems-consumer-remove-0"]').trigger('click')
    expect(lastConsumers(w).map(c => c.id)).toEqual(['b'])
  })

  it('accepts the list as an array or a JSON string and ignores unusable storage', () => {
    expect(mountCfg({ consumers: [C('a')] }).find('[data-testid="hems-consumer-0"]').exists()).toBe(true)
    expect(mountCfg({ consumers: '' }).find('[data-testid="hems-consumer-0"]').exists()).toBe(false)
    expect(mountCfg({ consumers: 'not json' }).find('[data-testid="hems-consumer-0"]').exists()).toBe(false)
    expect(mountCfg({ consumers: '{"a":1}' }).find('[data-testid="hems-consumer-0"]').exists()).toBe(false)
    expect(mountCfg({ consumers: [null, [], 'x', C('a')] }).findAll('[data-testid^="hems-consumer-position-"]')).toHaveLength(1)
  })

  it('edits only the addressed consumer and leaves the others untouched', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a'), C('b', { name: 'Keep' })]) })
    await setValue(w, 'hems-consumer-name-0', 'Changed', 'input')
    expect(lastConsumers(w)).toEqual([{ ...C('a'), name: 'Changed' }, C('b', { name: 'Keep' })])
  })

  it('stores a value typed into an optional override field', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a')]) })
    await setValue(w, 'hems-consumer-on_delay_s-0', '15')
    expect(lastConsumers(w)[0].on_delay_s).toBe(15)
    await setValue(w, 'hems-consumer-off_delay_s-0', '-4')
    expect(lastConsumers(w)[0].off_delay_s).toBe(0)
  })

  it('edits name, active flag and control type', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a')]) })
    await setValue(w, 'hems-consumer-name-0', 'Boiler', 'input')
    expect(lastConsumers(w)[0].name).toBe('Boiler')
    await w.find('[data-testid="hems-consumer-active-0"]').setValue(false)
    expect(lastConsumers(w)[0].active).toBe(false)
    await setValue(w, 'hems-consumer-mode-0', 'onoff')
    expect(lastConsumers(w)[0].mode).toBe('onoff')
  })

  it('treats a consumer without an explicit active flag as active', () => {
    const w = mountCfg({ consumers: JSON.stringify([{ id: 'a', mode: 'percent' }]) })
    expect(w.find('[data-testid="hems-consumer-active-0"]').element.checked).toBe(true)
  })
})

describe('HemsSurplusConfig — fields per control type', () => {
  const has = (w, key, i = 0) => w.find(`[data-testid="hems-consumer-${key}-${i}"]`).exists()

  it('percent shows setpoint limits, step and the behaviour below the minimum', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a')]) })
    for (const key of ['power_source', 'power_w', 'min_power_w', 'min_setpoint', 'max_setpoint', 'step', 'below_min', 'min_runtime_s', 'min_off_s', 'on_delay_s', 'off_delay_s']) {
      expect(has(w, key), key).toBe(true)
    }
    expect(has(w, 'min_surplus_w')).toBe(false)
    expect(w.text()).toContain('Maximalleistung (W)')
  })

  it('on/off shows thresholds and a rated power', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { mode: 'onoff' })]) })
    for (const key of ['power_w', 'on_threshold_w', 'off_threshold_w', 'min_runtime_s', 'min_off_s']) {
      expect(has(w, key), key).toBe(true)
    }
    expect(has(w, 'step')).toBe(false)
    expect(w.text()).toContain('Nennleistung (W)')
  })

  it('trigger shows its own settings and labels the delay as the hold time', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { mode: 'trigger' })]) })
    for (const key of ['min_surplus_w', 'on_delay_s', 'pulse_s', 'lockout_s', 'rearm', 'reserve_w']) {
      expect(has(w, key), key).toBe(true)
    }
    expect(has(w, 'power_w')).toBe(false)
    expect(has(w, 'min_runtime_s')).toBe(false)
    expect(w.text()).toContain('Mindestüberschuss muss bestehen für')
  })

  it('falls back to the percent fields for an unknown control type', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { mode: 'weird' })]) })
    expect(has(w, 'step')).toBe(true)
  })

  it('hides the fixed power when it comes from an input', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { power_source: 'input' })]) })
    expect(has(w, 'power_w')).toBe(false)
    expect(w.find('[data-testid="hems-consumer-power_source-0"]').element.value).toBe('input')
  })

  it('stores numbers within their bounds and enum selections', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a')]) })
    await setValue(w, 'hems-consumer-power_w-0', '11000')
    expect(lastConsumers(w)[0].power_w).toBe(11000)
    await setValue(w, 'hems-consumer-max_setpoint-0', '150')
    expect(lastConsumers(w)[0].max_setpoint).toBe(100)
    await setValue(w, 'hems-consumer-step-0', '0')
    expect(lastConsumers(w)[0].step).toBe(0.1)
    await setValue(w, 'hems-consumer-below_min-0', 'hold_min')
    expect(lastConsumers(w)[0].below_min).toBe('hold_min')
    await setValue(w, 'hems-consumer-power_source-0', 'input')
    expect(lastConsumers(w)[0].power_source).toBe('input')
  })

  it('an emptied required number falls back to its default, an emptied optional one to "use the global value"', async () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { on_delay_s: 30, step: 5 })]) })
    await setValue(w, 'hems-consumer-on_delay_s-0', '')
    expect(lastConsumers(w)[0].on_delay_s).toBeNull()
    await setValue(w, 'hems-consumer-step-0', '')
    expect(lastConsumers(w)[0].step).toBe(1)
    expect(w.find('[data-testid="hems-consumer-on_delay_s-0"]').element.placeholder).toBe('leer = globaler Wert')
  })

  it('shows a stored optional value and an empty box otherwise', () => {
    const w = mountCfg({ consumers: JSON.stringify([C('a', { on_delay_s: 15 })]) })
    expect(w.find('[data-testid="hems-consumer-on_delay_s-0"]').element.value).toBe('15')
    expect(w.find('[data-testid="hems-consumer-off_delay_s-0"]').element.value).toBe('')
  })

  it('shows the age-check hint only where it applies', () => {
    const w = mountCfg({})
    expect(w.text()).toContain('0 = keine Alterungsprüfung')
  })
})
