// @vitest-environment jsdom
/**
 * Issue #1008 — the switching value input must match the target object type.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { datapoints } from '@/api/client'
import ZeitschaltuhrBindingModal from './ZeitschaltuhrBindingModal.vue'

const apiMocks = vi.hoisted(() => ({
  listBindings: vi.fn(),
  updateBinding: vi.fn(),
  get: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  datapoints: apiMocks,
  adapters: { zsuHolidays: vi.fn().mockResolvedValue([]) },
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

const listBindingsMock = vi.mocked(datapoints.listBindings)
const updateBindingMock = vi.mocked(datapoints.updateBinding)
const getMock = vi.mocked(datapoints.get)

let wrapper: VueWrapper | null = null

function timerBinding(value: string, timerType = 'daily') {
  return {
    id: 'b-1',
    datapoint_id: 'dp-1',
    adapter_type: 'ZEITSCHALTUHR',
    adapter_instance_id: 'zsu-instance',
    instance_name: 'ZSU',
    direction: 'SOURCE',
    config: { timer_type: timerType, meta_type: 'holiday_today', time_ref: 'absolute', hour: 8, minute: 0, value },
    enabled: true,
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
  }
}

function datapoint(dataType: string, unit: string | null = null) {
  return {
    id: 'dp-1',
    name: 'Fensterposition',
    data_type: dataType,
    unit,
    tags: [],
    mqtt_topic: 'dp/dp-1/value',
    mqtt_alias: null,
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
  }
}

async function mountModal(dataType: string, value: string, unit: string | null = null, timerType = 'daily') {
  listBindingsMock.mockResolvedValue([timerBinding(value, timerType)] as never)
  getMock.mockResolvedValue(datapoint(dataType, unit) as never)
  wrapper = mount(ZeitschaltuhrBindingModal, {
    props: { datapointId: 'dp-1', instanceId: 'zsu-instance', bindingId: 'b-1' },
    global: {
      mocks: { $t: (key: string) => key },
      stubs: { Teleport: true },
    },
  })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('ZeitschaltuhrBindingModal — typed switching value', () => {
  it.each([
    ['BOOLEAN', '1', 'zst-value-boolean'],
    ['INTEGER', '50', 'zst-value-number'],
    ['FLOAT', '21.5', 'zst-value-number'],
    ['DATE', '2026-12-24', 'zst-value-date'],
    ['TIME', '08:00:00', 'zst-value-time'],
    ['DATETIME', '2026-12-24T08:00:00', 'zst-value-datetime'],
    ['STRING', 'on', 'zst-value-text'],
    ['UNKNOWN', '1', 'zst-value-text'],
  ])('renders the %s input', async (dataType, value, testid) => {
    const w = await mountModal(dataType, value)
    expect(w.find(`[data-testid="${testid}"]`).exists()).toBe(true)
    expect(w.find('[data-testid="zst-value-error"]').exists()).toBe(false)
  })

  // Codex review on PR #1155: a native `time`/`datetime-local` control cannot hold a
  // UTC offset (or fractional seconds, or a bare date used as a datetime). The browser
  // sanitizes it to an empty field, so the stored value would look unset and be wiped
  // on the next save — those legacy literals get a text field instead.
  it.each([
    ['TIME', '08:00:00+02:00', 'zst-value-time'],
    ['TIME', '08:00:00Z', 'zst-value-time'],
    ['DATETIME', '2026-12-24T08:00:00+02:00', 'zst-value-datetime'],
    ['DATETIME', '2026-12-24', 'zst-value-datetime'],
  ])('falls back to a text field for the %s value %s', async (dataType, value, pickerTestid) => {
    const w = await mountModal(dataType, value)
    expect(w.find(`[data-testid="${pickerTestid}"]`).exists()).toBe(false)
    const text = w.find('[data-testid="zst-value-text"]')
    expect(text.exists()).toBe(true)
    expect((text.element as HTMLInputElement).value).toBe(value)
  })

  it.each([
    ['08:00:00+24:00', 'TIME'],
    ['08:00:00+23:60', 'TIME'],
  ])('blocks saving %s, an offset the API rejects', async (value, dataType) => {
    const w = await mountModal(dataType, value)
    expect(w.find('[data-testid="zst-value-error"]').exists()).toBe(true)
    expect(w.get('[data-testid="zst-save-btn"]').attributes('disabled')).toBeDefined()
  })

  it('shows the object type and unit next to the label', async () => {
    const w = await mountModal('FLOAT', '50', '%')
    expect(w.get('[data-testid="zst-value-type"]').text()).toBe('FLOAT · %')
  })

  it('shows only the object type when the datapoint has no unit', async () => {
    const w = await mountModal('FLOAT', '50')
    expect(w.get('[data-testid="zst-value-type"]').text()).toBe('FLOAT')
  })

  it('falls back to UNKNOWN when the datapoint cannot be loaded', async () => {
    listBindingsMock.mockResolvedValue([timerBinding('1')] as never)
    getMock.mockRejectedValue(new Error('boom'))
    wrapper = mount(ZeitschaltuhrBindingModal, {
      props: { datapointId: 'dp-1', instanceId: 'zsu-instance', bindingId: 'b-1' },
      global: { mocks: { $t: (key: string) => key }, stubs: { Teleport: true } },
    })
    await flushPromises()
    expect(wrapper.get('[data-testid="zst-value-type"]').text()).toBe('UNKNOWN')
    expect(wrapper.find('[data-testid="zst-value-text"]').exists()).toBe(true)
  })

  it('keeps the switching value a string when a number is typed', async () => {
    const w = await mountModal('FLOAT', '50')
    await w.get('[data-testid="zst-value-number"]').setValue('0')
    updateBindingMock.mockResolvedValue({} as never)

    await w.get('[data-testid="zst-save-btn"]').trigger('click')
    await flushPromises()

    expect(updateBindingMock).toHaveBeenCalledTimes(1)
    expect(updateBindingMock).toHaveBeenCalledWith('dp-1', 'b-1', expect.objectContaining({
      config: expect.objectContaining({ value: '0' }),
    }))
  })

  it('repairs a legacy non-boolean value on a BOOLEAN object', async () => {
    // The select only offers Ein/Aus, so "50" could never be cleared by the user.
    const w = await mountModal('BOOLEAN', '50')
    expect(w.find('[data-testid="zst-value-error"]').exists()).toBe(false)
    expect(w.get('[data-testid="zst-save-btn"]').attributes('disabled')).toBeUndefined()

    updateBindingMock.mockResolvedValue({} as never)
    await w.get('[data-testid="zst-save-btn"]').trigger('click')
    await flushPromises()
    expect(updateBindingMock).toHaveBeenCalledWith('dp-1', 'b-1', expect.objectContaining({
      config: expect.objectContaining({ value: 'false' }),
    }))
  })

  it('writes true/false when the boolean select changes', async () => {
    const w = await mountModal('BOOLEAN', '1')
    await w.get('[data-testid="zst-value-boolean"]').setValue('false')
    updateBindingMock.mockResolvedValue({} as never)

    await w.get('[data-testid="zst-save-btn"]').trigger('click')
    await flushPromises()

    expect(updateBindingMock).toHaveBeenCalledWith('dp-1', 'b-1', expect.objectContaining({
      config: expect.objectContaining({ value: 'false' }),
    }))
  })

  it('shows an inline error and disables saving for an incompatible value', async () => {
    // DATE keeps the stored value — only BOOLEAN is auto-repaired, because its
    // select cannot represent an arbitrary literal.
    const w = await mountModal('DATE', '1')
    expect(w.get('[data-testid="zst-value-error"]').text()).toBe('zst.switchValueErrorDate')

    const saveBtn = w.get('[data-testid="zst-save-btn"]')
    expect(saveBtn.attributes('disabled')).toBeDefined()

    await saveBtn.trigger('click')
    await flushPromises()
    expect(updateBindingMock).not.toHaveBeenCalled()
  })

  it('does not block saving a meta binding whose value does not fit', async () => {
    const w = await mountModal('DATE', '1', null, 'meta')
    updateBindingMock.mockResolvedValue({} as never)

    const saveBtn = w.get('[data-testid="zst-save-btn"]')
    expect(saveBtn.attributes('disabled')).toBeUndefined()

    await saveBtn.trigger('click')
    await flushPromises()
    expect(updateBindingMock).toHaveBeenCalledTimes(1)
  })
})
